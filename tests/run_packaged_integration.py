"""Exercise the shipped binary using the user's random 10-page test PDF."""
import hashlib,json,subprocess,time
from pathlib import Path
from pypdf import PdfReader
root=Path(__file__).resolve().parents[1]
folder=root/'output/app-test'
worker=root/'OCR 도우미.app/Contents/Resources/engine/ocr-worker'
source=folder/'ADsP-random-10-source.pdf';output=folder/'ADsP-random-10-OCR.pdf'

def run(name,args):
 path=folder/f'{name}.jsonl'
 with path.open('w') as log:
  result=subprocess.run([str(worker)]+[str(x) for x in args],stdout=log,check=True)
 events=[json.loads(line) for line in path.read_text().splitlines() if line.startswith('{')]
 assert events[-1]['event']=='finished',events[-1]
 print(name,events[-1]['status'],flush=True)
 return events

start=time.monotonic()
events=run('final-pause',['--input',source,'--job-root',root/'artifacts/jobs/final-app-jobs','--stop-after','1'])
assert events[-1]['status']=='paused'
job=Path(events[-1]['job_dir']);first=job/'pages/00001/overlay.pdf';first_stamp=first.stat().st_mtime_ns
sample=run('final-sample',['--job',job,'--scope','sample'])
assert sample[-1]['status']=='sample_ready';assert first.stat().st_mtime_ns==first_stamp
assert any(e.get('page')==1 and e.get('cached') is True for e in sample)
partial=run('final-full-pause',['--job',job,'--scope','full','--output',output,'--sample-approved','--stop-after','2'])
assert partial[-1]['status']=='paused'
state=json.loads((job/'job.json').read_text())
stamps={n:(job/'pages'/f'{int(n):05}'/'overlay.pdf').stat().st_mtime_ns for n,r in state['pages'].items() if r.get('overlay_sha256')}
final=run('final-result',['--job',job,'--scope','full','--output',output])
assert final[-1]['status']=='complete',final[-1]
for n,stamp in stamps.items():assert (job/'pages'/f'{int(n):05}'/'overlay.pdf').stat().st_mtime_ns==stamp
reader=PdfReader(output);original=PdfReader(source)
assert len(reader.pages)==len(original.pages)==10
assert all(p.extract_text().strip() for p in reader.pages)
assert len(reader.outline)==len(original.outline)==10
for i,dest in enumerate(reader.outline):assert reader.get_destination_page_number(dest)==i
keywords=[]
import re
for n in range(1,11):
 data=json.loads((job/'pages'/f'{n:05}'/'analysis.json').read_text())
 term=next(token for line in data['ocr']['lines'] for token in re.findall('[가-힣]{2,}',line['text']))
 keywords.append({'page':n,'term':term})
(folder/'search-keywords.json').write_text(json.dumps(keywords,ensure_ascii=False,indent=2))
proof={'job_dir':str(job),'output':str(output),'sample':sample[-1]['path'],'report':final[-1]['report_path'],
       'source_page_numbers':json.loads((folder/'selection.json').read_text())['source_page_numbers'],
       'sample_pause_resume':'PASS','full_pause_resume':'PASS','completed_pages_not_reprocessed':True,
       'output_pages':10,'failed_pages':final[-1]['failed_pages'],'outline_preserved':True,
       'review_items':final[-1]['review_count'],'elapsed_seconds':round(time.monotonic()-start,2)}
(folder/'packaged-test.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2))
print(json.dumps(proof,ensure_ascii=False),flush=True)
