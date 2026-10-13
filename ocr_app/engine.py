"""Content-addressed page checkpoints, pause/resume and atomic PDF export."""
import contextlib
import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from pypdf import PdfReader
from .analysis import Analyzer
from .pdf_layer import display_geometry, create_overlay, export_pdf
from .reports import page_report, index_report
from .storage import UserError, Paused, atomic_json, atomic_bytes, job_lock, sha256_file, publish_file

TERMINAL = {'complete','no_text','existing_text','failed'}
SUCCESS = {'complete','no_text','existing_text'}
SCHEMA = 1

def sample_pages(count):
    return sorted({max(1,min(count,round(count*f))) for f in (.1,.3,.5,.7,.9)})

def read_pdf(path):
    with Path(path).open('rb') as stream:
        reader = PdfReader(stream)
        if reader.is_encrypted: raise UserError('암호가 설정된 PDF는 지원하지 않습니다. 암호를 해제한 사본을 선택하세요.')
        count = len(reader.pages)
        if count == 0: raise UserError('페이지가 없는 PDF입니다.')
        return count

def default_job_root():
    return Path.home()/'Library/Application Support/OCR Helper/jobs'

class Converter:
    def __init__(self, resources, stop, emit=None, analyzer_factory=Analyzer):
        self.resources, self.stop = resources, stop
        self.emit_callback = emit or (lambda event:None)
        self.analyzer_factory = analyzer_factory
        self.job_dir = None
        self.state = None
        self.done = self.target = 0

    def emit(self,event,**values):
        self.emit_callback(dict(event=event,done=self.done,target=self.target,**values))

    def check_stop(self):
        if self.stop.is_set(): raise Paused()

    def save(self):
        self.state['updated_at'] = time.time()
        atomic_json(self.job_dir/'job.json', self.state)

    def prepare(self, source, job_root):
        source = Path(source).expanduser().resolve()
        if not source.is_file() or source.suffix.lower()!='.pdf': raise UserError('읽을 수 있는 PDF 파일을 선택하세요.')
        self.emit('status',phase='prepare',message='파일을 확인하고 있습니다.')
        digest = hashlib.sha256()
        with source.open('rb') as stream:
            while chunk := stream.read(8*1024*1024):
                self.check_stop();digest.update(chunk)
        source_hash = digest.hexdigest()
        directory = Path(job_root).expanduser().resolve()/f'{source_hash[:20]}-{self.resources.fingerprint[:12]}'
        with job_lock(directory):
            manifest = directory/'job.json'
            if manifest.exists():
                existing = json.loads(manifest.read_text())
                if existing.get('source_sha256')!=source_hash or existing.get('recipe')!=self.resources.fingerprint:
                    raise UserError('작업 기록이 원본과 일치하지 않습니다.')
            else:
                pending = directory/'snapshot.pending'
                copied_hash = hashlib.sha256()
                try:
                    with source.open('rb') as incoming, pending.open('wb') as outgoing:
                        while chunk := incoming.read(8*1024*1024):
                            self.check_stop();outgoing.write(chunk);copied_hash.update(chunk)
                        outgoing.flush();os.fsync(outgoing.fileno())
                    if copied_hash.hexdigest()!=source_hash: raise UserError('원본 파일이 처리 준비 중 변경됐습니다. 다시 선택하세요.')
                    count = read_pdf(pending)
                    os.replace(pending,directory/'source.pdf')
                    data = {'schema':SCHEMA,'id':directory.name,'source_name':source.name,'source_path':str(source),
                            'source_sha256':source_hash,'recipe':self.resources.fingerprint,'page_count':count,
                            'sample_pages':sample_pages(count),'sample_ready':False,'sample_approved':False,
                            'status':'ready','scope':'sample','pages':{},'created_at':time.time(),'updated_at':time.time()}
                    atomic_json(manifest,data)
                finally:
                    with contextlib.suppress(FileNotFoundError): pending.unlink()
        return directory

    def cache_valid(self, number, retry_failed):
        record = self.state['pages'].get(str(number),{})
        if record.get('status')=='failed': return not retry_failed
        if record.get('status') not in SUCCESS: return False
        directory = self.job_dir/'pages'/f'{number:05}'
        data = directory/'analysis.json'
        if not data.is_file() or sha256_file(data)!=record.get('analysis_sha256'): return False
        if not (directory/'review.html').is_file(): return False
        if record.get('overlay_sha256'):
            overlay = directory/'overlay.pdf'
            if not overlay.is_file() or sha256_file(overlay)!=record['overlay_sha256']: return False
        return True

    def page(self, number, analyzer):
        target = self.job_dir/'pages'/f'{number:05}'
        target.mkdir(parents=True,exist_ok=True)
        snapshot = self.job_dir/'source.pdf'
        with snapshot.open('rb') as stream:
            page = PdfReader(stream).pages[number-1]
            existing = bool(page.extract_text().strip())
            geometry = display_geometry(page)
        if existing:
            data = {'status':'existing_text','alerts':[],'note':'Existing text preserved without adding another text layer.'}
            atomic_json(target/'analysis.json',data)
            page_report(target,number,[])
            return {'status':'existing_text','analysis_sha256':sha256_file(target/'analysis.json'),'review_count':0}
        work_root = self.job_dir/'work';work_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f'page-{number}-',dir=work_root) as temporary:
            data = analyzer.analyze(snapshot,number,geometry,Path(temporary))
            self.check_stop()
            joins = {d['line_index'] for d in data['wraps'] if d['action']=='join'}
            overlay = create_overlay(geometry[0],geometry[1],data['ocr']['lines'],joins)
            atomic_bytes(target/'overlay.pdf',overlay)
            page_report(target,number,data['alerts'],data['image_path'],data['image_size'])
            data.pop('image_path',None)
            atomic_json(target/'analysis.json',data)
        status = 'complete' if data['ocr']['lines'] else 'no_text'
        return {'status':status,'analysis_sha256':sha256_file(target/'analysis.json'),
                'overlay_sha256':sha256_file(target/'overlay.pdf'),'review_count':len(data['alerts']),
                'suspected_count':sum(a.get('priority')!='normal' for a in data['alerts']),
                'joined_boundaries':len(joins)}

    def convert(self, job_dir, scope='sample', output=None, approved=False, overwrite=False,
                retry_failed=False, stop_after=None):
        self.resources.check()
        self.job_dir = Path(job_dir).resolve()
        with job_lock(self.job_dir):
            path = self.job_dir/'job.json'
            if not path.is_file(): raise UserError('작업 기록을 찾을 수 없습니다.')
            self.state = json.loads(path.read_text())
            if self.state.get('schema')!=SCHEMA or self.state.get('recipe')!=self.resources.fingerprint:
                raise UserError('현재 앱과 다른 버전의 작업입니다. 원본 PDF를 새 작업으로 선택하세요.')
            snapshot = self.job_dir/'source.pdf'
            if sha256_file(snapshot)!=self.state['source_sha256']: raise UserError('보관된 원본 파일이 변경됐습니다. 원본을 다시 선택하세요.')
            if scope=='full':
                if not output: raise UserError('저장할 PDF 위치를 선택하세요.')
                if approved: self.state['sample_approved'] = True
                destination = Path(output).expanduser().resolve()
                original = Path(self.state['source_path']).resolve()
                if destination==original or destination.is_relative_to(self.job_dir): raise UserError('원본 또는 작업 기록을 덮어쓸 수 없습니다. 다른 저장 위치를 선택하세요.')
                if destination.suffix.lower()!='.pdf': raise UserError('저장 파일의 확장자는 .pdf여야 합니다.')
                destination.parent.mkdir(parents=True,exist_ok=True)
                if destination.exists() and not overwrite:
                    last = self.state.get('export',{})
                    if last.get('path')==str(destination) and last.get('sha256')==sha256_file(destination): overwrite=True
                    else: raise UserError('같은 이름의 파일이 있습니다. 다른 저장 이름을 선택하세요.')
                selected = list(range(1,self.state['page_count']+1))
                self.state['destination'] = str(destination)
            else:
                selected = self.state['sample_pages'];destination = self.job_dir/'sample.pdf';overwrite=True
            self.done,self.target = 0,len(selected)
            self.state.update(status='running',scope=scope)
            self.save()
            self.emit('job',job_dir=str(self.job_dir),source_name=self.state['source_name'],
                      source_path=self.state['source_path'],page_count=self.state['page_count'],sample_pages=self.state['sample_pages'],scope=scope)
            analyzer = self.analyzer_factory(self.resources,self.emit,self.stop)
            newly_processed = 0
            try:
                for number in selected:
                    self.check_stop()
                    cached = self.cache_valid(number,retry_failed)
                    if not cached:
                        previous_attempts = self.state['pages'].get(str(number),{}).get('attempts',0)
                        record = None
                        for attempt in range(2):
                            self.check_stop()
                            self.state['pages'][str(number)] = {'status':'processing','attempts':previous_attempts+attempt+1}
                            self.save()
                            try:
                                record = self.page(number,analyzer)
                                record['attempts'] = previous_attempts+attempt+1
                                break
                            except Paused: raise
                            except Exception as exc:
                                with (self.job_dir/'errors.log').open('a') as log: log.write(f'Page {number}, attempt {attempt+1}: {type(exc).__name__}: {exc}\n')
                                if attempt==0:
                                    self.emit('status',phase='retry',page=number,message=f'{number}쪽 다시 시도 중')
                                else:
                                    alert={'kind':'page_failed','priority':'high','reason':'처리를 완료하지 못했습니다. 최종 PDF에는 원본 페이지를 넣습니다.','detail':str(exc)}
                                    target=self.job_dir/'pages'/f'{number:05}';page_report(target,number,[alert])
                                    record={'status':'failed','attempts':previous_attempts+2,'error':str(exc),'review_count':1}
                        self.state['pages'][str(number)] = record
                        self.save();newly_processed += 1
                    self.done += 1
                    self.emit('page',page=number,status=self.state['pages'][str(number)]['status'],cached=cached,message=f'{self.done}/{self.target}쪽 처리')
                    if stop_after is not None and newly_processed>=stop_after: raise Paused()
                self.check_stop()
                self.emit('status',phase='export',message='PDF를 저장하고 있습니다.')
                overlays={n:self.job_dir/'pages'/f'{n:05}'/'overlay.pdf' for n in selected
                          if self.state['pages'][str(n)].get('overlay_sha256') and self.state['pages'][str(n)]['status'] in SUCCESS}
                fd,temporary=tempfile.mkstemp(prefix='.'+destination.stem+'-',suffix='.pdf',dir=destination.parent);os.close(fd)
                try:
                    export_pdf(snapshot,selected,overlays,temporary)
                    self.check_stop()
                    publish_file(temporary,destination,overwrite)
                finally:
                    with contextlib.suppress(FileNotFoundError):Path(temporary).unlink()
                report=index_report(self.job_dir,self.state,selected)
                failed=[n for n in selected if self.state['pages'][str(n)]['status']=='failed']
                review_count=sum(self.state['pages'][str(n)].get('review_count',0) for n in selected)
                if scope=='sample':
                    self.state['sample_ready']=True;self.state['sample_path']=str(destination);status='sample_ready'
                else:
                    self.state['export']={'path':str(destination),'sha256':sha256_file(destination)}
                    status='complete_with_errors' if failed else 'complete'
                self.state.update(status=status,report_path=str(report),failed_pages=failed,review_count=review_count)
                self.save()
                result={'status':status,'path':str(destination),'report_path':str(report),'job_dir':str(self.job_dir),
                        'failed_pages':failed,'review_count':review_count,'page_count':len(selected)}
                self.emit('finished',**result)
                return result
            except Paused:
                self.state['status']='paused';self.save()
                self.emit('finished',status='paused',job_dir=str(self.job_dir),message='중단했습니다. 완료한 페이지는 저장됐습니다.')
                return {'status':'paused','job_dir':str(self.job_dir)}
            except Exception:
                self.state['status']='error';self.save();raise
