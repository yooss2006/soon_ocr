"""The UI must distinguish OCR-required PDFs from inspection failures."""
import contextlib
import io
import json
import unittest
from unittest.mock import patch
from ocr_app.storage import NeedsOCR, UserError
from ocr_app import worker


class WorkflowTests(unittest.TestCase):
    def run_check(self, failure=None):
        output = io.StringIO()
        def inspect(*args, **kwargs):
            if failure:
                raise failure
            kwargs.get('emit', args[3])({'event': 'finished', 'status': 'ai_ready', 'page_count': 3})
        with patch('sys.argv', ['ocr-worker', '--input', 'input.pdf', '--scope', 'ai-check']), \
             patch.object(worker.signal, 'signal'), \
             patch.object(worker, 'export_markdown', side_effect=inspect), \
             contextlib.redirect_stdout(output):
            code = worker.main()
        return code, json.loads(output.getvalue().strip())

    def test_searchable_pdf_skips_ocr(self):
        code, event = self.run_check()
        self.assertEqual(code, 0)
        self.assertEqual(event['status'], 'ai_ready')

    def test_ocr_requirement_is_a_route_not_an_error(self):
        code, event = self.run_check(NeedsOCR('OCR 필요'))
        self.assertEqual(code, 0)
        self.assertEqual(event['event'], 'finished')
        self.assertEqual(event['status'], 'ocr_required')

    def test_unreadable_pdf_does_not_route_to_ocr(self):
        code, event = self.run_check(UserError('암호를 해제하세요.'))
        self.assertEqual(code, 2)
        self.assertEqual(event['event'], 'error')
        self.assertNotIn('status', event)
