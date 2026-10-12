"""Run the already-tested local recognizers; never substitute guessed math."""
import copy
import json
import subprocess
from pathlib import Path
import cv2
import onnxruntime as ort
from PIL import Image
from automatic_wraps import WrapAnalyzer
from detect_regions import formulas, apple_tables, code_tables, refine_candidates
from formula_crop import prepare_formula_crop
from formula_onnx import FormulaRecognizer
from quality_reviews import table_reviews, formula_reviews
from .storage import Paused

class Analyzer:
    def __init__(self, resources, emit, stop):
        self.resources, self.emit, self.stop = resources, emit, stop
        self.wrap = None
        self.detector = None
        self.recognizer = None

    def check_stop(self):
        if self.stop.is_set(): raise Paused()

    def run(self, command, timeout=180):
        self.check_stop()
        result = subprocess.run([str(x) for x in command], capture_output=True, text=True, timeout=timeout)
        self.check_stop()
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout or '로컬 도구 실행 실패')[-2000:])

    def render(self, snapshot, page, dpi, prefix, max_points):
        effective = min(dpi, max(36, int(6000*72/max_points)))
        self.run([self.resources.poppler,'-f',page,'-l',page,'-r',effective,'-cropbox','-singlefile','-png',snapshot,prefix])
        return Path(str(prefix)+'.png')

    def analyze(self, snapshot, number, geometry, work):
        self.emit('status', phase='render', page=number, message=f'{number}쪽 이미지 준비')
        image_path = self.render(snapshot, number, 300, work/'page', max(geometry[:2]))
        self.emit('status', phase='ocr', page=number, message=f'{number}쪽 글자 인식')
        raw_path, doc_path = work/'text.json', work/'document.json'
        self.run([self.resources.text, image_path, raw_path])
        ocr = json.loads(raw_path.read_text())
        alerts = []
        documents = []
        document_ok = False
        for attempt in range(2):
            try:
                self.run([self.resources.document, image_path, doc_path])
                documents = json.loads(doc_path.read_text())
                document_ok = True
                break
            except Paused: raise
            except Exception as exc:
                if attempt: alerts.append({'kind':'review_analysis_failed','priority':'high','reason':'표 검토를 완료하지 못했습니다.','detail':str(exc)})
        self.check_stop()
        self.emit('status', phase='wrap', page=number, message=f'{number}쪽 줄바꿈 확인')
        if self.wrap is None: self.wrap = WrapAnalyzer()
        decisions = self.wrap.analyze(ocr['lines'], documents) if document_ok else []
        for item in decisions:
            if item['action']=='review': alerts.append({'kind':'uncertain_wrap','priority':'medium','reason':'줄 경계 연결을 확정하지 못했습니다.','evidence':[item['left'][-30:],item['right'][:30]]})
        image = cv2.imread(str(image_path))
        height, width = image.shape[:2]
        if not ocr['lines']:
            alerts.append({'kind':'no_text','priority':'medium','reason':'인식된 글자가 없습니다. 빈 페이지 또는 그림인지 확인하세요.'})
        regions, recognized = [], []
        if ocr['lines']:
            self.emit('status', phase='review', page=number, message=f'{number}쪽 표·수식 검토')
            try:
                if self.detector is None:
                    options = ort.SessionOptions(); options.intra_op_num_threads = 4
                    self.detector = ort.InferenceSession(str(self.resources.models/'pix2text-mfd-1.5.onnx'),sess_options=options,providers=['CPUExecutionProvider'])
                regions = formulas(self.detector,image) + (apple_tables(doc_path,width,height) if document_ok else []) + code_tables(image,ocr)
                regions, _ = refine_candidates(regions,ocr,width,height,documents)
                regions.sort(key=lambda r:(r['bbox'][1],r['bbox'][0]))
                for i,r in enumerate(regions): r['id'] = f'{number}-{i:03}'
                alerts += table_reviews(image,ocr,documents,regions)
                formula_regions = [r for r in regions if r['kind']=='formula']
                if formula_regions:
                    high_path = self.render(snapshot,number,400,work/'high',max(geometry[:2]))
                    with Image.open(high_path) as im: high = im.convert('RGB')
                    sx, sy = high.width/width, high.height/height
                    if self.recognizer is None: self.recognizer = FormulaRecognizer(self.resources.models/'pix2text-mfr-1.5')
                    for r in formula_regions:
                        self.check_stop()
                        try:
                            box = [v*(sx if i%2==0 else sy) for i,v in enumerate(r.get('crop_bbox',r['bbox']))]
                            focus = [v*(sx if i%2==0 else sy) for i,v in enumerate(r['bbox'])] if 'crop_bbox' in r else None
                            crop, _ = prepare_formula_crop(high,box,focus)
                            result = self.recognizer.recognize(crop)
                            recognized.append(dict(r,**result))
                        except Paused: raise
                        except Exception as exc:
                            alerts.append({'kind':'formula_failed','priority':'high','bbox':r['bbox'],'reason':'수식을 읽지 못했습니다.','detail':str(exc)})
                    alerts += formula_reviews(recognized)
            except Paused: raise
            except Exception as exc:
                alerts.append({'kind':'review_analysis_failed','priority':'high','reason':'표·수식 검토 일부를 완료하지 못했습니다.','detail':str(exc)})
        self.check_stop()
        return {'ocr':ocr,'documents':documents,'wraps':decisions,'regions':regions,'formulas':recognized,
                'alerts':alerts,'image_path':str(image_path),'image_size':[width,height]}
