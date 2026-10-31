# Karakeep Tagger

A fast, keyboard-driven way to tag the image bookmarks in a self-hosted
[Karakeep](https://karakeep.app) instance. Select one image or hundreds in a grid,
type a tag with autocomplete, and move on.

It's a small Python server using only the standard library for tagging, plus two static
HTML pages. Moodboard PDFs and the inbox's HEIC conversion use a few optional packages.
Karakeep stays the source of truth: every change goes straight to Karakeep's API. The only
thing the tagger stores itself is the layout of your moodboards.

Optionally, it can also watch folders in a [Seafile](https://www.seafile.com) library and
import every image you drop there into Karakeep, see [Inbox import](#inbox-import).

> This is an unofficial companion app, not part of the Karakeep project.
> Tested with Karakeep 0.34.1.

## Features

- Grid of every image bookmark, with a tag filter (a tag or "untagged") and a list filter (a list or "in no list") that combine; the tag filter only offers tags found in the chosen list
- Multi-select with click, Shift+click, arrow keys and ⌘/Ctrl+A
- Tag pop-over with autocomplete; add or remove a tag on all selected images at once
- Put the selected images into a Karakeep list, or take them out, in a separate list pop-over
- Full-size viewer for tagging one image after another without touching the mouse
- Partial tags (on some of the selected images) shown as dashed chips; click to apply to all
- Moodboards: collect images for a shoot into sections with a description, write a mood
  note under each image, and export the board as a PDF to send, see [Moodboards](#moodboards)
- Optional inbox: drop images into a Seafile library from any device, they become Karakeep
  image bookmarks in the list of your choice

## Quick start

You need Docker with Compose, and a Karakeep API key (Karakeep → Settings → API Keys).

```sh
git clone https://github.com/jameslafa/karakeep-tagger.git
cd karakeep-tagger
cp .env.example .env    # then edit .env, see below
docker compose up -d --build
```

Open http://localhost:8080.

By default the container is only reachable from the machine it runs on. To use it from
other devices, see [Remote access](#remote-access).

## Configuration

All settings go in `.env`:

| Setting | Required | Description |
|---|---|---|
| `KARAKEEP_URL` | yes | Base URL of your Karakeep instance, without `/api/v1` |
| `KARAKEEP_API_KEY` | yes | Karakeep API key |
| `TAGGER_HOSTS` | for remote access | Host names you open the tagger by, comma-separated, no port. `localhost` and `127.0.0.1` always work. |
| `TAGGER_PASSWORD` | no | Require this password (HTTP basic auth, any user name) |
| `TAGGER_PUBLISH` | no | Host address and port Docker publishes on. Default `127.0.0.1:8080` |
| `TAGGER_CONCURRENCY` | no | Parallel Karakeep requests for bulk tagging. Default `8` |
| `TAGGER_BOARDS_LIST` | no | Name of the top-level Karakeep list that holds the moodboards. Default `Moodboards` |

After changing `.env`, apply it with `docker compose up -d`.

Inbox import settings are listed in [Inbox import](#inbox-import).

## Remote access

### Tailscale

Keep the default `TAGGER_PUBLISH` and let Tailscale serve the app over HTTPS, reachable
only from your tailnet:

```sh
tailscale serve --bg --https=8444 http://127.0.0.1:8080
```

Then add your machine's Tailscale name to `.env`, for example
`TAGGER_HOSTS=myserver.tailnet-name.ts.net`, and open
`https://myserver.tailnet-name.ts.net:8444`. Pick any free port instead of 8444.

### Local network

Publish on all interfaces and list the names or IP addresses you'll use:

```sh
TAGGER_PUBLISH=0.0.0.0:8080
TAGGER_HOSTS=192.168.1.20,myserver.lan
```

Then open `http://192.168.1.20:8080`. This is plain HTTP, so only do it on a network you
trust, and consider setting `TAGGER_PASSWORD`.

## Security

**Anyone who can reach the tagger can see all your images and change their tags.** It
has no password unless you set `TAGGER_PASSWORD`. Keep it on a private network.

What it does to stay safe:

- The Karakeep API key stays on the server. The browser only talks to the tagger, which
  forwards a fixed set of calls: list bookmarks, tags and lists, fetch images, add or
  remove tags, add to or remove from lists. It only creates, renames or deletes lists
  under the moodboards list.
- Requests for host names not in `TAGGER_HOSTS` are refused (status 421), which blocks
  DNS rebinding attacks.
- Changes must be same-origin JSON requests, so other websites can't make your
  browser change tags, lists or moodboards.
- The page has a strict Content Security Policy and can't be framed; images are served
  with a sandbox policy, so an SVG with a script can't run.

To report a vulnerability, please open a private security advisory on GitHub rather
than a public issue.

## Moodboards

A moodboard is a sublist of a Karakeep list called `Moodboards`, created with your first
board. Open **Moodboards** in the header of the tagger to manage them.

1. Click *New board* and give it a title and a description (the idea, date, place,
   wardrobe…). The × next to a board's name in the sidebar deletes it.
2. Back in the tagger, choose the board in the header drop-down, select images and press
   `B` to add them (`Shift+B` removes them). The list pop-over works too.
3. On the board page, add sections with a title and a description, drag images into them
   and into order (or use an image's *Move…* menu, handy on a tablet), and write a
   mood note under each image. ★ makes an image the cover. Click an image to see it full
   size with its note beside it to edit; ← → (when the note is empty), PageUp/PageDown or
   ⌘/Ctrl+↑↓ step through the board, ⌘/Ctrl+Enter goes to the next image. Changes save
   automatically. The ⧉ next to a board in the sidebar duplicates it, with the same
   sections, images and notes.
4. **Download PDF** builds a PDF on the server and downloads it, in colour and without a
   print dialog. It is made for screens, with a dark gallery look: pages are A4 wide and as
   tall as their content, so it scrolls like a web page. The first page has the cover edge
   to edge, then the title and description; each section is one page with a numbered
   heading and its images in a masonry grid (each image goes into the shortest column),
   with their notes underneath. *PDF columns* sets the number of columns,
   1 to 4 (default 2); each section can override it with its own setting. A very long section continues on a second page, since PDF viewers
   don't open pages taller than about 5 m. Images are resized to 1600 px so the file stays
   small enough to send by chat or email.

New images land in *Unsorted*. Once a board has sections, *Unsorted* is a staging area
and is left out of the PDF; a board without sections prints everything in it.

The board's title, description and images live in Karakeep. Sections, order, notes, the
cover and the column counts are kept in a JSON file per board in the `/data` volume (`moodboards/<list id>.json`).
Deleting a board deletes its Karakeep list and that file, never the images.

## Inbox import

A Seafile library can act as an inbox for Karakeep. Add images to it from the Seafile
desktop client, the mobile app or the web, and the tagger imports them:

1. Every `INBOX_POLL_SECONDS` (default 60) it lists the folders in `INBOX_FOLDERS`.
   Hidden files, files in the library root and other folders are ignored.
2. A file is picked up once its size and modification time are unchanged across two polls.
3. It is downloaded, uploaded to Karakeep as an image bookmark dated with the file's
   modification time, and added to the Karakeep list mapped to its folder. Karakeep's own
   AI tagging runs as usual.
4. Only after Karakeep confirms the bookmark and the stored size, the file is deleted from
   Seafile. Deleted files go to the library's recycle bin.

If any step fails, nothing is deleted and the file is retried on later polls with backoff
(doubling from one poll interval up to an hour). Failures are logged and shown as a red
banner at the top of the tagger page; `GET /api/inbox/status` returns the same information
as JSON. Files of other types are left alone and logged once.

Accepted: JPEG, PNG, WebP, GIF, HEIC/HEIF. Karakeep cannot store HEIC, so HEIC photos
from an iPhone are converted to JPEG (quality 92, EXIF kept) before upload; the original
HEIC only survives in the recycle bin. This conversion needs the `pillow` and
`pillow-heif` packages, which the Docker image installs.

Every imported file's SHA-256 and bookmark id are recorded in a state file on the
`inbox-state` volume. Dropping the same image twice, or a restart between import and
deletion, therefore never creates a second bookmark: the file is matched to the existing
bookmark (re-imported if you deleted that bookmark in Karakeep) and removed from the inbox.

### Setup

1. In Seafile, open the library, choose *API Token* from its menu, and create a token with
   read-write permission. It can only access this one library.
2. Create the folders in the library and the matching lists in Karakeep.
3. Add to `.env`:

| Setting | Required | Description |
|---|---|---|
| `SEAFILE_URL` | yes | Seafile base URL as reached from the container, e.g. `http://host.docker.internal:8000` when Seafile runs in Docker on the same machine |
| `SEAFILE_TOKEN` | yes | Library-scoped API token |
| `INBOX_FOLDERS` | yes | `folder=List name` pairs, comma-separated, e.g. `photography=Photography,ropes=Ropes` |
| `INBOX_TAG` | no | Tag to add to every import. None by default |
| `INBOX_POLL_SECONDS` | no | Default `60` |
| `INBOX_DRY_RUN` | no | `true` logs what would be imported and deleted without changing anything |
| `KARAKEEP_INBOX_API_KEY` | no | Separate Karakeep API key for the importer, for easier revocation. Default: `KARAKEEP_API_KEY` |

Start with `INBOX_DRY_RUN=true`, check `docker compose logs -f`, then set it to `false`.
The inbox is off unless `SEAFILE_URL`, `SEAFILE_TOKEN` and `INBOX_FOLDERS` are all set.

### Recovering a file from the recycle bin

In Seafile's web interface open the library, click the *Trash* icon at the top of the file
list, find the file and choose *Restore*. How long deleted files stay there is the
library's *History Setting* (library menu → History Setting); with a limit of 30 days
anything older is removed by Seafile's garbage collection. Restored files are imported
again on the next poll unless the bookmark still exists, in which case they are only
removed from the inbox again. To keep a restored file, move it out of the inbox folders.

## Running without Docker

```sh
set -a; . ./.env; set +a
python3 server.py
```

Python 3.8 or newer, no dependencies for tagging. The port is `PORT` (default `8080`).
Note that the server listens on all network interfaces; `TAGGER_PUBLISH` only applies to
Docker. For moodboards and the inbox, set `TAGGER_DATA_DIR` to a writable directory
(default `/data`). `pip install pillow reportlab` for moodboard PDFs and resized images
on board pages; add `pillow-heif` if you want HEIC files converted.

## Keyboard shortcuts

Grid
- Click: toggle selection. Shift+click: select a range. ⌘/Ctrl+A: select all. Esc: clear.
- Arrow keys: move focus (Shift extends the selection). Space: toggle. Home/End: first/last image.
- `T`: tag pop-over for the selection (or the focused image). `L`: list pop-over for the
  same images. Both scroll when long and resize from the bottom-right corner.
- Enter, `O` or double-click: open viewer.
- `F`: focus the tag filter. `R`: re-apply the filter (images you just tagged
  stay visible and dimmed until you do).
- `B`: add the selection (or the focused image) to the active moodboard. `Shift+B`: remove it.

Tag field (pop-over and viewer)
- Type to autocomplete from existing tags. ↑↓ choose, Tab completes, Enter adds.
  The top suggestion is used on Enter, so use ↓ to pick "Create …" for a new name
  that is a prefix of an existing tag.
- `-name` removes a tag. Chips: × removes; clicking a dashed (partial) chip adds
  it to all selected images.
- Viewer: ←/→ (when the field is empty) or PageUp/PageDown go to the previous/next
  image. Enter on an empty field goes to the next one. `L` focuses the list picker.
  `Alt+B` adds the image to the active moodboard, `Shift+Alt+B` removes it. Esc closes.

Lists
- The list pop-over and the viewer show the lists the image is in as green chips; × removes it.
  A dashed chip means only some of the selected images are in that list; click to add all.
- Pick a list from the drop-down to add all selected images. Nested lists show as
  "Parent / Child". Smart lists are shown but can't be edited.

## Notes

- Only bookmarks with `content.type = asset` and `assetType = image` are shown.
- Karakeep has no thumbnail endpoint, so the grid loads full-size images: at most 8 at a
  time, nearest to the screen first. The browser caches them.
- Bulk changes are sent as one request per bookmark (`TAGGER_CONCURRENCY` in
  parallel). Each bookmark is then re-read, so the page shows Karakeep's actual state.
- Karakeep doesn't report lists on bookmarks, so on load the tagger reads every list's
  contents once (one request per 100 bookmarks per list).

## License

[MIT](LICENSE). The fonts in `fonts/` (Cormorant Garamond and Inter, fixed weights cut from
the Google Fonts releases) are under the SIL Open Font License, see the `OFL-*.txt` files
next to them.
