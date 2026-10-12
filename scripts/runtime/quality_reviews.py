"""Evidence-based review alerts. An alert is a suspicion, never a correction."""
import re
import cv2
import numpy as np


def overlap(a,b):
    area=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    return area/max(1,(a[2]-a[0])*(a[3]-a[1]))


def words_from_ocr(ocr,width,height):
    words=[]
    for line in ocr['lines']:
        for match in re.finditer(r'\S+',line['text']):
            boxes=[c['box'] for c in line['chars'][match.start():match.end()] if c['box'][2]>0 and c['box'][3]>0]
            if not boxes:continue
            x=min(b[0] for b in boxes);y=min(b[1] for b in boxes)
            right=max(b[0]+b[2] for b in boxes);top=max(b[1]+b[3] for b in boxes)
            words.append({'text':match.group(),'bbox':[x*width,(1-top)*height,right*width,(1-y)*height]})
    return words


def numeric_table_candidates(image,ocr):
    height,width=image.shape[:2]
    gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
    binary=cv2.adaptiveThreshold(gray,255,cv2.ADAPTIVE_THRESH_GAUSSIAN_C,cv2.THRESH_BINARY_INV,41,12)
    horizontal=cv2.morphologyEx(binary,cv2.MORPH_OPEN,np.ones((1,max(40,width//8)),np.uint8))
    contours,_=cv2.findContours(horizontal,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    bars=[]
    for contour in contours:
        x,y,w,h=cv2.boundingRect(contour)
        if w>=width*.28 and h<=height*.06:bars.append([x,y,x+w,y+h])
    bars.sort(key=lambda b:b[1]);groups=[]
    for bar in bars:
        group=next((g for g in reversed(groups) if abs(g[-1][0]-bar[0])<width*.035 and abs(g[-1][2]-bar[2])<width*.035 and bar[1]-g[-1][3]<height*.12),None)
        if group is None:groups.append([bar])
        else:group.append(bar)
    words=words_from_ocr(ocr,width,height);candidates=[]
    for group in groups:
        if len(group)<2:continue
        box=[min(b[0] for b in group),min(b[1] for b in group),max(b[2] for b in group),max(b[3] for b in group)]
        if not height*.025<box[3]-box[1]<height*.3:continue
        contained=[word for word in words if overlap(word['bbox'],box)>.6]
        numeric=[word for word in contained if re.fullmatch(r'[+−-]?\d+(?:[./]\d+)?%?',word['text'])]
        if len(numeric)<6 or len(numeric)/max(1,len(contained))<.35:continue
        y_centers=sorted((w['bbox'][1]+w['bbox'][3])/2 for w in numeric)
        if y_centers[-1]-y_centers[0]<height*.015:continue
        candidates.append({'bbox':box,'numeric_evidence':[w['text'] for w in numeric],'rule_count':len(group)})
    return candidates


def table_reviews(image,ocr,documents,detected_regions):
    height,width=image.shape[:2];alerts=[]
    known=[r['bbox'] for r in detected_regions if r['kind'] in ('table','code_table')]
    for candidate in numeric_table_candidates(image,ocr):
        if not any(overlap(candidate['bbox'],box)>.65 for box in known):
            alerts.append({'kind':'possible_missing_table','priority':'high','bbox':candidate['bbox'],
                           'reason':'Aligned horizontal rules and multiple numeric rows exist outside recognized tables.',
                           'evidence':candidate['numeric_evidence']})
    words=words_from_ocr(ocr,width,height)
    for di,document in enumerate(documents):
        for ti,table in enumerate(document['tables']):
            for ri,row in enumerate(table):
                for ci,cell in enumerate(row):
                    if cell['text'].strip():continue
                    x,y,w,h=cell['box'];box=[x*width,(1-y-h)*height,(x+w)*width,(1-y)*height]
                    evidence=[word['text'] for word in words if overlap(word['bbox'],box)>.7 and re.search(r'\d|[가-힣]{2}|[A-Za-z]{2}',word['text'])]
                    if evidence:
                        alerts.append({'kind':'empty_cell_with_ocr_evidence','priority':'high','bbox':box,
                                       'cell':[di,ti,ri,ci],'reason':'Table parser returned an empty cell, but independent text OCR found ink/text here.',
                                       'evidence':evidence})
    return alerts


def formula_reasons(result):
    text=result['latex'];reasons=[]
    if not text.strip():reasons.append('empty_formula')
    if not result.get('complete',False):reasons.append('generation_truncated')
    braces=re.sub(r'\\[{}]','',text);depth=0
    for char in braces:
        if char=='{':depth+=1
        elif char=='}':depth-=1
        if depth<0:break
    if depth:reasons.append('unbalanced_braces')
    if re.search(r'(?:[\^_]|\\overset|\\underset)\s*\{\s*[\"\x27`]',text):reasons.append('quoted_math_bound')
    if re.search(r'[\^_]\s*\{(?:\s|\\[!,;:])*[\^_]',text):reasons.append('nested_script_without_base')
    if r'\phantom' in text:reasons.append('phantom_content')
    if result.get('confidence',0)<.95:reasons.append('low_generation_confidence')
    return reasons


def formula_reviews(results):
    output=[]
    for result in results:
        reasons=formula_reasons(result)
        output.append({'kind':'formula','id':result['id'],'bbox':result['bbox'],
                       'priority':'high' if any(r!='low_generation_confidence' for r in reasons) else ('medium' if reasons else 'normal'),
                       'status':'suspected_error' if reasons else 'unverified_no_rule_flag',
                       'requires_review':True,'reasons':reasons,'latex':result['latex'],
                       'confidence':result.get('confidence'),'note':'No rule flag does not mean the expression is correct.'})
    return output
