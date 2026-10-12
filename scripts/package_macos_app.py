"""Build the native UI and a self-contained local OCR engine for this Mac."""
import hashlib,json,os,plistlib,shutil,subprocess
from pathlib import Path
root=Path(__file__).resolve().parent.parent
build=root/'build';build.mkdir(exist_ok=True)
python=root/'.venv-app/bin/python'
def run(args,**kwargs):subprocess.run([str(x) for x in args],check=True,**kwargs)
cache='/private/tmp/ocr-swift-cache'
run(['swiftc','-module-cache-path',cache,'-target','arm64-apple-macosx26.0','-swift-version','5','-O','-parse-as-library','-framework','SwiftUI','-framework','AppKit',root/'mac_app/OCRApp.swift','-o',build/'OCRHelper'])
(build/'native').mkdir(exist_ok=True)
run(['swiftc','-module-cache-path',cache,root/'scripts/vision_sample.swift','-o',build/'native/vision_text'])
run(['swiftc','-parse-as-library','-module-cache-path',cache,root/'scripts/vision_document.swift','-o',build/'native/vision_document'])
env=dict(os.environ,PYINSTALLER_CONFIG_DIR='/private/tmp/ocr-pyinstaller')
with (build/'pyinstaller.log').open('w') as log:
 run([python,'-m','PyInstaller','--noconfirm','--onedir','--name','ocr-worker','--distpath',build/'frozen','--workpath',build/'pyinstaller','--specpath',build,'--paths',root,'--paths',root/'scripts','--collect-all','kiwipiepy','--collect-all','kiwipiepy_model','--collect-all','onnxruntime','--collect-all','tokenizers','--collect-data','reportlab',root/'ocr_app/worker.py'],env=env,stdout=log,stderr=subprocess.STDOUT)
app=root/'OCR 도우미.app'
if app.exists():
 info=app/'Contents/Info.plist'
 if not info.exists() or plistlib.loads(info.read_bytes()).get('CFBundleIdentifier')!='local.codex.ocr-helper':
  raise RuntimeError('Refusing to replace an unrelated app bundle')
 shutil.rmtree(app)
contents=app/'Contents';macos=contents/'MacOS';resources=contents/'Resources'
macos.mkdir(parents=True);resources.mkdir()
shutil.copy2(build/'OCRHelper',macos/'OCRHelper')
shutil.copytree(build/'frozen/ocr-worker',resources/'engine',symlinks=True)
shutil.copytree(build/'native',resources/'native')
(resources/'models/pix2text-mfr-1.5').mkdir(parents=True)
shutil.copy2(root/'models/pix2text-mfd-1.5.onnx',resources/'models/pix2text-mfd-1.5.onnx')
for file in (root/'models/pix2text-mfr-1.5').iterdir():
 if file.suffix in ('.json','.onnx'):shutil.copy2(file,resources/'models/pix2text-mfr-1.5'/file.name)
poppler=Path(os.environ['OCR_POPPLER_DIR']).expanduser().resolve()
shutil.copytree(poppler,resources/'poppler',symlinks=True)
paths=sorted((root/'ocr_app').glob('*.py'))+[root/'scripts'/name for name in ['automatic_wraps.py','detect_regions.py','quality_reviews.py','formula_crop.py','formula_onnx.py','vision_sample.swift','vision_document.swift']]
recipe={'version':'1.0.0','source_sha256':{str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},'requirements':(root/'requirements-app.txt').read_text(),'render_dpi':300,'formula_dpi':400}
(resources/'recipe.json').write_text(json.dumps(recipe,ensure_ascii=False,sort_keys=True,indent=2))
info={'CFBundleName':'OCR 도우미','CFBundleDisplayName':'OCR 도우미','CFBundleExecutable':'OCRHelper','CFBundleIdentifier':'local.codex.ocr-helper','CFBundleVersion':'1','CFBundleShortVersionString':'1.0.0','CFBundlePackageType':'APPL','LSMinimumSystemVersion':'26.0','NSHighResolutionCapable':True,'NSPrincipalClass':'NSApplication','CFBundleDocumentTypes':[{'CFBundleTypeName':'PDF document','CFBundleTypeRole':'Viewer','LSHandlerRank':'Alternate','LSItemContentTypes':['com.adobe.pdf']}]}
(contents/'Info.plist').write_bytes(plistlib.dumps(info))
run(['codesign','--force','--deep','--sign','-',app])
run(['codesign','--verify','--deep','--strict',app])
print(app,flush=True)
