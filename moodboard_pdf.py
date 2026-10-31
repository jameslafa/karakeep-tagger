"""Moodboard PDF, built on the server so it downloads in colour without a print dialog.

Made for screens, not paper: a dark "gallery" look, pages A4 wide and as tall as their
content. The first page has the cover image edge to edge, then the title and description;
each section is one page with a numbered heading, its description and its images in a
masonry grid (each image goes into the shortest column), each with its note underneath.
Descriptions and notes may use a little Markdown: bold, italic, "- " lists and links.
"""

import html
import io
import os
import re
from concurrent.futures import ThreadPoolExecutor

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.utils import ImageReader
    from reportlab.lib.fonts import addMapping
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.platypus import Paragraph
    AVAILABLE = True
except ImportError:
    AVAILABLE = False

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
MAX_PAGE_HEIGHT = 14000  # points; PDF viewers commonly refuse pages over 14400 (200 inches)

BACKGROUND = "#111214"
TEXT = "#EDEBE7"
SOFT = "#C9C6C0"   # descriptions
MUTED = "#8E8B86"  # section numbers, notes
RULE = "#2E3036"

_fonts = None


def fonts():
    """(display, regular, bold) font names; the bundled fonts, or built-in ones if missing."""
    global _fonts
    if _fonts is None:
        try:
            for name, file in [("Display", "CormorantGaramond-SemiBold.ttf"), ("Text", "Inter-Regular.ttf"),
                               ("Text-Bold", "Inter-SemiBold.ttf"), ("Text-Italic", "Inter-Italic.ttf"),
                               ("Text-BoldItalic", "Inter-SemiBoldItalic.ttf")]:
                pdfmetrics.registerFont(TTFont(name, os.path.join(FONT_DIR, file)))
            # So <b> and <i> in a paragraph find the right files
            for bold, italic, name in [(0, 0, "Text"), (1, 0, "Text-Bold"), (0, 1, "Text-Italic"), (1, 1, "Text-BoldItalic")]:
                addMapping("Text", bold, italic, name)
            _fonts = ("Display", "Text", "Text-Bold")
        except Exception:
            _fonts = ("Times-Bold", "Helvetica", "Helvetica-Bold")
    return _fonts


def text(s):
    """Plain text as Paragraph markup, keeping line breaks."""
    return html.escape(s.strip()).replace("\n", "<br/>")


# ---------- A little Markdown: **bold**, *italic* or _italic_, "- " lists, links ----------

LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)|(https?://[^\s<>]+)")


def inline(s):
    """One line of Markdown as Paragraph markup."""
    # Links first, set aside so the * and _ inside URLs aren't read as italics
    links, out, pos = [], [], 0
    for m in LINK.finditer(s):
        label, url, trail = m.group(1), m.group(2), ""
        if not url:  # a bare address; punctuation right after it isn't part of it
            url = m.group(3).rstrip(".,;:!?)*")  # * too, for **https://…**
            label, trail = url, m.group(3)[len(url):]
        links.append(f'<a href="{html.escape(url)}"><u>{html.escape(label)}</u></a>')
        out += [html.escape(s[pos:m.start()]), f"\x00{len(links) - 1}\x00", html.escape(trail)]
        pos = m.end()
    out.append(html.escape(s[pos:]))
    s = "".join(out)
    s = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<![*\w])\*(?=\S)(.+?)(?<=\S)\*(?![*\w])", r"<i>\1</i>", s)
    s = re.sub(r"(?<!\w)_(?=\S)(.+?)(?<=\S)_(?!\w)", r"<i>\1</i>", s)
    return re.sub(r"\x00(\d+)\x00", lambda m: links[int(m.group(1))], s)


