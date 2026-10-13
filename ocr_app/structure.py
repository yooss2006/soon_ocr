"""Geometry-based document structure with explicit, image-backed uncertainty."""
import html
import re
import cv2
import numpy as np


def union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def intersection_ratio(a, b):
    area=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    return area/max(1,(a[2]-a[0])*(a[3]-a[1]))


def pixel_box(box, width, height):
    x,y,w,h=box
    return [x*width,(1-y-h)*height,(x+w)*width,(1-y)*height]


def whitespace_groups(items, width, height, depth=0):
    """Recursive whitespace cuts separate columns; never interleave their lines."""
    if len(items)<2 or depth>12: return [items]
    candidates=[]
    for axis,minimum in [(0,width*.025),(1,height*.022)]:
        ordered=sorted(items,key=lambda item:item['bbox'][axis])
        edge=ordered[0]['bbox'][axis+2]
        for i,item in enumerate(ordered[1:],1):
            gap=item['bbox'][axis]-edge
            if gap>minimum:
                candidates.append((gap/minimum,ordered[:i],ordered[i:]))
            edge=max(edge,item['bbox'][axis+2])
    if not candidates: return [sorted(items,key=lambda item:(round(item['bbox'][1]/max(1,height*.004)),item['bbox'][0]))]
    _,left,right=max(candidates,key=lambda candidate:candidate[0])
    return whitespace_groups(left,width,height,depth+1)+whitespace_groups(right,width,height,depth+1)


def document_tables(documents, width, height):
    tables=[]
    for document in documents:
        for table in document.get('tables',[]):
            cells=[]; seen=set()
            for ri,row in enumerate(table):
                for ci,cell in enumerate(row):
                    rr=cell.get('row_range',[ri,ri+1]);cr=cell.get('column_range',[ci,ci+1])
                    key=tuple(rr+cr)
                    if key in seen: continue
                    seen.add(key)
                    cells.append({'text':cell['text'],'bbox':pixel_box(cell['box'],width,height),
                                  'row':rr[0],'column':cr[0],'rowspan':rr[1]-rr[0],'colspan':cr[1]-cr[0]})
            if not cells: continue
            # Conflicting spans are not silently flattened into a plausible table.
            occupied=set();valid=True
            for cell in cells:
                if min(cell['rowspan'],cell['colspan'])<1 or min(cell['row'],cell['column'])<0: valid=False;break
                for r in range(cell['row'],cell['row']+cell['rowspan']):
                    for c in range(cell['column'],cell['column']+cell['colspan']):
                        if (r,c) in occupied:valid=False
                        occupied.add((r,c))
            tables.append({'kind':'table','bbox':union([cell['bbox'] for cell in cells]),'cells':cells,
                           'source':'document-recognizer','valid_spans':valid,'requires_review':True})
    return tables


def colored_tables(image, lines, existing):
    """Recover two-column table candidates from a colored label column, not vocabulary."""
    height,width=image.shape[:2]
    hsv=cv2.cvtColor(image,cv2.COLOR_BGR2HSV)
    mask=cv2.inRange(hsv,np.array([0,85,45]),np.array([179,255,255]))
    mask=cv2.morphologyEx(mask,cv2.MORPH_OPEN,np.ones((7,7),np.uint8))
    mask=cv2.morphologyEx(mask,cv2.MORPH_CLOSE,np.ones((15,15),np.uint8))
    contours,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    output=[]
    for contour in contours:
        x,y,w,h=cv2.boundingRect(contour)
        if not (width*.08<w<width*.32 and height*.12<h<height*.55 and h>w*1.3):continue
        panel=[x,y,x+w,y+h]
        if any(intersection_ratio(panel,t['bbox'])>.5 for t in existing):continue
        labels=[line for line in lines if intersection_ratio(line['bbox'],panel)>.65]
        labels.sort(key=lambda line:line['bbox'][1])
        groups=[]
        for line in labels:
            if groups and line['bbox'][1]-groups[-1][-1]['bbox'][3]<height*.012:groups[-1].append(line)
            else:groups.append([line])
        if len(groups)<3:continue
        right=[line for line in lines if line['bbox'][0]>x+w-5 and y<=sum(line['bbox'][1::2])/2<=y+h]
        if len(right)<len(groups):continue
        edge=max(line['bbox'][2] for line in right)
        centers=[sum(union([line['bbox'] for line in group])[1::2])/2 for group in groups]
        boundaries=[y]+[(a+b)/2 for a,b in zip(centers,centers[1:])]+[y+h]
        cells=[]
        for row,group in enumerate(groups):
            top,bottom=boundaries[row:row+2]
            candidates=[line for line in right if top<=sum(line['bbox'][1::2])/2<bottom]
            if not candidates:break
            cells.extend([{'row':row,'column':0,'rowspan':1,'colspan':1,'text':'\n'.join(l['text'] for l in group),'bbox':[x,top,x+w,bottom]},
                          {'row':row,'column':1,'rowspan':1,'colspan':1,'text':'\n'.join(l['text'] for l in candidates),'bbox':[x+w,top,edge,bottom]}])
        else:
            output.append({'kind':'table','bbox':[x,y,edge,y+h],'cells':cells,'source':'colored-column-geometry',
                           'valid_spans':True,'requires_review':True})
    return output


