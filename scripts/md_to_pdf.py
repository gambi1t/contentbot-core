"""Конвертер Markdown → PDF на reportlab + Arial (поддержка кириллицы).

Использование:
    python scripts/md_to_pdf.py docs/SMM_GUIDE.md docs/SMM_GUIDE.pdf

Поддерживает:
- Заголовки H1-H3 (по #)
- Параграфы и пустые строки
- Маркированные списки (-, *, цифровые)
- Таблицы (| col | col |)
- Жирный (**...**), курсив (*...*)
- Code-inline (`...`)
- Цитаты (>)
- Горизонтальный divider (---)

Emoji удаляются (Arial их не рендерит).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable, ListFlowable, ListItem, PageBreak, Paragraph,
    SimpleDocTemplate, Spacer, Table, TableStyle,
)

# ─── Шрифты (Arial для кириллицы) ────────────────────────────────────────

_WIN_FONTS = Path("C:/Windows/Fonts")
_FONT_REGULAR = _WIN_FONTS / "arial.ttf"
_FONT_BOLD = _WIN_FONTS / "arialbd.ttf"
_FONT_ITALIC = _WIN_FONTS / "ariali.ttf"
_FONT_BOLD_ITALIC = _WIN_FONTS / "arialbi.ttf"

if _FONT_REGULAR.exists():
    pdfmetrics.registerFont(TTFont("Arial", str(_FONT_REGULAR)))
    pdfmetrics.registerFont(TTFont("Arial-Bold", str(_FONT_BOLD)))
    pdfmetrics.registerFont(TTFont("Arial-Italic", str(_FONT_ITALIC)))
    pdfmetrics.registerFont(TTFont("Arial-BoldItalic", str(_FONT_BOLD_ITALIC)))
    pdfmetrics.registerFontFamily(
        "Arial", normal="Arial", bold="Arial-Bold",
        italic="Arial-Italic", boldItalic="Arial-BoldItalic",
    )
    FONT = "Arial"
else:
    FONT = "Helvetica"   # fallback (без кириллицы — лучше иметь TTF)

# ─── Стили ───────────────────────────────────────────────────────────────

ACCENT = HexColor("#0F4C81")     # тёмно-синий — заголовки
LIGHT_BG = HexColor("#F2F4F8")   # светло-серый — фон таблиц
MUTED = HexColor("#6B7280")      # серый — meta-текст

styles = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=styles["Heading1"], fontName=f"{FONT}-Bold",
                    fontSize=20, leading=26, textColor=ACCENT, spaceAfter=12,
                    spaceBefore=16)
H2 = ParagraphStyle("H2", parent=styles["Heading2"], fontName=f"{FONT}-Bold",
                    fontSize=15, leading=20, textColor=ACCENT, spaceAfter=8,
                    spaceBefore=14)
H3 = ParagraphStyle("H3", parent=styles["Heading3"], fontName=f"{FONT}-Bold",
                    fontSize=12, leading=16, textColor=ACCENT, spaceAfter=6,
                    spaceBefore=10)
BODY = ParagraphStyle("Body", parent=styles["BodyText"], fontName=FONT,
                      fontSize=10.5, leading=15, spaceAfter=6, alignment=0)
BODY_MUTED = ParagraphStyle("BodyMuted", parent=BODY, textColor=MUTED,
                            fontSize=9, leading=12)
QUOTE = ParagraphStyle("Quote", parent=BODY, leftIndent=20, textColor=MUTED,
                       fontName=f"{FONT}-Italic", borderColor=ACCENT,
                       borderPadding=4, leftBorderColor=ACCENT)
TABLE_HDR = ParagraphStyle("TableHdr", parent=BODY, fontName=f"{FONT}-Bold",
                           fontSize=10, leading=13, textColor=ACCENT)
TABLE_CELL = ParagraphStyle("TableCell", parent=BODY, fontSize=9.5, leading=12)

# ─── Markdown parser (минимальный) ───────────────────────────────────────

_EMOJI_RE = re.compile(
    # Эмоджи диапазоны Unicode — широкая выборка
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF"
    "\U00002700-\U000027BF\U0001F900-\U0001F9FF]+",
    flags=re.UNICODE,
)
_INLINE_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_INLINE_ITALIC = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_INLINE_CODE = re.compile(r"`([^`]+)`")
_INLINE_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _strip_emoji(s: str) -> str:
    return _EMOJI_RE.sub("", s).strip()


def _render_inline(s: str) -> str:
    """Markdown inline → ReportLab XML."""
    s = _strip_emoji(s)
    # links → blue underlined
    s = _INLINE_LINK.sub(r'<font color="#0F4C81"><u>\1</u></font>', s)
    # bold
    s = _INLINE_BOLD.sub(r"<b>\1</b>", s)
    # italic
    s = _INLINE_ITALIC.sub(r"<i>\1</i>", s)
    # inline code → light bg
    s = _INLINE_CODE.sub(
        r'<font face="Courier" backColor="#F2F4F8"> \1 </font>', s
    )
    return s


def _parse_table(lines: list[str], start: int) -> tuple[list[list[str]], int]:
    """Парсит markdown-таблицу. Возвращает (rows, end_index)."""
    rows: list[list[str]] = []
    i = start
    while i < len(lines) and "|" in lines[i]:
        line = lines[i].strip()
        if not line or set(line) <= {"-", "|", ":", " "}:
            i += 1
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        rows.append(cells)
        i += 1
    return rows, i


def parse_markdown(md_text: str) -> list:
    """Парсит markdown в список Flowable объектов."""
    lines = md_text.split("\n")
    story: list = []
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        # Пустая строка
        if not line:
            i += 1
            continue
        # H1 / H2 / H3
        if line.startswith("# "):
            story.append(Paragraph(_render_inline(line[2:]), H1))
            i += 1
            continue
        if line.startswith("## "):
            story.append(Paragraph(_render_inline(line[3:]), H2))
            i += 1
            continue
        if line.startswith("### "):
            story.append(Paragraph(_render_inline(line[4:]), H3))
            i += 1
            continue
        # HR
        if re.fullmatch(r"-{3,}|_{3,}|\*{3,}", line.strip()):
            story.append(Spacer(1, 6))
            story.append(HRFlowable(width="100%", thickness=0.5, color=ACCENT))
            story.append(Spacer(1, 6))
            i += 1
            continue
        # Quote
        if line.startswith("> "):
            story.append(Paragraph(_render_inline(line[2:]), QUOTE))
            i += 1
            continue
        # Таблица — следующая строка содержит |
        if "|" in line:
            rows, end = _parse_table(lines, i)
            if rows:
                rendered = []
                for r_idx, row in enumerate(rows):
                    style = TABLE_HDR if r_idx == 0 else TABLE_CELL
                    rendered.append([Paragraph(_render_inline(c), style) for c in row])
                col_count = max(len(r) for r in rendered) if rendered else 1
                col_w = (A4[0] - 4 * cm) / col_count
                t = Table(rendered, colWidths=[col_w] * col_count, repeatRows=1)
                t.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), LIGHT_BG),
                    ("GRID", (0, 0), (-1, -1), 0.25, MUTED),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]))
                story.append(t)
                story.append(Spacer(1, 8))
                i = end
                continue
        # List item (- / * / number.)
        if re.match(r"^[\-\*]\s+", line) or re.match(r"^\d+\.\s+", line):
            items = []
            while i < len(lines) and (
                re.match(r"^[\-\*]\s+", lines[i]) or re.match(r"^\d+\.\s+", lines[i])
            ):
                text = re.sub(r"^[\-\*]\s+|^\d+\.\s+", "", lines[i].rstrip())
                items.append(ListItem(Paragraph(_render_inline(text), BODY),
                                      bulletColor=ACCENT, leftIndent=12))
                i += 1
            story.append(ListFlowable(items, bulletType="bullet",
                                       bulletFontName=FONT, bulletFontSize=10,
                                       leftIndent=12))
            story.append(Spacer(1, 4))
            continue
        # Регулярный параграф
        story.append(Paragraph(_render_inline(line), BODY))
        i += 1
    return story


def md_to_pdf(md_path: Path, pdf_path: Path) -> None:
    md_text = md_path.read_text(encoding="utf-8")
    story = parse_markdown(md_text)
    doc = SimpleDocTemplate(
        str(pdf_path), pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=1.5 * cm, bottomMargin=1.5 * cm,
        title=md_path.stem,
    )
    doc.build(story)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: md_to_pdf.py input.md output.pdf")
        sys.exit(1)
    md_to_pdf(Path(sys.argv[1]), Path(sys.argv[2]))
    print(f"OK: {sys.argv[1]} -> {sys.argv[2]}")
