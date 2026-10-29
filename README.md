# Karakeep Tagger

A small companion app for tagging image bookmarks in a self-hosted
[Karakeep](https://karakeep.app) instance quickly. It's one Python file using only
the standard library, plus one static HTML page. Karakeep is the source of truth;
this app stores nothing.

The browser only talks to this server. The server holds the Karakeep API key and
proxies a fixed allowlist of calls: list bookmarks, list tags, fetch image assets,
and add/remove bookmark tags.

## Run

```sh
cp .env.example .env   # fill in KARAKEEP_URL, KARAKEEP_API_KEY and TAGGER_HOSTS
docker compose up -d --build
# → http://localhost:8080
```

Without Docker: `set -a; . ./.env; set +a; python3 server.py` (Python ≥ 3.8).

Anyone who can reach the port can read your images and change their tags, so
keep it on your LAN or tailnet, or set `TAGGER_PASSWORD`.

The app only answers requests for `localhost`, `127.0.0.1` and the host names in
`TAGGER_HOSTS` (port ignored); anything else gets a 421. Add every name you open it
by, such as a Tailscale or LAN name.

## Keys

Grid
- Click: toggle selection. Shift+click: select a range. ⌘/Ctrl+A: select all. Esc: clear.
- Arrow keys: move focus (Shift extends the selection). Space: toggle.
- `T`: tag pop-over for the selection (or the focused image). Enter or double-click: open viewer.
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
- The grid loads full-size images lazily. Karakeep has no thumbnail endpoint,
  and responses are cached by the browser as immutable.
- Bulk changes are sent as one request per bookmark (`TAGGER_CONCURRENCY` in
  parallel). Each bookmark is then re-read, so the UI shows Karakeep's actual state.
