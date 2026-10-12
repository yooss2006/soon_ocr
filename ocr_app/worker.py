"""JSON-lines process interface used by the native Mac app."""
import argparse
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

if not getattr(sys,'frozen',False):
    root=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root));sys.path.insert(0,str(root/'scripts/runtime'))
from ocr_app.engine import Converter,default_job_root
from ocr_app.resources import Resources
from ocr_app.storage import UserError,Paused

def main():
    parser=argparse.ArgumentParser(description='Local resumable PDF OCR')
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--input',type=Path);source.add_argument('--job',type=Path)
    parser.add_argument('--scope',choices=['sample','full'],default='sample')
    parser.add_argument('--output',type=Path);parser.add_argument('--job-root',type=Path,default=default_job_root())
    parser.add_argument('--sample-approved',action='store_true');parser.add_argument('--overwrite',action='store_true')
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
        resources=Resources(args.resources);resources.check()
        converter=Converter(resources,stop,emit)
        output=args.output.expanduser().resolve() if args.output else None
        job=args.job.resolve() if args.job else converter.prepare(args.input,args.job_root)
        os.chdir(job)
        converter.convert(job,args.scope,output,args.sample_approved,args.overwrite,args.retry_failed,args.stop_after)
        return 0
    except Paused:
        emit({'event':'finished','status':'paused','message':'파일 준비를 중단했습니다. 다시 시작할 수 있습니다.'});return 0
    except Exception as exc:
        message=str(exc) if isinstance(exc,UserError) else '처리 중 문제가 발생했습니다: '+(str(exc) or type(exc).__name__)
        emit({'event':'error','message':message});return 2

if __name__=='__main__':raise SystemExit(main())
