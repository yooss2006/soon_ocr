import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image
from pypdf import PdfReader,PdfWriter
from pypdf.generic import RectangleObject
from reportlab.pdfgen import canvas
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ocr_app.engine import Converter
from ocr_app.pdf_layer import display_geometry,create_overlay,export_pdf
from ocr_app.storage import UserError,job_lock

class FakeAnalyzer:
    calls=[]
    failures={}
    def __init__(self,*_):pass
    def analyze(self,snapshot,number,geometry,work):
        self.calls.append(number)
        remaining=self.failures.get(number,0)
        if remaining:
            self.failures[number]=remaining-1
            raise RuntimeError('temporary test failure')
        image=work/'page.png';Image.new('RGB',(300,400),'white').save(image)
        line={'text':'검색 테스트','box':[.1,.5,.6,.04], 'chars':[]}
        for i,ch in enumerate(line['text']):line['chars'].append({'text':ch,'box':[.1+i*.06,.5,.05,.04]})
        return {'ocr':{'lines':[line]},'documents':[],'wraps':[],'alerts':[],'regions':[],'formulas':[], 'image_path':str(image),'image_size':[300,400]}

class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.source=self.root/'source.pdf'
        image=self.root/'scan.png';Image.new('RGB',(300,400),'white').save(image)
        c=canvas.Canvas(str(self.source),pagesize=(300,400))
        for _ in range(3):c.drawImage(str(image),0,0,300,400);c.showPage()
        c.save()
        self.res=SimpleNamespace(fingerprint='test-recipe',check=lambda:None)
        self.stop=threading.Event();FakeAnalyzer.calls=[];FakeAnalyzer.failures={}
        self.converter=Converter(self.res,self.stop,analyzer_factory=FakeAnalyzer)
        self.job=self.converter.prepare(self.source,self.root/'jobs')
    def tearDown(self):self.tmp.cleanup()

    def test_pause_resume_reuses_completed_pages(self):
        self.assertEqual(self.converter.convert(self.job,stop_after=1)['status'],'paused')
        first=self.job/'pages/00001/overlay.pdf';stamp=first.stat().st_mtime_ns
        self.assertEqual(self.converter.convert(self.job)['status'],'sample_ready')
        self.assertEqual(FakeAnalyzer.calls,[1,2,3]);self.assertEqual(first.stat().st_mtime_ns,stamp)
        result=self.converter.convert(self.job,'full',self.root/'result.pdf',approved=True)
        self.assertEqual(result['status'],'complete');self.assertEqual(FakeAnalyzer.calls,[1,2,3])
        self.assertEqual(len(PdfReader(result['path']).pages),3)

    def test_failure_keeps_page_and_retry_only_failed(self):
        FakeAnalyzer.failures={2:2}
        self.converter.convert(self.job)
        result=self.converter.convert(self.job,'full',self.root/'result.pdf',approved=True)
        self.assertEqual(result['failed_pages'],[2]);self.assertEqual(len(PdfReader(result['path']).pages),3)
        self.assertEqual(PdfReader(result['path']).pages[1].extract_text(),'')
        before=list(FakeAnalyzer.calls)
        result=self.converter.convert(self.job,'full',self.root/'result.pdf',retry_failed=True)
        self.assertEqual(FakeAnalyzer.calls,before+[2]);self.assertEqual(result['failed_pages'],[])

    def test_corrupt_checkpoint_is_reprocessed(self):
        self.converter.convert(self.job)
        (self.job/'pages/00002/overlay.pdf').write_bytes(b'corrupt')
        self.converter.convert(self.job)
        self.assertEqual(FakeAnalyzer.calls,[1,2,3,2])

    def test_source_and_foreign_output_not_overwritten(self):
        self.converter.convert(self.job)
        with self.assertRaises(UserError):self.converter.convert(self.job,'full',self.source,approved=True,overwrite=True)
        output=self.root/'existing.pdf';output.write_bytes(b'keep me')
        with self.assertRaises(UserError):self.converter.convert(self.job,'full',output,approved=True)
        self.assertEqual(output.read_bytes(),b'keep me')

    def test_full_requires_sample_and_approval(self):
        with self.assertRaises(UserError):self.converter.convert(self.job,'full',self.root/'result.pdf',approved=True)
        self.converter.convert(self.job)
        with self.assertRaises(UserError):self.converter.convert(self.job,'full',self.root/'result.pdf')

    def test_snapshot_change_rejected(self):
        with (self.job/'source.pdf').open('ab') as f:f.write(b'changed')
        with self.assertRaises(UserError):self.converter.convert(self.job)

    def test_lock_blocks_second_converter(self):
        with job_lock(self.job):
            with self.assertRaises(UserError):self.converter.convert(self.job)

    def test_existing_text_is_not_duplicated(self):
        source=self.root/'text.pdf';c=canvas.Canvas(str(source));c.drawString(40,200,'already searchable');c.save()
        job=self.converter.prepare(source,self.root/'jobs')
        self.converter.convert(job)
        self.assertEqual(FakeAnalyzer.calls,[])
        record=json.loads((job/'job.json').read_text())
        self.assertEqual(record['pages']['1']['status'],'existing_text')

    def test_metadata_and_bookmarks_survive_export(self):
        r=PdfReader(self.source);w=PdfWriter();w.append(r);w.add_metadata({'/Title':'Original title'});w.add_outline_item('Chapter',1)
        source=self.root/'outline.pdf';w.write(source)
        output=self.root/'outline-output.pdf';export_pdf(source,[1,2,3],{},output)
        final=PdfReader(output)
        self.assertEqual(final.metadata.title,'Original title');self.assertEqual(final.outline[0].title,'Chapter')

    def test_shared_content_stream_is_not_ocrd_multiple_times(self):
        reader=PdfReader(self.source);writer=PdfWriter()
        for _ in range(3):writer.add_page(reader.pages[0])
        source=self.root/'shared-stream.pdf';writer.write(source)
        job=self.converter.prepare(source,self.root/'jobs')
        self.converter.convert(job)
        result=self.converter.convert(job,'full',self.root/'shared-result.pdf',approved=True)
        for page in PdfReader(result['path']).pages:
            self.assertEqual(page.extract_text().count('검색'),1)

if __name__=='__main__':unittest.main()
