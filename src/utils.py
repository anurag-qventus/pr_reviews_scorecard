import io
import json
import os
import re
import unicodedata
from datetime import date


def extract_score(text: str, period: str) -> float | None:
    """
    Finds the 'Quality rating for the {period} period' line, then scans
    the next 5 lines for a score in 'X/10' format.
    """
    lines = text.split('\n')
    for i, line in enumerate(lines):
        if re.search(rf'quality rating for the {period} period', line, re.IGNORECASE):
            for j in range(i + 1, min(i + 6, len(lines))):
                match = re.search(r'(\d+)\s*/\s*10', lines[j])
                if match:
                    return float(match.group(1))
    return None

from fpdf import FPDF
from fpdf.enums import XPos, YPos


# Explicit replacements for common Unicode chars the LLM often emits
_UNICODE_MAP = {
    '\u2013': '-',    # en dash
    '\u2014': '--',   # em dash
    '\u2018': "'",    # left single quote
    '\u2019': "'",    # right single quote
    '\u201c': '"',    # left double quote
    '\u201d': '"',    # right double quote
    '\u2022': '-',    # bullet
    '\u2026': '...', # ellipsis
    '\u00a0': ' ',   # non-breaking space
    '\u2192': '->',  # right arrow
    '\u2190': '<-',  # left arrow
    '\u00b7': '-',   # middle dot
}


def _ascii_safe(text: str) -> str:
    """
    Replaces Unicode characters outside Latin-1 with ASCII equivalents
    so fpdf2's built-in Helvetica font can render them without errors.
    """
    for char, replacement in _UNICODE_MAP.items():
        text = text.replace(char, replacement)
    result = []
    for char in text:
        if ord(char) < 256:
            result.append(char)
        else:
            normalized = unicodedata.normalize('NFKD', char)
            ascii_char = normalized.encode('ascii', 'ignore').decode('ascii')
            result.append(ascii_char if ascii_char else '?')
    return ''.join(result)

def _strip_inline_md(text: str) -> str:
    """Remove inline markdown markers: **bold**, *italic*, `code`, leading -/* bullets."""
    text = re.sub(r'\*{1,2}(.*?)\*{1,2}', r'\1', text)  # bold / italic
    text = re.sub(r'`([^`]*)`', r'\1', text)              # inline code
    return text.strip()


def _render_report(pdf: FPDF, text: str) -> None:
    """
    Renders sanitized plain-text markdown to the PDF using multi_cell only.
    No HTML parsing — avoids all unicode/encoding issues from write_html.
    """
    page_w = pdf.w - pdf.l_margin - pdf.r_margin

    for line in text.splitlines():
        stripped = line.strip()

        if not stripped:
            pdf.ln(2)
            continue

        # Section heading: ### **Title** or ## Title
        if stripped.startswith('#'):
            content = re.sub(r'^#+\s*', '', stripped)
            content = _strip_inline_md(content)
            pdf.set_font("Helvetica", "B", 12)
            pdf.set_text_color(31, 56, 100)
            pdf.ln(3)
            pdf.multi_cell(page_w, 6, content)
            pdf.ln(1)
            continue

        # Bullet: lines starting with - or *
        if re.match(r'^[-*]\s', stripped):
            content = _strip_inline_md(stripped[2:])
            pdf.set_font("Helvetica", "", 10)
            pdf.set_text_color(26, 26, 26)
            pdf.set_x(pdf.l_margin + 6)
            pdf.multi_cell(page_w - 6, 5, f"- {content}")
            continue

        # Numbered item: 1) or 1.
        if re.match(r'^\d+[.)]\s', stripped):
            num, _, rest = stripped.partition(' ')
            content = _strip_inline_md(rest)
            pdf.set_font("Helvetica", "B", 10)
            pdf.set_text_color(26, 26, 26)
            pdf.ln(2)
            pdf.multi_cell(page_w, 5, f"{num} {content}")
            continue

        # Default: regular paragraph line
        content = _strip_inline_md(stripped)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(26, 26, 26)
        pdf.multi_cell(page_w, 5, content)


def generate_pdf_bytes(
    user_label: str,
    date_range_label: str,
    current_from: date,
    current_to: date,
    previous_from: date,
    previous_to: date,
    current_score,
    previous_score,
    report_text: str,
) -> bytes:
    """
    Converts the comparative analysis report to a PDF and returns the raw bytes.
    Pure Python — uses fpdf2 for layout and markdown for body HTML rendering.
    """
    # Sanitize all text to Latin-1 safe before any fpdf2 call
    user_label       = _ascii_safe(user_label)
    date_range_label = _ascii_safe(date_range_label)
    report_text      = _ascii_safe(report_text)

    pdf = FPDF()
    pdf.set_margins(20, 20, 20)
    pdf.add_page()

    # ---- Title ----
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(31, 56, 100)
    pdf.cell(0, 10, "PR Reviews Score Card", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_draw_color(31, 56, 100)
    pdf.set_line_width(0.5)
    pdf.line(20, pdf.get_y(), 190, pdf.get_y())
    pdf.ln(3)

    # ---- Meta info ----
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(0, 6, f"Developer: {user_label}   |   Period: {date_range_label}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 6, f"Current: {current_from} to {current_to}   |   Previous: {previous_from} to {previous_to}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)

    # ---- Score boxes ----
    cur_str  = f"{current_score:.0f} / 10"  if current_score  is not None else "N/A"
    prev_str = f"{previous_score:.0f} / 10" if previous_score is not None else "N/A"
    delta_str = ""
    if current_score is not None and previous_score is not None:
        delta = current_score - previous_score
        delta_str = f"Change: {delta:+.0f} pts"

    box_y = pdf.get_y()
    for label, value, note in [
        ("Current Period Score",  cur_str,  ""),
        ("Previous Period Score", prev_str, delta_str),
    ]:
        x = 20 if label.startswith("Current") else 110
        pdf.set_fill_color(240, 245, 251)
        pdf.set_draw_color(200, 216, 232)
        pdf.rect(x, box_y, 85, 22, style="FD")
        pdf.set_xy(x + 3, box_y + 2)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(79, 5, label)
        pdf.set_xy(x + 3, box_y + 8)
        pdf.set_font("Helvetica", "B", 16)
        pdf.set_text_color(31, 56, 100)
        pdf.cell(79, 8, value)
        if note:
            pdf.set_xy(x + 3, box_y + 17)
            pdf.set_font("Helvetica", "", 8)
            pdf.set_text_color(100, 100, 100)
            pdf.cell(79, 4, note)

    pdf.set_y(box_y + 26)
    pdf.set_draw_color(200, 200, 200)
    pdf.set_line_width(0.3)
    pdf.line(20, pdf.get_y(), 190, pdf.get_y())
    pdf.ln(4)

    # ---- Report body (direct text rendering — no HTML parsing) ----
    _render_report(pdf, report_text)

    buf = io.BytesIO()
    pdf.output(buf)
    return buf.getvalue()


def save_json(filename, data):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    print(f"script_dir: {script_dir}")
    file_path = os.path.abspath(os.path.join(script_dir, "..", filename))
    print(f"file_path: {file_path}")
    with open(filename, 'w') as f:
        json.dump(data, f, indent=4)


def count_words(filename: str) -> int:
    with open(filename, 'r') as f:
        text = f.read()
        words = text.split()
        count = len(words)
        return count

