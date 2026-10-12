import json,subprocess,sys
from pathlib import Path
from PIL import Image,ImageDraw,ImageChops
from reportlab.pdfgen import canvas
from pypdf import PdfReader,PdfWriter
from pypdf.generic import RectangleObject
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ocr_app.pdf_layer import display_geometry,create_overlay,export_pdf
root=Path(__file__).resolve().parents[1];out=root/'artifacts/tests/regression/geometry';out.mkdir(parents=True,exist_ok=True)
image=Image.new('RGB',(600,800),'white');d=ImageDraw.Draw(image)
for x in range(0,600,50):d.line((x,0,x,800),fill=(220,225,235),width=2)
for y in range(0,800,50):d.line((0,y,600,y),fill=(220,225,235),width=2)
d.rectangle((70,100,180,240),fill=(150,190,240));image.save(out/'scan.png')
c=canvas.Canvas(str(out/'base.pdf'),pagesize=(600,800));c.drawImage(str(out/'scan.png'),0,0,600,800);c.save()
base=PdfReader(out/'base.pdf');writer=PdfWriter()
for rotation in [0,90,180,270]:
 page=writer.add_page(base.pages[0]);page.cropbox=RectangleObject([40,80,560,740]);page.rotation=rotation
writer.write(out/'source.pdf')
reader=PdfReader(out/'source.pdf');overlays={};expected=[]
for index,page in enumerate(reader.pages,1):
 w,h,matrix=display_geometry(page)
 line={'text':'검증','box':[.2,.3,.095,.04],'chars':[{'text':'검','box':[.2,.3,.045,.04]},{'text':'증','box':[.25,.3,.045,.04]}]}
 path=out/f'overlay-{index}.pdf';path.write_bytes(create_overlay(w,h,[line],set()));overlays[index]=path
 expected.append({'page':index,'rotation':page.rotation,'display_size':[w,h],'matrix':matrix})
export_pdf(out/'source.pdf',[1,2,3,4],overlays,out/'result.pdf')
for index in range(1,5):
 for name in ['source','result']:
  subprocess.run(['pdftoppm','-f',str(index),'-l',str(index),'-cropbox','-scale-to','800','-singlefile','-png',str(out/f'{name}.pdf'),str(out/f'{name}-{index}')],check=True)
 a=Image.open(out/f'source-{index}.png').convert('RGB');b=Image.open(out/f'result-{index}.png').convert('RGB')
 assert ImageChops.difference(a,b).getbbox() is None,index
(out/'expected.json').write_text(json.dumps(expected,indent=2))
print('Four rotations with nonzero CropBox: visible pixels unchanged.')
