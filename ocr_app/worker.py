"""JSON-lines process interface used by the native Mac app."""
import argparse
import json
import os
import signal
import sys
import threading
import tempfile
import time
from pathlib import Path

if not getattr(sys,'frozen',False):
    root=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root));sys.path.insert(0,str(root/'scripts/runtime'))
from ocr_app.engine import Converter,default_job_root
from ocr_app.ai_export import export_markdown
from ocr_app.resources import Resources
from ocr_app.storage import UserError,NeedsOCR,Paused

def main():
    parser=argparse.ArgumentParser(description='Local resumable PDF OCR')
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--input',type=Path);source.add_argument('--job',type=Path)
    parser.add_argument('--scope',choices=['sample','full','ai','ai-check'],default='sample')
    parser.add_argument('--output',type=Path);parser.add_argument('--job-root',type=Path,default=default_job_root())
    parser.add_argument('--sample-approved',action='store_true');parser.add_argument('--overwrite',action='store_true')
    parser.add_argument('--ai-format',choices=['markdown','package'],default='markdown')
    parser.add_argument('--split-pages',type=int,default=0,help='AI용 분할 쪽 수. 0이면 파일 하나로 저장')
    parser.add_argument('--retry-failed',action='store_true');parser.add_argument('--resources',type=Path)
    parser.add_argument('--parent-pid',type=int);parser.add_argument('--stop-after',type=int,help=argparse.SUPPRESS)
    args=parser.parse_args()
    stop=threading.Event()
    signal.signal(signal.SIGTERM,lambda *_:stop.set());signal.signal(signal.SIGINT,lambda *_:stop.set())
    if args.parent_pid:
        def watch_parent():
            while not stop.wait(1):
                try:os.kill(args.parent_pid,0)
                except ProcessLookupError:stop.set();return
        threading.Thread(target=watch_parent,daemon=True).start()
    def emit(event):print(json.dumps(event,ensure_ascii=False),flush=True)
    try:
        if args.scope in ('ai', 'ai-check'):
            if args.scope == 'ai' and not args.output: raise UserError('AI용 파일의 저장 위치를 선택하세요.')
            def verify_no_text(source, number, page):
                from ocr_app.analysis import Analyzer
                from ocr_app.pdf_layer import display_geometry
                resources=Resources(args.resources);resources.check()
                analyzer=Analyzer(resources,lambda *a, **kw: None,stop)
                emit({'event':'status','message':f'{number}쪽의 OCR 필요 여부를 확인하고 있습니다.'})
                with tempfile.TemporaryDirectory(prefix='ocr-check-') as directory:
                    work=Path(directory)
                    image=analyzer.render(source,number,300,work/'page',max(display_geometry(page)[:2]))
                    raw=work/'text.json'
                    analyzer.run([resources.text,image,raw])
                    return not any(line['text'].strip() for line in json.loads(raw.read_text())['lines'])
            if args.scope == 'ai' and args.ai_format == 'package':
                from ocr_app.ai_package import export_package
                export_package(args.input,args.output,stop,Resources(args.resources),args.job_root,emit,
                               job=args.job,split_pages=args.split_pages,verify_no_text=verify_no_text)
                return 0
            export_markdown(args.input,args.output,stop,emit,job=args.job,split_pages=args.split_pages,
                            overwrite=args.overwrite,inspect_only=args.scope=='ai-check',verify_no_text=verify_no_text)
            return 0
        resources=Resources(args.resources);resources.check()
        converter=Converter(resources,stop,emit)
        output=args.output.expanduser().resolve() if args.output else None
        job=args.job.resolve() if args.job else converter.prepare(args.input,args.job_root)
        os.chdir(job)
        converter.convert(job,args.scope,output,args.sample_approved,args.overwrite,args.retry_failed,args.stop_after)
        return 0
    except NeedsOCR as exc:
        if args.scope == 'ai-check':
            emit({'event':'finished','status':'ocr_required','message':str(exc)});return 0
        emit({'event':'error','message':str(exc)});return 2
    except Paused:
        emit({'event':'finished','status':'ai_paused' if args.scope in ('ai','ai-check') else 'paused','message':'저장을 중단했습니다. 다시 시작할 수 있습니다.'});return 0
    except Exception as exc:
        message=str(exc) if isinstance(exc,UserError) else '처리 중 문제가 발생했습니다: '+(str(exc) or type(exc).__name__)
        emit({'event':'error','message':message});return 2

if __name__=='__main__':raise SystemExit(main())
