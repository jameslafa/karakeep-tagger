"""Moodboard PDF, built on the server so it downloads in colour without a print dialog.

Made for screens, not paper: pages are A4 wide and as tall as their content. The first page
has the title, description and cover; then each section is one page with its title,
description and its images in a masonry grid (each image goes into the shortest column),
each with its note underneath.
"""

import html
import io
from concurrent.futures import ThreadPoolExecutor

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.platypus import Paragraph
    AVAILABLE = True
except ImportError:
    AVAILABLE = False

# DejaVu covers far more characters than the built-in Helvetica (which is Latin-1 only).
DEJAVU = "/usr/share/fonts/truetype/dejavu/"
MAX_PAGE_HEIGHT = 14000  # points; PDF viewers commonly refuse pages over 14400 (200 inches)
_fonts = None


def fonts():
    global _fonts
    if _fonts is None:
        try:
            pdfmetrics.registerFont(TTFont("DejaVu", DEJAVU + "DejaVuSans.ttf"))
            pdfmetrics.registerFont(TTFont("DejaVu-Bold", DEJAVU + "DejaVuSans-Bold.ttf"))
            _fonts = ("DejaVu", "DejaVu-Bold")
        except Exception:
            _fonts = ("Helvetica", "Helvetica-Bold")
    return _fonts


def text(s):
    """Plain text as Paragraph markup, keeping line breaks."""
    return html.escape(s.strip()).replace("\n", "<br/>")


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
    regular, bold = fonts()
    title_style = ParagraphStyle("title", fontName=bold, fontSize=26, leading=31)
    h2_style = ParagraphStyle("h2", fontName=bold, fontSize=17, leading=21)
    desc_style = ParagraphStyle("desc", fontName=regular, fontSize=11, leading=16, textColor="#333333")
    note_style = ParagraphStyle("note", fontName=regular, fontSize=10, leading=14.5, textColor="#333333")

    # A board without sections prints its unsorted images; with sections, unsorted is a staging area.
    if board["sections"]:
        parts = [s for s in board["sections"] if s["items"] or s["title"].strip() or s["description"].strip()]
    else:
        parts = [{"title": "", "description": "", "items": board["unsorted"]}] if board["unsorted"] else []
    ids = ([board["cover"]] if board["cover"] else []) + [i for p in parts for i in p["items"]]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        images = dict(zip(ids, pool.map(lambda i: reader(fetch(board["images"][i]["assetId"])), ids)))

    page_w = A4[0]
    margin = 14 * mm
    width = page_w - 2 * margin
    gap = 5 * mm
    cols = max(1, min(4, int(board.get("columns") or 2)))
    col_w = (width - gap * (cols - 1)) / cols

    def para(markup, style, w):
        p = Paragraph(markup, style)
        return p, p.wrap(w, 10 ** 6)[1]

    # Pages are laid out first as (height, ops), each op placed by its distance from the top,
    # because a page's height is only known once its content is.
    pages = []

    # Cover page
    ops, y = [], margin
    p, h = para(text(board["name"]), title_style, width)
    ops.append(("para", margin, y, p, h)); y += h
    if board["description"].strip():
        p, h = para(text(board["description"]), desc_style, width)
        y += 4 * mm
        ops.append(("para", margin, y, p, h)); y += h
    cover = images.get(board["cover"]) if board["cover"] else None
    if cover:
        w, h = fit(cover, width, 250 * mm)
        y += 6 * mm
        ops.append(("image", margin, y, cover[0], w, h)); y += h
    pages.append((y + margin, ops))

    # One page per section (more only if it outgrows the maximum page height)
    for part in parts:
        ops, y = [], margin
        if part["title"].strip():
            p, h = para(text(part["title"]), h2_style, width)
            ops.append(("para", margin, y, p, h)); y += h + 2 * mm
        if part["description"].strip():
            p, h = para(text(part["description"]), desc_style, width)
            ops.append(("para", margin, y, p, h)); y += h + 2 * mm
        if ops:
            y += 4 * mm
        tops = [y] * cols  # where the next image goes in each column
        for i in part["items"]:
            # The image and its note, placed relative to the block's top left
            block, bh = [], 0
            img = images.get(i)
            if img:
                w, bh = fit(img, col_w, col_w * 3)  # keep very tall strips in check
                block.append(("image", 0, 0, img[0], w, bh))
            else:
                p, bh = para("(image unavailable)", note_style, col_w)
                block.append(("para", 0, 0, p, bh))
            note = board["notes"].get(i, "")
            if note.strip():
                p, h = para(text(note), note_style, col_w)
                block.append(("para", 0, bh + 2 * mm, p, h)); bh += 2 * mm + h
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
        for kind, x, top, *rest in ops:
            if kind == "para":
                p, h = rest
                p.drawOn(canvas, x, page_h - top - h)
            else:
                r, w, h = rest
                canvas.drawImage(r, x, page_h - top - h, w, h)
        canvas.showPage()
    canvas.save()
    return out.getvalue()
