"""Detect formula and table regions without page-specific coordinates.

Formula detection: Pix2Text MFD 1.5 (ONNX, local).
Table cells: Apple Vision document analysis; bordered statistical output: OpenCV.
Coordinates in the output use pixels with a top-left origin.
"""
import argparse
import ast
import json
import re
from pathlib import Path
import cv2
import numpy as np
import onnxruntime as ort


def nms(boxes, scores, threshold=0.4):
    order = np.argsort(scores)[::-1]
    keep = []
    while order.size:
        i = int(order[0]); keep.append(i)
        if order.size == 1: break
        rest = order[1:]
        a, b = boxes[i], boxes[rest]
        wh = np.maximum(0, np.minimum(a[2:],b[:,2:])-np.maximum(a[:2],b[:,:2]))
        overlap = wh[:,0]*wh[:,1]
        area_a = np.prod(a[2:]-a[:2])
        area_b = np.prod(b[:,2:]-b[:,:2],axis=1)
        iou = overlap / np.maximum(area_a+area_b-overlap,1e-8)
        order = rest[iou <= threshold]
    return keep


def formulas(session, image, size=1536, confidence=0.35):
    height,width=image.shape[:2]
    scale=min(size/width,size/height)
    resized=cv2.resize(image,(round(width*scale),round(height*scale)))
    left=(size-resized.shape[1])//2; top=(size-resized.shape[0])//2
    canvas=np.full((size,size,3),114,np.uint8)
    canvas[top:top+resized.shape[0],left:left+resized.shape[1]]=resized
    tensor=np.ascontiguousarray(canvas[:,:,::-1].transpose(2,0,1)[None],dtype=np.float32)/255
    prediction=session.run(None,{session.get_inputs()[0].name:tensor})[0][0].T
    scores=prediction[:,4:].max(axis=1)
    classes=prediction[:,4:].argmax(axis=1)
    selected=scores>=confidence
    p=prediction[selected]; scores=scores[selected]; classes=classes[selected]
    boxes=np.concatenate([p[:,:2]-p[:,2:4]/2,p[:,:2]+p[:,2:4]/2],axis=1)
    boxes=(boxes-np.array([left,top,left,top]))/scale
    boxes[:,[0,2]]=boxes[:,[0,2]].clip(0,width)
    boxes[:,[1,3]]=boxes[:,[1,3]].clip(0,height)
    names=ast.literal_eval(session.get_modelmeta().custom_metadata_map['names'])
    return [{'kind':'formula','class':names[int(classes[i])], 'score':round(float(scores[i]),5),
             'bbox':boxes[i].round(2).tolist(),'source':'pix2text-mfd-1.5'}
            for i in nms(boxes,scores)]


def apple_tables(path,width,height):
    if not path.exists(): return []
    tables=[]
    for doc in json.loads(path.read_text()):
        for table in doc['tables']:
            boxes=[c['box'] for row in table for c in row]
            x=min(b[0] for b in boxes); y=min(b[1] for b in boxes)
            right=max(b[0]+b[2] for b in boxes); top=max(b[1]+b[3] for b in boxes)
            tables.append({'kind':'table','bbox':[x*width,(1-top)*height,right*width,(1-y)*height],
                           'source':'apple-vision-document','rows':len(table),'columns':len(table[0])})
    return tables


