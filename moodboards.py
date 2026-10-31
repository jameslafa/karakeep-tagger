"""Moodboards: collections of images for a shoot, exported as a PDF.

Karakeep holds each board as a sublist of the moodboards list (name, description, which
images). A JSON file per board holds what Karakeep can't: sections, order, a note per
image, the cover and the PDF's column count.
"""

import io
import json
import os
import threading
import urllib.error
from concurrent.futures import ThreadPoolExecutor

import moodboard_pdf
from karakeep import DATA_DIR, ID_RE, KarakeepError, clean_ids, is_image, kk, paginate

try:  # optional: resizes images for board pages and PDFs
    from PIL import Image, ImageOps
except ImportError:
    Image = None

# Moodboards are the sublists of this top-level Karakeep list; it is created with the first board.
BOARDS_LIST = os.environ.get("TAGGER_BOARDS_LIST", "Moodboards")
BOARDS_DIR = os.path.join(DATA_DIR, "moodboards")


class NotFound(Exception):
    pass


boards_lock = threading.Lock()


def boards_parent(create=False):
    lists = kk("GET", "/lists")["lists"]
    parent = next((l for l in lists if l["name"] == BOARDS_LIST and not l.get("parentId")
                   and l.get("type", "manual") == "manual"), None)
    if not parent and create:
        parent = kk("POST", "/lists", {"name": BOARDS_LIST, "icon": "🎞️", "type": "manual"})
    return parent, lists


def board_lists():
    parent, lists = boards_parent()
    if not parent:
        return []
    return sorted((l for l in lists if l.get("parentId") == parent["id"] and l.get("type", "manual") == "manual"),
                  key=lambda l: l["name"].lower())


def board_list(board_id):
    """The Karakeep list of a board; refuses any list that isn't a board."""
    l = next((l for l in board_lists() if l["id"] == board_id), None)
    if not l:
        raise NotFound("no such moodboard")
    return l


def layout_path(board_id):
    return os.path.join(BOARDS_DIR, board_id + ".json")  # board_id matched ID_RE


def load_layout(board_id):
    try:
        with open(layout_path(board_id)) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_layout(board_id, layout):
    os.makedirs(BOARDS_DIR, exist_ok=True)
    tmp = layout_path(board_id) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(layout, f, indent=1)
    os.replace(tmp, layout_path(board_id))


def merge_layout(layout, members):
    """Fit the stored layout to the board's current images: drop the ones taken out of the
    list (their notes are kept, in case they come back) and put new ones in unsorted."""
    member_set, placed = set(members), set()
    sections = []
    for sec in layout.get("sections", []):
        items = [i for i in sec.get("items", []) if i in member_set and i not in placed]
        placed.update(items)
        sections.append({**sec, "items": items})
    unsorted = [i for i in layout.get("unsorted", []) if i in member_set and i not in placed]
    placed.update(unsorted)
    unsorted += [i for i in members if i not in placed]
    cover = layout.get("cover") if layout.get("cover") in member_set else None
    return {"sections": sections, "unsorted": unsorted, "notes": layout.get("notes", {}), "cover": cover,
            "columns": layout.get("columns", 2)}


def list_boards():
    return [{"id": l["id"], "name": l["name"], "description": l.get("description") or ""} for l in board_lists()]


def get_board(board_id):
    l = board_list(board_id)
    images = {}
    for b in paginate(f"/lists/{board_id}/bookmarks", "bookmarks"):
        if is_image(b):
            images[b["id"]] = {"assetId": b["content"]["assetId"], "title": b.get("title")}
    with boards_lock:
        layout = merge_layout(load_layout(board_id), list(images))
    return {"id": l["id"], "name": l["name"], "description": l.get("description") or "", "images": images, **layout}


def clean_text(v, what, limit, required=False):
    if not isinstance(v, str) or len(v) > limit or (required and not v.strip()):
        raise ValueError(f"{what} must be {'non-empty ' if required else ''}text up to {limit} characters")
    return v.strip() if required else v


def create_board(body):
    name = clean_text(body.get("name"), "name", 40, required=True)
    description = clean_text(body.get("description", ""), "description", 500)
    parent, _ = boards_parent(create=True)
    l = kk("POST", "/lists", {"name": name, "description": description, "icon": "🖼️",
                              "type": "manual", "parentId": parent["id"]})
    return {"id": l["id"], "name": l["name"], "description": l.get("description") or ""}


