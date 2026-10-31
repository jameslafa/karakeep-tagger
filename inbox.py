"""Inbox import: watch folders in a Seafile library and import the images into Karakeep.

Runs as a background thread inside server.py. Every poll lists the configured folders,
waits until a file's size and mtime are unchanged across two polls, then downloads it,
uploads it to Karakeep as an image bookmark, adds it to the folder's list, tags it, and
only then deletes it from Seafile (into the library's recycle bin). Nothing is deleted
unless every step succeeded; failures are retried on later polls with backoff.

A state file maps SHA-256 of the original file to the bookmark id, so a crash between
import and deletion, or the same file dropped twice, never creates a second bookmark.
"""

import hashlib
import io
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

from karakeep import DATA_DIR

SEAFILE_URL = os.environ.get("SEAFILE_URL", "").rstrip("/")
SEAFILE_TOKEN = os.environ.get("SEAFILE_TOKEN", "")
# "folder=List name,folder2=Other list": Seafile folder (relative to the library root)
# to the Karakeep list its images go into.
FOLDERS = os.environ.get("INBOX_FOLDERS", "")
TAG = os.environ.get("INBOX_TAG", "").strip()  # optional tag for every import
POLL = int(os.environ.get("INBOX_POLL_SECONDS", "60"))
DRY_RUN = os.environ.get("INBOX_DRY_RUN", "").lower() in ("1", "true", "yes")
TIMEOUT = 60
MAX_BACKOFF = 3600

CONTENT_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".gif": "image/gif",
}
HEIC = {".heic", ".heif"}  # converted to JPEG, Karakeep does not accept them
STATE_FILE = os.path.join(DATA_DIR, "inbox-state.json")


def enabled():
    return bool(SEAFILE_URL and SEAFILE_TOKEN and FOLDERS)


def log(msg):
    sys.stderr.write("inbox: %s\n" % msg)
    sys.stderr.flush()


# ---------- Seafile (library-scoped API token) ----------

class SeafileError(Exception):
    pass


def sf(method, path, query=None):
    url = SEAFILE_URL + "/api/v2.1/via-repo-token" + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", "Bearer " + SEAFILE_TOKEN)
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise SeafileError(f"Seafile {e.code} on {path}: {e.read().decode(errors='replace')[:200]}") from None


def rehost(link):
    """Seafile builds file-server links from its public host name, which may not be the
    address this service reaches it by. Keep the path, use our configured base."""
    u = urllib.parse.urlsplit(link)
    return SEAFILE_URL + urllib.parse.urlunsplit(("", "", u.path, u.query, ""))


def sf_list(folder):
    data = sf("GET", "/dir/", {"path": folder, "t": "f"})
    return data["dirent_list"]


def sf_download(path):
    link = rehost(sf("GET", "/download-link/", {"path": path}))
    req = urllib.request.Request(link)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


def sf_delete(path):
    data = sf("DELETE", "/file/", {"path": path})
    if not data.get("success"):
        raise SeafileError(f"delete of {path} not confirmed: {data}")


# ---------- Karakeep ----------

