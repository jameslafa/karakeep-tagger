# Karakeep Tagger

A fast, keyboard-driven way to tag the image bookmarks in a self-hosted
[Karakeep](https://karakeep.app) instance. Select one image or hundreds in a grid,
type a tag with autocomplete, and move on.

It's one Python file using only the standard library, plus one static HTML page.
Karakeep stays the source of truth: the tagger stores nothing and every change goes
straight to Karakeep's API.

> This is an unofficial companion app, not part of the Karakeep project.
> Tested with Karakeep 0.34.1.

## Features

- Grid of every image bookmark, filterable by tag or "untagged"
- Multi-select with click, Shift+click, arrow keys and ⌘/Ctrl+A
- Tag pop-over with autocomplete; add or remove a tag on all selected images at once
- Full-size viewer for tagging one image after another without touching the mouse
- Partial tags (on some of the selected images) shown as dashed chips; click to apply to all

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

After changing `.env`, apply it with `docker compose up -d`.

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
  forwards a fixed set of calls: list bookmarks, list tags, fetch images, add or remove tags.
- Requests for host names not in `TAGGER_HOSTS` are refused (status 421), which blocks
  DNS rebinding attacks.
- Tag changes must be same-origin JSON requests, so other websites can't make your
  browser change tags.
- The page has a strict Content Security Policy and can't be framed; images are served
  with a sandbox policy, so an SVG with a script can't run.

To report a vulnerability, please open a private security advisory on GitHub rather
than a public issue.

## Running without Docker

```sh
set -a; . ./.env; set +a
python3 server.py
```

Python 3.8 or newer, no dependencies. The port is `PORT` (default `8080`). Note that the
server listens on all network interfaces; `TAGGER_PUBLISH` only applies to Docker.

## Keyboard shortcuts

Grid
- Click: toggle selection. Shift+click: select a range. ⌘/Ctrl+A: select all. Esc: clear.
- Arrow keys: move focus (Shift extends the selection). Space: toggle. Home/End: first/last image.
- `T`: tag pop-over for the selection (or the focused image). Enter, `O` or double-click: open viewer.
- `F`: focus the tag filter. `R`: re-apply the filter (images you just tagged
  stay visible and dimmed until you do).

Tag field (pop-over and viewer)
- Type to autocomplete from existing tags. ↑↓ choose, Tab completes, Enter adds.
  The top suggestion is used on Enter, so use ↓ to pick "Create …" for a new name
  that is a prefix of an existing tag.
- `-name` removes a tag. Chips: × removes; clicking a dashed (partial) chip adds
  it to all selected images.
- Viewer: ←/→ (when the field is empty) or PageUp/PageDown go to the previous/next
  image. Enter on an empty field goes to the next one. Esc closes.

## Notes

- Only bookmarks with `content.type = asset` and `assetType = image` are shown.
- Karakeep has no thumbnail endpoint, so the grid loads full-size images: at most 8 at a
  time, nearest to the screen first. The browser caches them.
- Bulk changes are sent as one request per bookmark (`TAGGER_CONCURRENCY` in
  parallel). Each bookmark is then re-read, so the page shows Karakeep's actual state.

## License

[MIT](LICENSE)
