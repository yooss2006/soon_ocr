import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from PIL import Image
import numpy as np
from reportlab.pdfgen import canvas
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parents[1]/'scripts/runtime')]
from ocr_app.structure import build_structure,document_tables,table_markdown,table_html
from ocr_app.ai_package import export_package
from ocr_app.storage import Paused,UserError,sha256_file


def line(text,x,y,w=.25,h=.025):
    return {'text':text,'box':[x,1-y-h,w,h],'confidence':.99,'chars':[]}


class FakeAnalyzer:
    calls=[]
    def __init__(self,*args):pass
    def analyze(self,source,n,geometry,work):
        self.calls.append(n)
        path=work/'page.png';Image.new('RGB',(600,800),'white').save(path)
        return {'ocr':{'lines':[line(f'Page {n} text',.1,.1)]},'documents':[],'wraps':[],
                'regions':[],'formulas':[],'alerts':[],'image_path':str(path),'image_size':[600,800]}


class StructureTests(unittest.TestCase):
    def test_columns_do_not_interleave_or_drop_lines(self):
        lines=[line('left1',.1,.1),line('right1',.65,.1),line('left2',.1,.15),line('right2',.65,.15)]
        s=build_structure({'ocr':{'lines':lines}},np.full((1000,800,3),255,np.uint8))
        texts=[b['text'] for b in s['blocks']]
        self.assertEqual('\n'.join(texts),'left1\nleft2\nright1\nright2')
        self.assertEqual(s['coverage'],{'ocr_line_count':4,'assigned_line_count':4})

    def test_spanning_heading_does_not_mix_columns(self):
        lines=[line('Heading',.1,.02,.8)]
        for i in range(3):
            lines.extend([line(f'side {i}',.1,.1+i*.04),line(f'body {i}',.65,.1+i*.04)])
        s=build_structure({'ocr':{'lines':lines}},np.full((1000,800,3),255,np.uint8))
        for block in s['blocks']:
            text=block.get('text','')
            self.assertFalse('side' in text and 'body' in text)
        self.assertEqual(s['coverage']['assigned_line_count'],7)

    def test_confirmed_join_only_and_no_text_rewriting(self):
        lines=[line('데이',.1,.1),line('터',.1,.14),line('오탈쟈 그대로',.1,.18)]
        data={'ocr':{'lines':lines},'wraps':[{'line_index':0,'next_line_index':1,'action':'join'}]}
        s=build_structure(data,np.full((1000,800,3),255,np.uint8))
        self.assertIn('데이터\n오탈쟈 그대로',s['blocks'][0]['text'])

    def test_table_spans_and_source_text_are_preserved(self):
        cell=lambda text,r,c,rs=1,cs=1:{'text':text,'box':[.1,.6,.3,.1],'row_range':[r,r+rs],'column_range':[c,c+cs]}
        doc=[{'tables':[[[cell('merged',0,0,1,2)],[cell('A',1,0),cell('<script>x</script>',1,1)]]]}]
        table=document_tables(doc,800,1000)[0]
        md=table_markdown(table)
        self.assertIn('colspan="2"',md)
        self.assertIn('&lt;script&gt;',md)
        self.assertTrue(table['valid_spans'])

    def test_merged_table_preserves_empty_columns(self):
        table={'valid_spans':True,'cells':[
            {'row':0,'column':0,'rowspan':2,'colspan':1,'text':'merged'},
            {'row':0,'column':2,'rowspan':1,'colspan':1,'text':'right'},
            {'row':1,'column':1,'rowspan':1,'colspan':2,'text':'bottom'}]}
        content=table_html(table)
        self.assertIn('merged</td><td></td><td rowspan="1" colspan="1">right',content)
        self.assertIn('colspan="2">bottom',content)

    def test_colored_label_column_keeps_row_relationships(self):
        image=np.full((1000,800,3),255,np.uint8)
        image[200:720,100:250]=(180,70,10)
        lines=[]
        for label,value,y in [('A','description A',.25),('B','description B',.4),('C','description C',.56)]:
            lines.extend([line(label,.14,y,.1),line(value,.34,y,.5)])
        s=build_structure({'ocr':{'lines':lines}},image)
        table=next(b for b in s['blocks'] if b['kind']=='table')
        cells={(c['row'],c['column']):c['text'] for c in table['cells']}
        self.assertEqual(cells[(1,0)],'B')
        self.assertEqual(cells[(1,1)],'description B')
        self.assertEqual(len(table['line_ids']),6)
        self.assertTrue(table['requires_review'])

    def test_table_ocr_coverage_and_formula_uncertainty(self):
        data={'ocr':{'lines':[line('table OCR',.1,.3)]},
              'documents':[{'tables':[[[{'text':'cell','box':[.09,.65,.4,.1],'row_range':[0,1],'column_range':[0,1]}]]]}],
              'formulas':[{'bbox':[10,10,100,50],'latex':'x^2','complete':True,'confidence':1}]}
        s=build_structure(data,np.full((1000,800,3),255,np.uint8))
        self.assertEqual(s['blocks'][0]['line_ids'],[0])
        self.assertEqual(s['blocks'][0]['ocr_text'],'table OCR')
        self.assertTrue(s['formulas'][0]['requires_review'])
        self.assertIn('parent_block',s['formulas'][0])


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.source=self.root/'source.pdf';c=canvas.Canvas(str(self.source))
        for n in (1,2):c.drawString(72,700,f'Page {n} text');c.showPage()
        c.save();self.stop=threading.Event();FakeAnalyzer.calls=[]
        self.resources=SimpleNamespace(fingerprint='test',check=lambda:None)
    def tearDown(self):self.tmp.cleanup()
    def export(self,out,emit=None):
        return export_package(self.source,self.root/out,self.stop,self.resources,self.root/'cache',emit,analyzer_factory=FakeAnalyzer)

    def test_package_has_complete_evidence_and_reuses_checkpoints(self):
        self.export('one')
        p=self.root/'one'
        m=json.loads((p/'manifest.json').read_text())
        self.assertEqual(m['page_count'],2)
        self.assertEqual(FakeAnalyzer.calls,[1,2])
        for name,digest in m['files'].items():self.assertEqual(sha256_file(p/name),digest)
        self.assertTrue((p/'pages/00001/images/page.jpg').is_file())
        self.assertIn('pages/00001/images/page.jpg',(p/'content.md').read_text())
        self.assertIn('직접 첨부',(p/'ChatGPT에 첨부하기.txt').read_text())
        self.export('two');self.assertEqual(FakeAnalyzer.calls,[1,2])
        cache=next((self.root/'cache').iterdir())
        (cache/'00001/images/page.jpg').write_bytes(b'broken')
        self.export('three');self.assertEqual(FakeAnalyzer.calls,[1,2,1])

    def test_pause_keeps_checkpoint_without_partial_output(self):
        def emit(e):
            if e.get('done')==1 and 'cached' in e:self.stop.set()
        with self.assertRaises(Paused):self.export('paused',emit)
        self.assertFalse((self.root/'paused').exists())
        self.stop.clear();self.export('resumed')
        self.assertEqual(FakeAnalyzer.calls,[1,2])
        with self.assertRaises(UserError):self.export('resumed')


if __name__=='__main__':unittest.main()
