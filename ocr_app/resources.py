import hashlib
import json
import shutil
import sys
from pathlib import Path
from .storage import UserError

class Resources:
    def __init__(self, root=None):
        self.root = Path(root).resolve() if root else (Path(sys.executable).resolve().parent.parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1])
        if getattr(sys, 'frozen', False) or root:
            self.models = self.root/'models'
            self.text = self.root/'native/vision_text'
            self.document = self.root/'native/vision_document'
            self.poppler = self.root/'poppler/bin/pdftoppm'
            self.recipe = (self.root/'recipe.json').read_bytes()
        else:
            self.models = self.root/'models'
            self.text = self.root/'build/native/vision_text'
            self.document = self.root/'build/native/vision_document'
            self.poppler = Path(shutil.which('pdftoppm') or '/nonexistent/pdftoppm')
            paths = sorted((self.root/'ocr_app').glob('*.py'))
            paths += [self.root/'scripts/runtime'/name for name in ['automatic_wraps.py','detect_regions.py','quality_reviews.py','formula_crop.py','formula_onnx.py']]
            self.recipe = json.dumps({str(p.relative_to(self.root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},sort_keys=True).encode()
        self.fingerprint = hashlib.sha256(self.recipe+b'|dpi300|math400|review-v1').hexdigest()

    def check(self):
        paths = [self.text, self.document, self.poppler, self.models/'pix2text-mfd-1.5.onnx',
                 self.models/'pix2text-mfr-1.5/encoder_model.onnx', self.models/'pix2text-mfr-1.5/decoder_model.onnx']
        if any(not path.is_file() for path in paths):
            raise UserError('앱의 실행 파일 또는 모델이 없습니다. 완성된 앱을 다시 사용하세요.')