def code_tables(image,ocr):
    height,width=image.shape[:2]
    gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
    binary=cv2.adaptiveThreshold(gray,255,cv2.ADAPTIVE_THRESH_GAUSSIAN_C,cv2.THRESH_BINARY_INV,41,12)
    horizontal=cv2.morphologyEx(binary,cv2.MORPH_OPEN,np.ones((1,max(30,width//30)),np.uint8))
    vertical=cv2.morphologyEx(binary,cv2.MORPH_OPEN,np.ones((max(30,height//30),1),np.uint8))
    mask=cv2.dilate(horizontal|vertical,np.ones((3,3),np.uint8))
    contours,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    output=[]
    for contour in contours:
        x,y,w,h=cv2.boundingRect(contour)
        if w<width*.3 or h<height*.1 or w*h>width*height*.8: continue
        texts=[]
        for line in ocr['lines']:
            bx,by,bw,bh=line['box'];cx=(bx+bw/2)*width;cy=(1-by-bh/2)*height
            if x<=cx<=x+w and y<=cy<=y+h: texts.append(line['text'])
        joined=' '.join(texts).lower()
        if sum(token in joined for token in ['estimate','residuals','aic:','deviance','coefficients','coeficients'])<2: continue
        output.append({'kind':'code_table','bbox':[x,y,x+w,y+h],'source':'ruled-box-and-statistical-headings'})
    return output


def text_boxes(ocr, width, height):
    result=[]
    for line in ocr['lines']:
        for char in line['chars']:
            x,y,w,h=char['box']
            if w<=0 or h<=0: continue
            result.append((char['text'],[x*width,(1-y-h)*height,(x+w)*width,(1-y)*height]))
    return result


def center_inside(a,b):
    return b[0]<=(a[0]+a[2])/2<=b[2] and b[1]<=(a[1]+a[3])/2<=b[3]


def refine_candidates(regions, ocr, width, height, document=None):
    """Reject Korean prose and recover operator-rich inline OCR spans."""
    tables=[r for r in regions if r['kind'] in ('table','code_table')]
    formulas=[r for r in regions if r['kind']=='formula']
    chars=text_boxes(ocr,width,height)
    rejected=[]; candidates=[]
    for r in formulas:
        box=list(r['bbox'])
        overlaps=[(ch,b) for ch,b in chars if center_inside(b,box)]
        hangul=[b for ch,b in overlaps if re.search('[가-힣]',ch)]
        nonhangul=[b for ch,b in overlaps if re.search('[A-Za-z0-9α-ωΑ-Ω=+*/<>±]',ch)]
        if hangul and not nonhangul:
            rejected.append(dict(r,reason='Korean prose, not a formula'));continue
        # Trim Korean particles accidentally included at either edge.
        for b in hangul:
            if nonhangul and b[0]>=max(v[2] for v in nonhangul)-2: box[2]=min(box[2],b[0])
            elif nonhangul and b[2]<=min(v[0] for v in nonhangul)+2: box[0]=max(box[0],b[2])
        text=''.join(ch for ch,b in overlaps if center_inside(b,box))
        if text and re.fullmatch(r'[*☆★✓•.·…]+',text):
            rejected.append(dict(r,reason='Decoration or punctuation already preserved by base OCR'));continue
        if re.fullmatch('[A-Za-z]',text) and not any(center_inside(box,t['bbox']) for t in tables):
            rejected.append(dict(r,reason='Plain Latin letter outside a table'));continue
        contained=[f for f in formulas if f is not r and
                   f['bbox'][0]>=box[0] and f['bbox'][2]<=box[2] and
                   f['bbox'][1]>=box[1]-5 and f['bbox'][3]<=box[3]+5]
        if len(contained)>=2 and any(center_inside(box,t['bbox']) for t in tables):
            rejected.append(dict(r,reason='Multi-cell region already covered by individual formulas'));continue
        if box[2]<=box[0] or box[3]<=box[1]:continue
        candidates.append(dict(r,bbox=box))
    for line in ocr['lines']:
        # Only spans with a digit and an arithmetic/comparison operator qualify.
        for match in re.finditer(r'[^가-힣]+',line['text']):
            text=match.group().strip()
            if not re.search('[0-9]',text) or not re.search(r'[=+*/<>×±−-]',text):continue
            operators=re.findall(r'[=+*/<>×±−-]',text)
            if len(operators)<2 and len(re.findall('[0-9]',text))<2 and '=' not in text:continue
            subset=[c['box'] for c in line['chars'][match.start():match.end()] if c['box'][2]>0 and c['box'][3]>0]
            if not subset:continue
            x=min(b[0] for b in subset)*width;right=max(b[0]+b[2] for b in subset)*width
            top=(1-max(b[1]+b[3] for b in subset))*height;bottom=(1-min(b[1] for b in subset))*height
            box=[x,top,right,bottom]
            if any(t['kind']=='code_table' and center_inside(box,t['bbox']) for t in tables):continue
            if any(center_inside(box,r['bbox']) or center_inside(r['bbox'],box) for r in candidates):continue
            candidates.append({'kind':'formula','class':'embedding','score':None,'bbox':box,'source':'ocr-math-operators'})
    # A detector can clip tiny subscripts. In a math-only table cell, use the
    # complete cell as recognition context; the ink crop removes table rules.
    for r in candidates:
        for doc in document or []:
            for table in doc['tables']:
                for row in table:
                    for cell in row:
                        x,y,w,h=cell['box']
                        box=[x*width,(1-y-h)*height,(x+w)*width,(1-y)*height]
                        if center_inside(r['bbox'],box) and not re.search('[가-힣]',cell['text']):
                            if sum(center_inside(other['bbox'],box) for other in candidates)==1:
                                r['crop_bbox']=box
    return candidates+tables,rejected


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pages',type=int,nargs='+',default=[80,269,280,400])
    parser.add_argument('--size',type=int,default=1536)
    args=parser.parse_args()
    root=Path(__file__).resolve().parent.parent
    options=ort.SessionOptions(); options.intra_op_num_threads=4
    session=ort.InferenceSession(str(root/'models/pix2text-mfd-1.5.onnx'),sess_options=options,providers=['CPUExecutionProvider'])
    output=root/'tmp/pdfs/auto';output.mkdir(exist_ok=True)
    for page in args.pages:
        path=root/f'tmp/pdfs/ocr-{page}.png'
        image=cv2.imread(str(path));height,width=image.shape[:2]
        ocr=json.loads((root/f'tmp/pdfs/ocr-{page}.json').read_text())
        regions=formulas(session,image,args.size)+apple_tables(root/f'tmp/pdfs/document-{page}.json',width,height)+code_tables(image,ocr)
        document_path=root/f'tmp/pdfs/document-{page}.json'
        document=json.loads(document_path.read_text()) if document_path.exists() else []
        regions,rejected=refine_candidates(regions,ocr,width,height,document)
        regions.sort(key=lambda r:(r['bbox'][1],r['bbox'][0]))
        for i,region in enumerate(regions):region['id']=f'{page}-{i:03}'
        (output/f'regions-{page}.json').write_text(json.dumps({'page':page,'image_size':[width,height],'regions':regions,'rejected':rejected},ensure_ascii=False,indent=2))
        marked=image.copy()
        for region in regions:
            x1,y1,x2,y2=map(round,region['bbox']);color=(0,0,255) if region['kind']=='formula' else (0,150,0)
            cv2.rectangle(marked,(x1,y1),(x2,y2),color,2)
            cv2.putText(marked,region['id'],(x1,max(12,y1-4)),cv2.FONT_HERSHEY_SIMPLEX,.5,color,1)
        cv2.imwrite(str(output/f'detection-{page}.png'),marked)
        print(page,{kind:sum(r['kind']==kind for r in regions) for kind in ['formula','table','code_table']},flush=True)

if __name__=='__main__':main()