def blocks(s):
    """Markdown text as [(markup, plain markup, is_list_item)]; the plain one is the fallback
    for markers that don't nest. Lines within a paragraph stay separate lines; an empty line
    starts a new paragraph."""
    out, lines = [], []

    def end_paragraph():
        if lines:
            out.append(("<br/>".join(inline(l) for l in lines), "<br/>".join(html.escape(l) for l in lines), False))
            lines.clear()

    for line in s.strip().splitlines():
        item = re.match(r"\s*[-*]\s+(.*)", line)
        if item:
            end_paragraph()
            out.append((inline(item.group(1)), html.escape(item.group(1)), True))
        elif line.strip():
            lines.append(line.strip())
        else:
            end_paragraph()
    end_paragraph()
    return out


def rich(s, style, width):
    """Paragraphs for Markdown text: [(Paragraph, height, space above)]."""
    item_style = ParagraphStyle(style.name + "-item", parent=style, leftIndent=11, bulletIndent=1,
                                bulletFontName=style.fontName, bulletFontSize=style.fontSize)
    out, prev_item = [], None
    for markup, plain, is_item in blocks(s):
        try:
            p = Paragraph(markup, item_style, bulletText="•") if is_item else Paragraph(markup, style)
        except ValueError:  # e.g. "**a *b** c*": tags that don't nest; show the text as typed
            p = Paragraph(plain, item_style, bulletText="•") if is_item else Paragraph(plain, style)
        space = 0 if prev_item is None else (style.leading * 0.15 if is_item and prev_item else style.leading * 0.6)
        out.append((p, p.wrap(width, 10 ** 6)[1], space))
        prev_item = is_item
    return out


def reader(data):
    """(ImageReader, width, height) for image bytes, or None."""
    if not data:
        return None
    try:
        r = ImageReader(io.BytesIO(data))
        w, h = r.getSize()
        return r, w, h
    except Exception:
        return None


def fit(img, max_w, max_h):
    _, iw, ih = img
    scale = min(max_w / iw, max_h / ih)
    return iw * scale, ih * scale


