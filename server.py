"""Karakeep image tagger: a tiny stdlib-only proxy + static page.

The browser only talks to this server; the Karakeep API key stays here.
Nothing is stored: every request is answered from Karakeep directly.
"""

import base64
import hmac
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KARAKEEP_URL = os.environ.get("KARAKEEP_URL", "").rstrip("/")
API_KEY = os.environ.get("KARAKEEP_API_KEY", "")
PORT = int(os.environ.get("PORT", "8080"))
# Optional HTTP basic auth in front of the app (user name is ignored).
PASSWORD = os.environ.get("TAGGER_PASSWORD", "")
WORKERS = int(os.environ.get("TAGGER_CONCURRENCY", "8"))
TIMEOUT = 30

API = KARAKEEP_URL + "/api/v1"
INDEX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "index.html")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class KarakeepError(Exception):
    def __init__(self, status, body):
        super().__init__(f"Karakeep {status}: {body[:200]}")
        self.status = status


def kk(method, path, body=None, raw=False):
    """Call the Karakeep API. Returns parsed JSON, or the raw response if raw=True."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + API_KEY)
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


def slim(b):
    c = b["content"]
    return {
        "id": b["id"],
        "assetId": c["assetId"],
        "fileName": c.get("fileName"),
        "title": b.get("title"),
        "createdAt": b["createdAt"],
        "tags": sorted(t["name"] for t in b["tags"]),
    }


def is_image(b):
    c = b.get("content") or {}
    return c.get("type") == "asset" and c.get("assetType") == "image"


def list_images():
    return [slim(b) for b in paginate("/bookmarks", "bookmarks") if is_image(b)]


def list_tags():
    return sorted((t["name"] for t in paginate("/tags", "tags")), key=str.lower)


def clean_tags(names):
    if not isinstance(names, list):
        raise ValueError("tags must be a list")
    out = []
    for n in names:
        if not isinstance(n, str) or not n.strip():
            raise ValueError("tag names must be non-empty strings")
        out.append(n.strip())
    return out


def apply_tags(bookmark_id, add, remove):
    if add:
        kk("POST", f"/bookmarks/{bookmark_id}/tags", {"tags": [{"tagName": n} for n in add]})
    if remove:
        kk("DELETE", f"/bookmarks/{bookmark_id}/tags", {"tags": [{"tagName": n} for n in remove]})
    # Re-read so the client reflects Karakeep's actual state.
    return slim(kk("GET", f"/bookmarks/{bookmark_id}"))


def bulk_tags(body):
    ids = body.get("bookmarkIds")
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and ID_RE.match(i) for i in ids):
        raise ValueError("bookmarkIds must be a non-empty list of ids")
    add, remove = clean_tags(body.get("add", [])), clean_tags(body.get("remove", []))
    if not add and not remove:
        raise ValueError("nothing to add or remove")

    def one(bid):
        try:
            return {"ok": apply_tags(bid, add, remove)}
        except Exception as e:  # report per bookmark, keep going
            return {"error": {"id": bid, "message": str(e)}}

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(one, ids))
    return {
        "updated": [r["ok"] for r in results if "ok" in r],
        "failed": [r["error"] for r in results if "error" in r],
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "karakeep-tagger"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def authorized(self):
        if not PASSWORD:
            return True
        h = self.headers.get("Authorization", "")
        if h.startswith("Basic "):
            try:
                _, _, pw = base64.b64decode(h[6:]).decode().partition(":")
                if hmac.compare_digest(pw, PASSWORD):
                    return True
            except Exception:
                pass
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="karakeep-tagger"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def send_json(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_errors(self, fn):
        try:
            fn()
        except ValueError as e:
            self.send_json(400, {"error": str(e)})
        except KarakeepError as e:
            self.send_json(404 if e.status == 404 else 502, {"error": str(e)})
        except (urllib.error.URLError, TimeoutError) as e:
            self.send_json(502, {"error": f"Cannot reach Karakeep: {e}"})

    def do_GET(self):
        if not self.authorized():
            return
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self.serve_index()
        if path == "/healthz":
            return self.send_json(200, {"ok": True})
        if path == "/api/bookmarks":
            return self.handle_errors(lambda: self.send_json(200, {"bookmarks": list_images()}))
        if path == "/api/tags":
            return self.handle_errors(lambda: self.send_json(200, {"tags": list_tags()}))
        m = re.match(r"^/api/assets/([A-Za-z0-9-]{1,64})$", path)
        if m:
            return self.handle_errors(lambda: self.proxy_asset(m.group(1)))
        self.send_json(404, {"error": "not found"})

    def do_POST(self):
        if not self.authorized():
            return
        if urllib.parse.urlparse(self.path).path != "/api/tags/bulk":
            return self.send_json(404, {"error": "not found"})
        # CSRF: a cross-site page can only send a JSON content type after a CORS preflight,
        # which this server never grants; browsers also mark cross-site requests in Sec-Fetch-Site.
        ctype = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if ctype != "application/json":
            return self.send_json(415, {"error": "expected application/json"})
        if self.headers.get("Sec-Fetch-Site", "same-origin") != "same-origin":
            return self.send_json(403, {"error": "cross-site request refused"})

        def run():
            length = int(self.headers.get("Content-Length") or 0)
            if length > 1_000_000:
                raise ValueError("request too large")
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                raise ValueError("invalid JSON") from None
            if not isinstance(body, dict):
                raise ValueError("invalid body")
            self.send_json(200, bulk_tags(body))

        self.handle_errors(run)

    def serve_index(self):
        with open(INDEX, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def proxy_asset(self, asset_id):
        resp = kk("GET", f"/assets/{asset_id}", raw=True)
        with resp:
            ctype = resp.headers.get("Content-Type", "application/octet-stream")
            if not ctype.startswith("image/"):
                raise ValueError("asset is not an image")
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "private, max-age=31536000, immutable")
            if resp.headers.get("Content-Length"):
                self.send_header("Content-Length", resp.headers["Content-Length"])
            self.end_headers()
            try:
                while chunk := resp.read(64 * 1024):
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass  # browser scrolled away / cancelled


def main():
    if not KARAKEEP_URL or not API_KEY:
        sys.exit("KARAKEEP_URL and KARAKEEP_API_KEY must be set")
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    httpd.daemon_threads = True
    print(f"karakeep-tagger on :{PORT} -> {KARAKEEP_URL}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