def save_board(board_id, body):
    # Check the whole request before changing anything.
    l = board_list(board_id)
    patch = {}
    if "name" in body:
        name = clean_text(body["name"], "name", 40, required=True)
        if name != l["name"]:
            patch["name"] = name
    if "description" in body:
        description = clean_text(body["description"], "description", 500)
        if description != (l.get("description") or ""):
            patch["description"] = description
    layout = None
    if "sections" in body:
        sections = body["sections"]
        if not isinstance(sections, list) or len(sections) > 100:
            raise ValueError("sections must be a list of up to 100")
        clean = []
        for sec in sections:
            if not isinstance(sec, dict) or not isinstance(sec.get("id"), str) or not ID_RE.match(sec["id"]):
                raise ValueError("each section needs an id")
            cols = sec.get("columns")  # None: the board's setting
            if cols is not None and (cols not in (1, 2, 3, 4) or isinstance(cols, bool)):
                raise ValueError("section columns must be 1 to 4")
            clean.append({"id": sec["id"], "title": clean_text(sec.get("title", ""), "section title", 200),
                          "description": clean_text(sec.get("description", ""), "section description", 2000),
                          "items": clean_ids(sec.get("items", []), "section items"), "columns": cols})
        notes = body.get("notes", {})
        if not isinstance(notes, dict) or not all(isinstance(k, str) and ID_RE.match(k) for k in notes):
            raise ValueError("notes must map image ids to text")
        notes = {k: clean_text(v, "note", 1000) for k, v in notes.items() if v}
        cover = body.get("cover")
        if cover is not None and not (isinstance(cover, str) and ID_RE.match(cover)):
            raise ValueError("cover must be an image id")
        columns = body.get("columns", 2)
        if columns not in (1, 2, 3, 4) or isinstance(columns, bool):
            raise ValueError("columns must be 1 to 4")
        layout = {"sections": clean, "unsorted": clean_ids(body.get("unsorted", []), "unsorted"),
                  "notes": notes, "cover": cover, "columns": columns}
    if patch:
        kk("PATCH", f"/lists/{board_id}", patch)
    if layout is not None:
        with boards_lock:
            save_layout(board_id, layout)
    return {"ok": True}


def duplicate_board(board_id, body):
    """A new board with the same description, images, sections, notes, cover and columns."""
    name = clean_text(body.get("name"), "name", 40, required=True)
    src = get_board(board_id)
    new = create_board({"name": name, "description": src["description"]})
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda bid: kk("PUT", f"/lists/{new['id']}/bookmarks/{bid}"), src["images"]))
    with boards_lock:
        save_layout(new["id"], {k: src[k] for k in ("sections", "unsorted", "notes", "cover", "columns")})
    return new


def delete_board(board_id, body=None):
    board_list(board_id)
    kk("DELETE", f"/lists/{board_id}")
    with boards_lock:
        try:
            os.remove(layout_path(board_id))
        except FileNotFoundError:
            pass
    return {"ok": True}


def resize_image(data, width):
    """A JPEG at most width pixels on its long edge, or None if Pillow can't do it."""
    if Image is None:
        return None
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
        img.thumbnail((width, width))
        if img.mode in ("RGBA", "LA", "P"):  # JPEG has no transparency: put it on white, not black
            img = img.convert("RGBA")
            img = Image.alpha_composite(Image.new("RGBA", img.size, "white"), img)
        out = io.BytesIO()
        img.convert("RGB").save(out, "JPEG", quality=85, optimize=True)
        return out.getvalue()
    except Exception:
        return None


def pdf(board_id, workers=8):
    """(board name, PDF bytes) for a board."""
    if not moodboard_pdf.AVAILABLE:
        raise ValueError("PDF export needs the reportlab package (pip install reportlab)")

    def fetch(asset_id):
        try:
            with kk("GET", f"/assets/{asset_id}", raw=True) as resp:
                data = resp.read()
        except (KarakeepError, urllib.error.URLError, TimeoutError):
            return None
        return resize_image(data, 1600) or data

    board = get_board(board_id)
    return board["name"], moodboard_pdf.build(board, fetch, workers)