def build(board, fetch, workers=8):
    """The PDF as bytes. board is what get_board returns; fetch(asset_id) returns image bytes
    (or None when the image can't be had)."""
    display, regular, bold = fonts()
    title_style = ParagraphStyle("title", fontName=display, fontSize=40, leading=44, textColor=TEXT)
    h2_style = ParagraphStyle("h2", fontName=display, fontSize=28, leading=32, textColor=TEXT)
    desc_style = ParagraphStyle("desc", fontName=regular, fontSize=10.5, leading=16, textColor=SOFT)
    note_style = ParagraphStyle("note", fontName=regular, fontSize=8.5, leading=12.5, textColor=MUTED)

    # A board without sections prints its unsorted images; with sections, unsorted is a staging area.
    if board["sections"]:
        parts = [s for s in board["sections"] if s["items"] or s["title"].strip() or s["description"].strip()]
    else:
        parts = [{"title": "", "description": "", "items": board["unsorted"]}] if board["unsorted"] else []
    ids = ([board["cover"]] if board["cover"] else []) + [i for p in parts for i in p["items"]]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        images = dict(zip(ids, pool.map(lambda i: reader(fetch(board["images"][i]["assetId"])), ids)))

    page_w = A4[0]
    margin = 12 * mm
    width = page_w - 2 * margin
    gap = 3 * mm
    board_cols = board.get("columns") or 2

    def para(markup, style, w):
        p = Paragraph(markup, style)
        return p, p.wrap(w, 10 ** 6)[1]

    def place(ops, x, y, markdown, style, w):
        """Lay out Markdown text from y down; returns where it ends."""
        for p, h, space in rich(markdown, style, w):
            y += space
            ops.append(("para", x, y, p, h)); y += h
        return y

    # Pages are laid out first as (height, ops), each op placed by its distance from the top,
    # because a page's height is only known once its content is.
    pages = []

    # Cover page: the cover image edge to edge, then the title block
    ops, y = [], margin
    cover = images.get(board["cover"]) if board["cover"] else None
    if cover:
        _, iw, ih = cover
        h = page_w * ih / iw
        shown = min(h, page_w * 1.25)  # a tall cover is cropped to its middle
        ops.append(("cover", 0, 0, cover[0], page_w, h, shown)); y = shown + 12 * mm
    p, h = para(text(board["name"]), title_style, width)
    ops.append(("para", margin, y, p, h)); y += h
    if board["description"].strip():
        y = place(ops, margin, y + 5 * mm, board["description"], desc_style, width)
    pages.append((y + margin + 4 * mm, ops))

    # One page per section (more only if it outgrows the maximum page height)
    for n, part in enumerate(parts, 1):
        cols = max(1, min(4, int(part.get("columns") or board_cols)))  # a section may override the board
        col_w = (width - gap * (cols - 1)) / cols
        ops, y = [], margin
        title, desc = part["title"].strip(), part["description"].strip()
        if board["sections"]:  # a board without sections has no headings
            # The number in the text font: Cormorant's old-style "01" reads like "oı"
            heading = f'<font name="{bold}" size="15" color="{MUTED}">{n:02d}</font>' + (f"&nbsp;&nbsp;&nbsp;{text(title)}" if title else "")
            p, h = para(heading, h2_style, width)
            ops.append(("para", margin, y, p, h)); y += h + 3 * mm
            ops.append(("rule", margin, y, width)); y += 4 * mm
        if desc:
            y = place(ops, margin, y, desc, desc_style, width) + 6 * mm
        tops = [y] * cols  # where the next image goes in each column
        for i in part["items"]:
            # The image and its note, placed relative to the block's top left
            block, bh = [], 0
            img = images.get(i)
            if img:
                w, bh = fit(img, col_w, col_w * 3)  # keep very tall strips in check
                block.append(("image", 0, 0, img[0], w, bh, 2.5))
            else:
                p, bh = para("(image unavailable)", note_style, col_w)
                block.append(("para", 0, 0, p, bh))
            note = board["notes"].get(i, "")
            if note.strip():
                bh = place(block, 0, bh + 2 * mm, note, note_style, col_w)
            c = min(range(cols), key=lambda k: tops[k])
            if tops[c] + bh + margin > MAX_PAGE_HEIGHT and tops[c] > y:
                pages.append((max(tops) - gap + margin, ops))
                ops, y = [], margin
                tops = [y] * cols
                c = 0
            x = margin + c * (col_w + gap)
            for kind, dx, dy, *rest in block:
                ops.append((kind, x + dx, tops[c] + dy, *rest))
            tops[c] += bh + gap
        pages.append(((max(tops) - gap if max(tops) > y else y) + margin, ops))

    out = io.BytesIO()
    canvas = Canvas(out, pagesize=(page_w, pages[0][0]))
    canvas.setTitle(board["name"].strip())
    canvas.setAuthor("")
    canvas.setCreator("")
    for page_h, ops in pages:
        canvas.setPageSize((page_w, page_h))
        canvas.setFillColor(BACKGROUND)
        canvas.rect(0, 0, page_w, page_h, stroke=0, fill=1)
        for kind, x, top, *rest in ops:
            if kind == "para":
                p, h = rest
                p.drawOn(canvas, x, page_h - top - h)
            elif kind == "image":
                r, w, h, radius = rest
                canvas.saveState()
                if radius:
                    path = canvas.beginPath()
                    path.roundRect(x, page_h - top - h, w, h, radius)
                    canvas.clipPath(path, stroke=0, fill=0)
                canvas.drawImage(r, x, page_h - top - h, w, h)
                canvas.restoreState()
            elif kind == "cover":
                r, w, h, shown = rest
                canvas.saveState()
                path = canvas.beginPath()
                path.rect(x, page_h - top - shown, w, shown)
                canvas.clipPath(path, stroke=0, fill=0)
                canvas.drawImage(r, x, page_h - top - shown - (h - shown) / 2, w, h)
                canvas.restoreState()
            elif kind == "rule":
                (w,) = rest
                canvas.setStrokeColor(RULE)
                canvas.setLineWidth(0.5)
                canvas.line(x, page_h - top, x + w, page_h - top)
        canvas.showPage()
    canvas.save()
    return out.getvalue()