def multipart(field, filename, content_type, data):
    boundary = uuid.uuid4().hex
    body = io.BytesIO()
    body.write(f"--{boundary}\r\n".encode())
    body.write(f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'.encode())
    body.write(f"Content-Type: {content_type}\r\n\r\n".encode())
    body.write(data)
    body.write(f"\r\n--{boundary}--\r\n".encode())
    return f"multipart/form-data; boundary={boundary}", body.getvalue()


class Karakeep:
    def __init__(self, url, key):
        self.url, self.key = url, key

    def upload_asset(self, filename, content_type, data):
        ctype, body = multipart("file", filename, content_type, data)
        req = urllib.request.Request(self.url + "/api/v1/assets", data=body, method="POST")
        req.add_header("Authorization", "Bearer " + self.key)
        req.add_header("Content-Type", ctype)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Karakeep {e.code} on asset upload: {e.read().decode(errors='replace')[:200]}") from None


# ---------- Conversion ----------

def to_jpeg(data):
    """HEIC/HEIF -> JPEG bytes, keeping EXIF (orientation, capture date)."""
    try:
        from PIL import Image
        import pillow_heif
    except ImportError:
        raise RuntimeError("HEIC support needs the pillow and pillow-heif packages") from None
    pillow_heif.register_heif_opener()
    img = Image.open(io.BytesIO(data))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    out = io.BytesIO()
    kw = {"quality": 92, "optimize": True}
    if img.info.get("exif"):
        kw["exif"] = img.info["exif"]
    img.save(out, "JPEG", **kw)
    return out.getvalue()


# ---------- The poller ----------

class Inbox:
    def __init__(self, karakeep_url, karakeep_key, kk):
        self.kk = kk  # server.py's Karakeep JSON client (method, path, body, key=...)
        self.key = karakeep_key
        self.api = Karakeep(karakeep_url, karakeep_key)
        self.folders = self.parse_folders(FOLDERS)
        self.list_ids = {}      # folder -> Karakeep list id
        self.seen = {}          # path -> (size, mtime) from the previous poll
        self.failed = {}        # path -> {"attempts", "error", "next"}
        self.warned = set()     # paths already reported as unsupported
        self.state = self.load_state()
        self.lock = threading.Lock()
        self.status = {"lastPoll": None, "lastError": None, "imported": 0, "pending": []}

    @staticmethod
    def parse_folders(spec):
        out = {}
        for part in spec.split(","):
            if not part.strip():
                continue
            folder, sep, name = part.partition("=")
            if not sep or not folder.strip() or not name.strip():
                sys.exit(f"INBOX_FOLDERS: expected folder=List name, got {part!r}")
            out["/" + folder.strip().strip("/")] = name.strip()
        return out

    # -- state file --
    def load_state(self):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    def save_state(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.state, f, indent=1, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, STATE_FILE)

    # -- Karakeep helpers --
    def resolve_lists(self):
        missing = [f for f in self.folders if f not in self.list_ids]
        if not missing:
            return
        lists = {l["name"]: l["id"] for l in self.kk("GET", "/lists", key=self.key)["lists"]}
        for folder in missing:
            name = self.folders[folder]
            if name in lists:
                self.list_ids[folder] = lists[name]
                log(f"folder {folder} -> list {name!r} ({lists[name]})")
            else:
                raise RuntimeError(f"Karakeep list {name!r} for folder {folder} not found")

    def bookmark_exists(self, bookmark_id):
        try:
            return self.kk("GET", f"/bookmarks/{bookmark_id}", key=self.key)
        except Exception as e:
            if getattr(e, "status", None) == 404:
                return None
            raise

    # -- status for /api/inbox/status --
    def snapshot(self):
        with self.lock:
            s = dict(self.status)
            s["failed"] = [
                {"path": p, "attempts": f["attempts"], "error": f["error"],
                 "nextRetry": iso(f["next"])}
                for p, f in sorted(self.failed.items())
            ]
        s.update(enabled=True, dryRun=DRY_RUN, folders=self.folders, pollSeconds=POLL)
        return s

    # -- main loop --
    def run(self):
        log(f"watching {', '.join(f'{f} -> {n!r}' for f, n in self.folders.items())} every {POLL}s"
            + (" [DRY RUN]" if DRY_RUN else ""))
        while True:
            try:
                self.poll()
                with self.lock:
                    self.status["lastError"] = None
            except Exception as e:
                log(f"poll failed: {e}")
                with self.lock:
                    self.status["lastError"] = str(e)
            with self.lock:
                self.status["lastPoll"] = iso(time.time())
            time.sleep(POLL)

    def poll(self):
        self.resolve_lists()
        now = time.time()
        current, pending = {}, []
        for folder in self.folders:
            for ent in sf_list(folder):
                name = ent["name"]
                path = folder + "/" + name
                if name.startswith("."):
                    continue
                ext = os.path.splitext(name)[1].lower()
                if ext not in CONTENT_TYPES and ext not in HEIC:
                    if path not in self.warned:
                        self.warned.add(path)
                        log(f"skip {path}: unsupported file type, leaving it in place")
                    continue
                current[path] = (ent["size"], ent["mtime"])
        # Forget files that disappeared (moved/deleted by hand)
        for path in list(self.failed):
            if path not in current:
                del self.failed[path]
        for path, sig in current.items():
            if self.seen.get(path) != sig:
                log(f"new/changed {path} ({sig[0]} bytes), waiting for it to settle")
                pending.append(path)
                continue
            fail = self.failed.get(path)
            if fail and fail["next"] > now:
                pending.append(path)
                continue
            try:
                self.process(path, sig)
                self.failed.pop(path, None)
            except Exception as e:
                n = (fail["attempts"] if fail else 0) + 1
                wait = min(POLL * 2 ** (n - 1), MAX_BACKOFF)
                self.failed[path] = {"attempts": n, "error": str(e), "next": time.time() + wait}
                log(f"FAILED {path} (attempt {n}): {e}; retry in {wait}s")
                pending.append(path)
        self.seen = current
        with self.lock:
            self.status["pending"] = sorted(pending)

    def process(self, path, sig):
        size, mtime = sig
        folder, name = path.rsplit("/", 1)
        stem, ext = os.path.splitext(name)
        ext = ext.lower()
        list_id = self.list_ids[folder]

        data = sf_download(path)
        if len(data) != size:
            raise RuntimeError(f"downloaded {len(data)} bytes, Seafile lists {size}")
        sha = hashlib.sha256(data).hexdigest()

        if ext in HEIC:
            upload, upload_name, ctype = to_jpeg(data), stem + ".jpg", "image/jpeg"
            log(f"converted {path} HEIC -> JPEG ({len(data)} -> {len(upload)} bytes)")
        else:
            upload, upload_name, ctype = data, name, CONTENT_TYPES[ext]

        prefix = "DRY RUN: would " if DRY_RUN else ""
        entry = self.state.get(sha)
        bookmark = self.bookmark_exists(entry["bookmarkId"]) if entry else None
        if entry and not bookmark:
            log(f"{path}: bookmark {entry['bookmarkId']} from an earlier import is gone, importing again")
            entry = None

        if entry:
            log(f"{path}: duplicate of bookmark {entry['bookmarkId']} (imported {entry['importedAt']})")
            bookmark_id = entry["bookmarkId"]
        elif DRY_RUN:
            log(f"{prefix}upload {path} as {upload_name} ({ctype}, {len(upload)} bytes), "
                f"create bookmark dated {mtime}, add to list {self.folders[folder]!r}"
                + (f", tag {TAG!r}" if TAG else ""))
            log(f"{prefix}delete {path} from Seafile")
            return
        else:
            asset = self.api.upload_asset(upload_name, ctype, upload)
            if asset.get("size") != len(upload):
                raise RuntimeError(f"Karakeep stored {asset.get('size')} bytes, uploaded {len(upload)}")
            bm = self.kk("POST", "/bookmarks", {
                "type": "asset", "assetType": "image", "assetId": asset["assetId"],
                "title": stem, "fileName": upload_name, "createdAt": to_utc(mtime),
            }, key=self.key)
            bookmark_id = bm["id"]
            # Remember it before anything else can fail, so a retry reuses this bookmark.
            self.state[sha] = {"bookmarkId": bookmark_id, "name": name, "importedAt": iso(time.time())}
            self.save_state()
            log(f"imported {path} -> bookmark {bookmark_id} (asset {asset['assetId']})")

        if DRY_RUN:
            log(f"{prefix}ensure bookmark {bookmark_id} is in list {self.folders[folder]!r}, then delete {path}")
            return
        # Idempotent: safe to repeat on a retry.
        self.kk("PUT", f"/lists/{list_id}/bookmarks/{bookmark_id}", key=self.key)
        if TAG:
            self.kk("POST", f"/bookmarks/{bookmark_id}/tags", {"tags": [{"tagName": TAG}]}, key=self.key)

        # Verify before deleting anything.
        bm = self.bookmark_exists(bookmark_id)
        if not bm:
            raise RuntimeError(f"bookmark {bookmark_id} vanished after import")
        c = bm.get("content") or {}
        if c.get("type") != "asset" or c.get("assetType") != "image" or not c.get("assetId"):
            raise RuntimeError(f"bookmark {bookmark_id} is not an image asset: {c}")
        if not entry and c.get("size") != len(upload):
            raise RuntimeError(f"bookmark {bookmark_id} asset size {c.get('size')} != {len(upload)}")

        sf_delete(path)
        log(f"deleted {path} from Seafile (recycle bin)")
        with self.lock:
            self.status["imported"] += 1


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def to_utc(seafile_mtime):
    """Seafile gives e.g. 2026-10-31T07:12:00+01:00; Karakeep wants an ISO UTC instant."""
    try:
        return iso(datetime.fromisoformat(seafile_mtime).timestamp())
    except ValueError:
        return iso(time.time())


def start(karakeep_url, karakeep_key, kk):
    """Start the poller thread; returns the Inbox for status queries, or None if disabled."""
    if not enabled():
        log("disabled (set SEAFILE_URL, SEAFILE_TOKEN and INBOX_FOLDERS to enable)")
        return None
    inbox = Inbox(karakeep_url, karakeep_key, kk)
    if not DRY_RUN:
        inbox.save_state()  # fail at start-up, not mid-import, if the state dir is unwritable
    t = threading.Thread(target=inbox.run, name="inbox", daemon=True)
    t.start()
    return inbox
