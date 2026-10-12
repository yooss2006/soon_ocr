"""Small per-page review reports and a job index, with escaped source text."""
import html
import json
from pathlib import Path
from PIL import Image
from .storage import atomic_bytes

NAMES = {'possible_missing_table':'표 누락 의심','empty_cell_with_ocr_evidence':'빈 숫자·문자 칸 의심',
         'formula':'수식 확인','formula_failed':'수식 인식 미완료','review_analysis_failed':'검토 미완료',
         'uncertain_wrap':'줄 경계 확인','no_text':'인식된 글자 없음','page_failed':'페이지 처리 실패'}
REASONS = {'quoted_math_bound':'수식 경계의 따옴표','nested_script_without_base':'기준 글자 없는 첨자',
           'unbalanced_braces':'괄호 구조 불일치','phantom_content':'숨겨진 수식 명령',
           'low_generation_confidence':'낮은 인식 신뢰도','generation_truncated':'수식 생성 중단','empty_formula':'빈 수식'}
STYLE = "body{font:16px/1.6 system-ui,sans-serif;max-width:960px;margin:32px auto;padding:0 20px;color:#243246}article,details{border:1px solid #d8dee8;padding:16px;border-radius:10px;margin:16px 0}img{max-width:100%;height:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f5f8;padding:12px}a{color:#1465c0}small{color:#59687b}table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #ddd;text-align:left;padding:8px}summary{cursor:pointer;font-weight:600}"

def document(title, body):
    return f"<!doctype html><html lang='ko'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{html.escape(title)}</title><style>{STYLE}</style><h1>{html.escape(title)}</h1>{body}</html>"

def page_report(directory, number, alerts, image_path=None, image_size=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    body = '<p>경고는 오류 의심 표시입니다. 경고가 없어도 정확성을 보장하지 않습니다. 수식 인식 결과는 PDF에 자동으로 대체하지 않았습니다.</p>'
    normal, urgent = [], []
    image = None
    if image_path:
        with Image.open(image_path) as source: image = source.convert('RGB')
    for index, item in enumerate(alerts):
        title = NAMES.get(item['kind'], item['kind'])
        reason = ' · '.join(REASONS.get(r,r) for r in item.get('reasons',[]))
        if item['kind']=='possible_missing_table': reason = '표 인식 결과 밖에서 숫자 행과 가로선을 찾았습니다.'
        elif item['kind']=='empty_cell_with_ocr_evidence': reason = '표에서는 빈칸이지만 글자 인식 결과가 있습니다.'
        elif item['kind']=='formula' and not reason: reason = '자동 규칙 경고 없음 · 아직 검수하지 않은 수식입니다.'
        else: reason = reason or item.get('reason','원본을 확인하세요.')
        card = f'<article><h3>{html.escape(title)}</h3><p>{html.escape(reason)}</p>'
        if image is not None and 'bbox' in item and item.get('priority')!='normal':
            x1,y1,x2,y2 = item['bbox']
            crop = image.crop((max(0,int(x1)-10),max(0,int(y1)-10),min(image.width,int(x2)+11),min(image.height,int(y2)+11)))
            if crop.width and crop.height:
                crop.thumbnail((850,450))
                name = f'item-{index:03}.png'; crop.save(directory/name)
                card += f'<img src="{name}" alt="원본 영역">'
        if item.get('latex'): card += '<pre>'+html.escape(item['latex'])+'</pre>'
        if item.get('evidence'): card += '<p>다른 인식 결과: '+html.escape(' · '.join(map(str,item['evidence'])))+'</p>'
        card += '</article>'
        (normal if item.get('priority')=='normal' else urgent).append(card)
    body += ''.join(urgent)
    if normal: body += f'<details><summary>미검수 수식 {len(normal)}개</summary>'+''.join(normal)+'</details>'
    if not alerts: body += '<p>이 페이지에서 자동 검토 항목을 찾지 못했습니다.</p>'
    atomic_bytes(directory/'review.html', document(f'PDF {number}쪽 검토',body).encode())

def index_report(job_dir, manifest, selected):
    root = Path(job_dir)
    body = '<p>'+html.escape(manifest['source_name'])+'</p>'
    body += '<p>표·수식은 원본과 함께 확인하세요. 자동 검토는 모든 오류를 찾지 못합니다. 이미 글자가 있는 페이지는 추가 OCR 없이 보존합니다.</p>'
    body += '<table><tr><th>PDF 쪽</th><th>처리 상태</th><th>확인 항목</th></tr>'
    names = {'complete':'글자 인식 완료','no_text':'글자 없음 · 확인 필요','existing_text':'기존 글자 보존','failed':'미처리 · 원본 포함'}
    for number in selected:
        record = manifest['pages'].get(str(number),{})
        state = record.get('status','pending')
        count = record.get('review_count',0)
        link = f'pages/{number:05}/review.html'
        label = f'<a href="{link}">{count}개 확인</a>' if (root/link).is_file() else '-'
        body += f'<tr><td>{number}</td><td>{html.escape(names.get(state,"대기"))}</td><td>{label}</td></tr>'
    body += '</table>'
    atomic_bytes(root/'report.html',document('OCR 검토 보고서',body).encode())
    return root/'report.html'
