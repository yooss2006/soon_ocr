import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from reportlab.pdfgen import canvas
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ocr_app.ai_export import export_markdown
from PIL import Image
from ocr_app.storage import UserError, Paused, sha256_file


class AIExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root/'source.pdf'
        c = canvas.Canvas(str(self.source))
        for text in ['First page', '', 'Third page']:
            if text: c.drawString(72, 700, text)
            c.showPage()
        c.save()
        self.stop = threading.Event()

    def tearDown(self): self.tmp.cleanup()

    def test_single_and_split_preserve_pages_and_mark_missing(self):
        single = self.root/'all.md'
        result = export_markdown(self.source, single, self.stop)
        self.assertEqual(result['missing_pages'], [2])
        self.assertEqual(result['page_count'], 3)
        text = single.read_text()
        self.assertIn('First page', text)
        self.assertIn('텍스트 없음', text)
        self.assertEqual(text.count('## PDF '), 3)
        split = self.root/'parts'
        result = export_markdown(self.source, split, self.stop, split_pages=2)
        self.assertEqual(result['file_count'], 2)
        self.assertEqual(sorted(p.name for p in split.iterdir()), ['pages-0001-0002.md', 'pages-0003-0003.md'])
        self.assertNotIn('## PDF 3쪽', (split/'pages-0001-0002.md').read_text())
        self.assertIn('## PDF 3쪽', (split/'pages-0003-0003.md').read_text())

    def test_protection_and_cancel_leave_existing_output_intact(self):
        out = self.root/'all.md'; out.write_text('keep')
        with self.assertRaises(UserError): export_markdown(self.source, out, self.stop)
        with self.assertRaises(UserError): export_markdown(self.source, self.source, self.stop, overwrite=True)
        self.stop.set()
        with self.assertRaises(Paused): export_markdown(self.source, out, self.stop, overwrite=True)
        self.assertEqual(out.read_text(), 'keep')
        self.stop.clear()
        folder = self.root/'parts';folder.mkdir()
        with self.assertRaises(UserError): export_markdown(self.source, folder, self.stop, split_pages=2, overwrite=True)

    def test_completed_cache_reused_and_corrupt_cache_blocked(self):
        job = self.root/'job';job.mkdir()
        (job/'source.pdf').write_bytes(self.source.read_bytes())
        page = job/'pages/00001';page.mkdir(parents=True)
        analysis = page/'analysis.json'
        analysis.write_text(json.dumps({'ocr':{'lines':[{'text':'데이'}, {'text':'터'}, {'text':'다음 문장'}]},
                                        'wraps':[{'line_index':0, 'next_line_index':1, 'action':'join'}],
                                        'alerts':[{'kind':'formula'}]}))
        state = {'source_name':self.source.name,'source_path':str(self.source),
                 'source_sha256':sha256_file(job/'source.pdf'),'page_count':3,
                 'pages':{'1':{'status':'complete','analysis_sha256':sha256_file(analysis)}}}
        for n in (2,3):
            directory=job/'pages'/f'{n:05}';directory.mkdir()
            data={'ocr':{'lines':[]}} if n==2 else {'status':'existing_text'}
            path=directory/'analysis.json';path.write_text(json.dumps(data))
            state['pages'][str(n)]={'status':'no_text' if n==2 else 'existing_text','analysis_sha256':sha256_file(path)}
        (job/'job.json').write_text(json.dumps(state))
        original = (job/'job.json').read_bytes()
        out = self.root/'cache.md'
        export_markdown(None, out, self.stop, job=job)
        self.assertIn('데이터\n다음 문장', out.read_text())
        self.assertNotIn('First page', out.read_text())
        self.assertEqual((job/'job.json').read_bytes(), original)
        analysis.write_text('{}')
        with self.assertRaises(UserError): export_markdown(None, out, self.stop, job=job, overwrite=True)
        self.assertIn('데이터', out.read_text())
        with self.assertRaises(UserError): export_markdown(None, job/'bad.md', self.stop, job=job)

    def test_mixed_pdf_requires_ocr_even_with_searchable_first_page(self):
        image=self.root/'scan.png';Image.new('RGB',(100,100),'white').save(image)
        source=self.root/'mixed.pdf'
        c=canvas.Canvas(str(source));c.drawString(72,700,'Searchable');c.showPage()
        c.drawImage(str(image),0,0,100,100);c.showPage();c.save()
        out=self.root/'mixed.md'
        with self.assertRaises(UserError): export_markdown(source,out,self.stop)
        with self.assertRaises(UserError):
            export_markdown(source,None,self.stop,inspect_only=True,verify_no_text=lambda *_:False)
        self.assertFalse(out.exists())
        result=export_markdown(source,None,self.stop,inspect_only=True,verify_no_text=lambda *_:True)
        self.assertEqual(result['status'],'ai_ready')

    def test_incomplete_and_failed_jobs_cannot_export(self):
        job=self.root/'job';job.mkdir();(job/'source.pdf').write_bytes(self.source.read_bytes())
        state={'source_name':self.source.name,'source_path':str(self.source),
               'source_sha256':sha256_file(job/'source.pdf'),'page_count':3,'pages':{}}
        for status in ('sample_ready','paused','complete_with_errors','complete'):
            state['status']=status
            (job/'job.json').write_text(json.dumps(state))
            with self.assertRaises(UserError): export_markdown(None,self.root/'blocked.md',self.stop,job=job)
        self.assertFalse((self.root/'blocked.md').exists())

    def test_cancel_during_extraction_publishes_nothing(self):
        def emit(event):
            if event.get('done') == 1: self.stop.set()
        out = self.root/'parts'
        with self.assertRaises(Paused): export_markdown(self.source, out, self.stop, emit, split_pages=2)
        self.assertFalse(out.exists())

    def test_image_only_requires_ocr(self):
        source = self.root/'blank.pdf'
        c = canvas.Canvas(str(source));c.showPage();c.save()
        out = self.root/'blank.md'
        with self.assertRaises(UserError): export_markdown(source, out, self.stop)
        self.assertFalse(out.exists())


if __name__ == '__main__': unittest.main()