def figure_regions(image, lines, tables):
    """Keep non-text connected structures as images. Full-page evidence covers misses."""
    height,width=image.shape[:2]
    gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
    mask=cv2.threshold(gray,175,255,cv2.THRESH_BINARY_INV)[1]
    for item in lines+tables:
        x0,y0,x1,y1=[int(v) for v in item['bbox']]
        mask[max(0,y0-2):min(height,y1+3),max(0,x0-2):min(width,x1+3)]=0
    margin=max(1,int(width*.02));mask[:margin]=0;mask[-margin:]=0;mask[:,:margin]=0;mask[:,-margin:]=0
    mask=cv2.morphologyEx(mask,cv2.MORPH_CLOSE,np.ones((max(3,int(height*.008)),max(3,int(width*.008))),np.uint8))
    contours,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    boxes=[]
    for contour in contours:
        x,y,w,h=cv2.boundingRect(contour)
        if w>width*.14 and h>height*.055 and w*h>width*height*.015:
            box=[max(0,x-12),max(0,y-12),min(width,x+w+12),min(height,y+h+12)]
            if not any(intersection_ratio(box,t['bbox'])>.7 for t in tables):boxes.append(box)
    return [{'kind':'figure','bbox':box,'requires_review':True,'source':'non-text-geometry'} for box in sorted(boxes,key=lambda b:(b[1],b[0]))]


def build_structure(data, image):
    height,width=image.shape[:2]
    lines=[{'id':i,'text':line['text'],'bbox':pixel_box(line['box'],width,height),
            'confidence':line.get('confidence')} for i,line in enumerate(data['ocr']['lines'])]
    tables=document_tables(data.get('documents',[]),width,height)
    tables+=colored_tables(image,lines,tables)
    figures=figure_regions(image,lines,tables)
    blocks=[]
    # Every OCR line belongs to exactly one prose, table, or figure block.
    remaining=list(lines)
    for region in tables+figures:
        assigned=[line for line in remaining if intersection_ratio(line['bbox'],region['bbox'])>.65]
        region['line_ids']=[line['id'] for line in assigned]
        region['ocr_text']='\n'.join(line['text'] for line in assigned)
        remaining=[line for line in remaining if line not in assigned]
        blocks.append(region)
    joins={d['line_index'] for d in data.get('wraps',[]) if d.get('action')=='join' and d.get('next_line_index')==d['line_index']+1}
    for group in whitespace_groups(remaining,width,height):
        if not group:continue
        text=''
        for i,line in enumerate(group):
            text+=line['text'].strip()
            text+='' if i+1<len(group) and line['id'] in joins and group[i+1]['id']==line['id']+1 else '\n'
        blocks.append({'kind':'text','bbox':union([l['bbox'] for l in group]),'text':text.strip(),
                       'line_ids':[l['id'] for l in group],'requires_review':True})
    ordered=[block for group in whitespace_groups(blocks,width,height) for block in group]
    for i,block in enumerate(ordered,1):block['id']=f'b{i:03}'
    formulas=[]
    for i,result in enumerate(data.get('formulas',[]),1):
        formulas.append({'id':f'm{i:03}','kind':'formula','bbox':result['bbox'],'latex':result.get('latex',''),
                         'complete':result.get('complete',False),'confidence':result.get('confidence'),
                         'requires_review':True,'source':'formula-recognizer'})
    for formula in formulas:
        parents=[b for b in ordered if intersection_ratio(formula['bbox'],b['bbox'])>.6]
        formula['parent_block']=parents[0]['id'] if parents else None
        formula['table_cells']=[]
        for parent in parents:
            if parent['kind']=='table':
                formula['table_cells']=[{'row':cell['row'],'column':cell['column']}
                                        for cell in parent['cells'] if intersection_ratio(formula['bbox'],cell['bbox'])>.5]
    assigned=[n for b in ordered for n in b['line_ids']]
    if sorted(assigned)!=list(range(len(lines))):raise ValueError('OCR line coverage failed')
    structure={'width':width,'height':height,'blocks':ordered,'formulas':formulas,'ocr_lines':lines,
            'coverage':{'ocr_line_count':len(lines),'assigned_line_count':len(assigned)},
            'verification':'source_images_attached_not_semantically_verified'}
    return reflow_structure(structure,data.get('wraps',[]))


