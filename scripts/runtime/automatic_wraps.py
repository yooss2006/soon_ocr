"""Infer Korean word continuations from geometry and a local language model.

Only join line boundaries. Never respell or rewrite OCR characters. Uncertain
boundaries remain unchanged and are returned for review. No page-specific rules.
"""
import re
from kiwipiepy import Kiwi


def cell_memberships(lines,documents):
    memberships=[set() for _ in lines]
    cells=[c for d in documents for t in d['tables'] for row in t for c in row]
    for index,line in enumerate(lines):
        x,y,w,h=line['box'];cx,cy=x+w/2,y+h/2
        for ci,cell in enumerate(cells):
            bx,by,bw,bh=cell['box']
            if bx<=cx<=bx+bw and by<=cy<=by+bh:memberships[index].add(ci)
    return memberships


class WrapAnalyzer:
    def __init__(self,margin=1.5):
        self.kiwi=Kiwi(num_workers=1)
        self.margin=margin

    def analyze(self,lines,documents=()):
        cells=cell_memberships(lines,documents)
        corpus=' '.join(line['text'] for line in lines)
        decisions=[]
        for index in range(len(lines)-1):
            a,b=lines[index:index+2]
            left,right=a['text'].rstrip(),b['text'].lstrip()
            if not re.search('[가-힣]$',left) or not re.match('[가-힣]',right):continue
            x,y,w,h=a['box'];nx,ny,nw,nh=b['box']
            gap=(y+h/2)-(ny+nh/2)
            if not (.65*max(h,nh)<gap<2.3*max(h,nh)):continue
            if abs(x-nx)>max(.025,w*.12) or w<.10:continue
            if (cells[index] or cells[index+1]) and not cells[index].intersection(cells[index+1]):continue
            # Use bounded surrounding context, without adding a vocabulary for this book.
            context_left=left[-120:];context_right=right[:120]
            separated=self.kiwi.analyze(context_left+' '+context_right,top_n=1)[0]
            joined=self.kiwi.analyze(context_left+context_right,top_n=1)[0]
            delta=joined[1]-separated[1];boundary=len(context_left)
            crossing=any(t.start<boundary<t.start+t.len for t in joined[0])
            after=next((t for t in joined[0] if t.start>=boundary),None)
            attached=after is not None and (after.tag.startswith(('E','J')) or after.tag in ('XSN','XSV','XSA'))
            tail=re.search('[가-힣]+$',left).group();head=re.match('[가-힣]+',right).group()
            # Resolve a language-model tie only with a complete-word witness
            # elsewhere on this page; do not hardcode technical vocabulary.
            witnessed=abs(delta)<.01 and len(tail)<=2 and bool(re.search(r'(?<![가-힣])'+re.escape(tail+head)+r'(?![가-힣])',corpus))
            action='join' if (delta>=self.margin and (crossing or attached)) or witnessed else ('review' if delta>0 else 'keep_space')
            decisions.append({'line_index':index,'next_line_index':index+1,'action':action,
                              'score_margin':round(delta,4),'crossing_morpheme':crossing,'attached_suffix':attached,
                              'complete_word_witness':witnessed,
                              'left':left,'right':right,'box':a['box'],'next_box':b['box']})
        return decisions
