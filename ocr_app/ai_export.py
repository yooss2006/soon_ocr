"""Export page-addressable text without rerunning OCR or changing its wording."""
import contextlib
import json
import re
import tempfile
from pathlib import Path
from pypdf import PdfReader
from .storage import UserError, NeedsOCR, Paused, job_lock, sha256_file, publish_file


def clean_text(text):
    # Only an explicit PDF soft-hyphen marker authorizes joining across a line.
    return re.sub('\u00ad[ \t]*\r?\n', '', text).replace('\x00', '').strip()


def cached_text(data):
    lines = data['ocr']['lines']
    joins = {item['line_index'] for item in data.get('wraps', [])
             if item.get('action') == 'join' and item.get('next_line_index') == item['line_index'] + 1}
    return clean_text(''.join(line['text'].strip() + ('' if i in joins else '\n')
                              for i, line in enumerate(lines)))


def export_markdown(source, output, stop, emit=None, job=None, split_pages=0, overwrite=False,
                    inspect_only=False, verify_no_text=None):
    emit = emit or (lambda event: None)
    def check_stop():
        if stop.is_set(): raise Paused()
    if split_pages < 0: raise UserError('분할 쪽 수는 0 이상이어야 합니다.')
    destination = Path(output).expanduser().resolve() if output else None
    if not inspect_only and destination is None: raise UserError("저장 위치를 선택하세요.")
    job = Path(job).resolve() if job else None
    if job and not (job/'job.json').is_file(): raise UserError('작업 기록을 찾을 수 없습니다.')
    with job_lock(job) if job else contextlib.nullcontext():
        state = json.loads((job/'job.json').read_text()) if job else None
        source = job/'source.pdf' if job else Path(source).expanduser().resolve()
        if not source.is_file(): raise UserError('읽을 PDF 파일을 찾을 수 없습니다.')
        if destination == source or (job and destination and (destination.is_relative_to(job) or destination == Path(state['source_path']).resolve())):
            raise UserError('원본 또는 작업 기록을 덮어쓸 수 없습니다.')
        if not inspect_only and not split_pages and destination.suffix.lower() != '.md':
            raise UserError('저장 파일의 확장자는 .md여야 합니다.')
        if destination and destination.exists() and (split_pages or not overwrite):
            raise UserError('같은 이름이 있습니다. 새 저장 이름을 선택하세요.')
        check_stop()
        emit({'event':'status','message':'AI용 텍스트를 준비하고 있습니다.'})
        if state and sha256_file(source) != state['source_sha256']:
            raise UserError('보관된 원본이 변경됐습니다. PDF를 다시 선택하세요.')
        with source.open('rb') as stream:
            reader = PdfReader(stream)
            if reader.is_encrypted: raise UserError('암호를 해제한 PDF 사본을 선택하세요.')
            count = len(reader.pages)
            if not count: raise UserError('페이지가 없는 PDF입니다.')
            if state and count != state['page_count']: raise UserError('작업 기록의 쪽 수가 원본과 다릅니다.')
            if state:
                incomplete = [n for n in range(1, count+1) if state.get('pages', {}).get(str(n), {}).get('status')
                              not in ('complete', 'no_text', 'existing_text')]
                if incomplete:
                    raise NeedsOCR(f'OCR 미완료 {len(incomplete)}쪽이 있습니다. 전체 OCR을 완료한 뒤 AI용으로 저장하세요.')
            name = state['source_name'] if state else source.name
            header = (f'# {name}\n\n'
                      '> AI용 텍스트입니다. 내용은 요약하거나 교정하지 않았습니다.\n'
                      '> 쪽 번호는 PDF의 첫 페이지를 1쪽으로 센 번호입니다.\n'
                      '> 표·수식은 추출된 글자로만 제공됩니다. 칸 구조와 읽는 순서는 원본과 확인하세요.\n'
                      '> 이미지와 그림은 포함하지 않습니다. OCR 오탈자가 있을 수 있습니다.\n\n')
            pages, missing, review_pages = [], [], []
            for number, page in enumerate(reader.pages, 1):
                check_stop()
                text, notes = '', []
                record = state.get('pages', {}).get(str(number), {}) if state else {}
                analysis = job/'pages'/f'{number:05}'/'analysis.json' if job else None
                used_cache = False
                if state:
                    if not analysis.is_file() or sha256_file(analysis) != record.get('analysis_sha256'):
                        raise UserError(f'{number}쪽 OCR 기록을 확인할 수 없습니다. OCR을 다시 완료하세요.')
                    data = json.loads(analysis.read_text())
                    if record['status'] in ('complete', 'no_text'):
                        if 'ocr' not in data: raise UserError(f'{number}쪽 OCR 기록이 손상됐습니다.')
                        text = cached_text(data); used_cache = True
                        if data.get('alerts'):
                            notes.append('확인 필요: OCR 검토 항목이 있습니다. 앱의 검토 보고서와 원본을 확인하세요.')
                if not used_cache:
                    try: text = clean_text(page.extract_text() or '')
                    except Exception: raise UserError(f'{number}쪽 텍스트를 읽지 못했습니다. OCR을 다시 완료하세요.')
                    if not text:
                        if state:
                            raise UserError(f'{number}쪽 텍스트가 기록과 다릅니다. OCR을 다시 완료하세요.')
                        # Textless pages with painted content require a local recognition check.
                        contents = page.get_contents()
                        paints = {b'Do', b'INLINE IMAGE', b'S', b's', b'f', b'F', b'f*', b'B', b'B*', b'b', b'b*', b'sh', b'Tj', b'TJ', b"'", b'"'}
                        painted = contents is not None and any(op in paints for _, op in contents.operations)
                        if painted and (verify_no_text is None or not verify_no_text(source, number, page)):
                            raise NeedsOCR(f'PDF {number}쪽에 OCR이 필요한 내용이 있습니다. 전체 OCR을 완료한 뒤 AI용으로 저장하세요.')
                if not text:
                    missing.append(number)
                    notes.append('텍스트 없음: 빈 페이지 또는 글자를 인식하지 못한 그림입니다. 원본을 확인하세요.')
                if notes: review_pages.append(number)
                block = f'## PDF {number}쪽\n\n> 이미지·그림 제외: 원본 PDF {number}쪽을 확인하세요.\n\n'
                if notes: block += '\n'.join('> '+note for note in notes)+'\n\n'
                pages.append(block + text + '\n\n')
                emit({'event':'progress','done':number,'target':count,'message':f'{number}/{count}쪽 텍스트 추출'})
        if len(missing) == count:
            raise NeedsOCR('추출할 텍스트가 없습니다. 먼저 OCR 변환을 완료한 뒤 AI용으로 저장하세요.')
        check_stop()
        if inspect_only:
            result = {'event':'finished','status':'ai_ready','page_count':count,
                      'message':'OCR 확인 완료. AI용으로 저장할 수 있습니다.'}
            emit(result)
            return result
        destination.parent.mkdir(parents=True, exist_ok=True)
        size = split_pages or count
        paths, byte_count = [], 0
        with tempfile.TemporaryDirectory(prefix='.ai-export-', dir=destination.parent) as temporary:
            staging = Path(temporary)
            for start in range(0, count, size):
                check_stop()
                end = min(start + size, count)
                filename = f'pages-{start+1:04}-{end:04}.md' if split_pages else destination.name
                content = header + f'> 포함 범위: PDF {start+1}–{end}쪽 / 전체 {count}쪽\n\n' + ''.join(pages[start:end])
                path = staging/filename
                byte_count += path.write_bytes(content.encode('utf-8'))
                paths.append(path)
            check_stop()
            if split_pages:
                try: destination.mkdir()
                except FileExistsError: raise UserError('같은 이름이 있습니다. 새 저장 이름을 선택하세요.')
                published = []
                try:
                    for path in paths:
                        target = destination/path.name
                        publish_file(path, target)
                        published.append(target)
                except Exception:
                    for target in published: target.unlink()
                    destination.rmdir()
                    raise
            else:
                publish_file(paths[0], destination, overwrite)
        result = {'event':'finished','status':'ai_complete','path':str(destination),'page_count':count,
                  'file_count':len(paths),'bytes':byte_count,'missing_pages':missing,'review_pages':review_pages,
                  'message':f'AI용 저장 완료: {len(paths)}개 파일 · 텍스트 없는 쪽 {len(missing)}개'}
        emit(result)
        return result
