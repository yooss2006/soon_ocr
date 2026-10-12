"""Invisible OCR overlays in the displayed CropBox coordinate system."""
import io
import re
from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from pypdf import PdfReader, PdfWriter, Transformation
from pypdf.generic import DecodedStreamObject, NameObject

FONT = 'OCRUnicode'
FONT_PATH = Path('/System/Library/Fonts/Supplemental/Arial Unicode.ttf')

def ensure_font():
    if FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(FONT, str(FONT_PATH)))

def display_geometry(page):
    box = page.cropbox
    x, y = float(box.left), float(box.bottom)
    w, h = float(box.width), float(box.height)
    rotation = int(page.rotation or 0) % 360
    if rotation not in (0, 90, 180, 270) or w <= 0 or h <= 0:
        raise ValueError('지원하지 않는 페이지 크기 또는 회전입니다.')
    transforms = {0: (1, 0, 0, 1, x, y), 90: (0, 1, -1, 0, x+w, y),
                  180: (-1, 0, 0, -1, x+w, y+h), 270: (0, -1, 1, 0, x, y+h)}
    return (h, w, transforms[rotation]) if rotation in (90, 270) else (w, h, transforms[rotation])

def word_boxes(chars, start, end):
    position = 0
    for char in chars:
        following = position + len(char['text'])
        if position < end and following > start and char['box'][2] > 0 and char['box'][3] > 0:
            yield char['box']
        position = following

def create_overlay(width, height, lines, joined_indexes):
    ensure_font()
    ascent, descent = pdfmetrics.getAscentDescent(FONT)
    data = io.BytesIO()
    c = canvas.Canvas(data, pagesize=(width, height), pageCompression=1, invariant=1)
    for index, line in enumerate(lines):
        if ''.join(char['text'] for char in line['chars']) != line['text']:
            raise ValueError('글자 위치 정보가 완전하지 않습니다.')
        size = line['box'][3] * height * 1000 / (ascent-descent)
        if size <= 0: continue
        text = c.beginText()
        text.setTextRenderMode(3)
        text.setFont(FONT, size)
        baseline = line['box'][1] * height - descent * size / 1000
        for match in re.finditer(r'\S+', line['text']):
            boxes = list(word_boxes(line['chars'], match.start(), match.end()))
            if not boxes: continue
            left = min(b[0] for b in boxes)
            right = max(b[0]+b[2] for b in boxes)
            natural = pdfmetrics.stringWidth(match.group(), FONT, size)
            if natural <= 0: continue
            text.setHorizScale((right-left)*width/natural*100)
            text.setTextOrigin(left*width, baseline)
            suffix = ' ' if match.end() < len(line['text']) else ('\u00ad' if index in joined_indexes else '')
            text.textOut(match.group()+suffix)
        c.drawText(text)
    c.showPage()
    c.save()
    return data.getvalue()

def export_pdf(snapshot, selected_pages, overlays, destination):
    """Full exports preserve the source catalog, outlines, annotations and page order."""
    with Path(snapshot).open('rb') as stream:
        reader = PdfReader(stream)
        whole_document = selected_pages == list(range(1, len(reader.pages)+1))
        writer = PdfWriter()
        if whole_document:
            writer.clone_document_from_reader(reader)
        else:
            for number in selected_pages: writer.add_page(reader.pages[number-1])
        for output_index, source_number in enumerate(selected_pages):
            overlay_path = overlays.get(source_number)
            if not overlay_path: continue
            page = writer.pages[output_index]
            # Source PDFs may share a content stream between pages. Detach it
            # before merging, otherwise one page's OCR can leak into another.
            original = page.get_contents()
            detached = DecodedStreamObject()
            detached.set_data(original.get_data() if original is not None else b'')
            page[NameObject('/Contents')] = writer._add_object(detached)
            _, _, matrix = display_geometry(page)
            layer = PdfReader(str(overlay_path)).pages[0]
            page.merge_transformed_page(layer, Transformation(matrix), over=True, expand=False)
        writer.add_metadata({'/Producer': 'OCR Helper 1.0', '/Subject': 'Searchable text added locally. Review flags do not guarantee complete OCR accuracy.'})
        with Path(destination).open('wb') as result: writer.write(result)