def table_html(table):
    if not table['valid_spans']:return '<p>셀 병합 범위를 확인하지 못했습니다. 원본 이미지를 확인하세요.</p>'
    anchors={(cell['row'],cell['column']):cell for cell in table['cells']}
    rows=max(c['row']+c['rowspan'] for c in table['cells'])
    columns=max(c['column']+c['colspan'] for c in table['cells'])
    if rows*columns>10000:return '<p>표 범위가 너무 큽니다. 원본 이미지와 셀 데이터를 확인하세요.</p>'
    covered=set()
    for cell in table['cells']:
        covered.update((r,c) for r in range(cell['row'],cell['row']+cell['rowspan'])
                       for c in range(cell['column'],cell['column']+cell['colspan']))
    result='<table>\n'
    for row in range(rows):
        result+='<tr>'
        for column in range(columns):
            cell=anchors.get((row,column))
            if cell:
                result+=f'<td rowspan="{cell["rowspan"]}" colspan="{cell["colspan"]}">'+html.escape(cell['text']).replace('\n','<br>')+'</td>'
            elif (row,column) not in covered:
                result+='<td></td>'
        result+='</tr>\n'
    return result+'</table>'


def table_markdown(table):
    if not table['valid_spans'] or any(c['rowspan']!=1 or c['colspan']!=1 for c in table['cells']):
        return table_html(table)
    cells={(c['row'],c['column']):c['text'] for c in table['cells']}
    rows=max(r for r,c in cells)+1;columns=max(c for r,c in cells)+1
    if rows*columns>10000:return table_html(table)
    def esc(text):return html.escape(text).replace('\\','\\\\').replace('|','\\|').replace('\n','<br>')
    result='| '+' | '.join(f'열 {i+1}' for i in range(columns))+' |\n'
    result+='| '+' | '.join('---' for _ in range(columns))+' |\n'
    for row in range(rows):result+='| '+' | '.join(esc(cells.get((row,c),'')) for c in range(columns))+' |\n'
    return result


def text_columns(lines, width, height, depth=0):
    """Allow a few spanning titles without losing the gutter between text columns."""
    if len(lines)<4 or depth>=3:return whitespace_groups(lines,width,height)
    edges=sorted({b['bbox'][i] for b in lines for i in (0,2)})
    candidates=[]
    for left_edge,right_edge in zip(edges,edges[1:]):
        gap=right_edge-left_edge;cut=(left_edge+right_edge)/2
        if gap<width*.012 or not width*.12<cut<width*.88:continue
        left=[line for line in lines if line['bbox'][2]<=cut]
        right=[line for line in lines if line['bbox'][0]>=cut]
        spanning=[line for line in lines if line not in left and line not in right]
        if min(len(left),len(right))<2 or len(spanning)>len(lines)*.2:continue
        score=gap*min(len(left),len(right))/(1+len(spanning))
        candidates.append((score,left,right,spanning))
    if not candidates:return whitespace_groups(lines,width,height)
    _,left,right,spanning=max(candidates,key=lambda candidate:candidate[0])
    return text_columns(left,width,height,depth+1)+text_columns(right,width,height,depth+1)+whitespace_groups(spanning,width,height)


def reflow_structure(structure, wraps):
    """Change text grouping only; keep source images and table/figure identities intact."""
    lines=structure['ocr_lines'];width=structure['width'];height=structure['height']
    blocks=[b for b in structure['blocks'] if b['kind']!='text']
    taken={n for b in blocks for n in b['line_ids']}
    remaining=[line for line in lines if line['id'] not in taken]
    joins={d['line_index'] for d in wraps if d.get('action')=='join' and d.get('next_line_index')==d['line_index']+1}
    for index,group in enumerate(text_columns(remaining,width,height),1):
        if not group:continue
        text=''
        for i,line in enumerate(group):
            text+=line['text'].strip()
            text+='' if i+1<len(group) and line['id'] in joins and group[i+1]['id']==line['id']+1 else '\n'
        blocks.append({'id':f't{index:03}','kind':'text','bbox':union([l['bbox'] for l in group]),
                       'text':text.strip(),'line_ids':[l['id'] for l in group],
                       'image':'images/page.jpg','requires_review':True})
    structure['blocks']=[b for group in whitespace_groups(blocks,width,height) for b in group]
    for formula in structure['formulas']:
        parents=[b for b in structure['blocks'] if intersection_ratio(formula['bbox'],b['bbox'])>.6]
        formula['parent_block']=parents[0]['id'] if parents else None
        formula['table_cells']=[]
        for parent in parents:
            if parent['kind']=='table':
                formula['table_cells']=[{'row':c['row'],'column':c['column']} for c in parent['cells']
                                        if intersection_ratio(formula['bbox'],c['bbox'])>.5]
    assigned=[n for b in structure['blocks'] for n in b['line_ids']]
    if sorted(assigned)!=list(range(len(lines))):raise ValueError('Reflow line coverage failed')
    structure['text_layout_version']=2
    return structure
