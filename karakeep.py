"""Karakeep API client and the settings shared by the tagger's modules."""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

KARAKEEP_URL = os.environ.get("KARAKEEP_URL", "").rstrip("/")
API_KEY = os.environ.get("KARAKEEP_API_KEY", "")
API = KARAKEEP_URL + "/api/v1"
TIMEOUT = 30
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# Where the tagger keeps its own files: moodboard layouts and the inbox state.
DATA_DIR = os.environ.get("TAGGER_DATA_DIR") or os.environ.get("INBOX_STATE_DIR") or "/data"


class KarakeepError(Exception):
    def __init__(self, status, body):
        super().__init__(f"Karakeep {status}: {body[:200]}")
        self.status = status


def kk(method, path, body=None, raw=False, key=None):
    """Call the Karakeep API. Returns parsed JSON, or the raw response if raw=True."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + (key or API_KEY))
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(req, timeout=TIMEOUT)
    except urllib.error.HTTPError as e:
        raise KarakeepError(e.code, e.read().decode(errors="replace")) from None
    if raw:
        return resp
    with resp:
        payload = resp.read()
    return json.loads(payload) if payload else None


def paginate(path, key):
    items, cursor = [], None
    while True:
        q = {"limit": 100}
        if cursor:
            q["cursor"] = cursor
        page = kk("GET", f"{path}?{urllib.parse.urlencode(q)}")
        items.extend(page[key])
        cursor = page.get("nextCursor")
        if not cursor:
            return items


def is_image(b):
    c = b.get("content") or {}
    return c.get("type") == "asset" and c.get("assetType") == "image"


def clean_ids(ids, what):
    if not isinstance(ids, list) or not all(isinstance(i, str) and ID_RE.match(i) for i in ids):
        raise ValueError(f"{what} must be a list of ids")
    return ids
