# yoto-library

Manage Yoto Player Create-Your-Own (CYO) playlists as folders on disk, with two-way sync to the Yoto API.

Each playlist is a folder. Audio files live in MKA containers that carry metadata and icon attachments. Cover art and track icons are auto-generated via AI when missing. Sync pushes local state to Yoto; pull downloads remote state to a local folder.

## Prerequisites

**Required:**

- Python 3.10+
- [FFmpeg](https://ffmpeg.org/) — audio processing, MKA muxing, silence detection
- [mkvtoolnix](https://mkvtoolnix.download/) — MKA metadata and attachment read/write

**Optional:**

- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — YouTube audio downloads (for `.webloc` support)
- [Claude CLI](https://docs.anthropic.com/en/docs/claude-code) — auto-generated descriptions, LLM-based icon matching

On macOS with Homebrew:

```
brew install ffmpeg mkvtoolnix yt-dlp
```

## Installation

```
git clone <repo-url>
cd yoto-library
pip install -e .
```

This installs the `yoto` command.

## Quick start

**Authenticate:**

```
yoto auth
```

Opens a device code flow — visit the URL shown, enter the code, and your token is saved to macOS Keychain.

**Pull an existing playlist:**

```
yoto pull abc12
```

Downloads the playlist with card ID `abc12` into a local folder.

**Create a new playlist:**

```
yoto init "Bedtime Songs"
```

Scaffolds an empty playlist folder. Add `.mka` audio files, then sync.

**Import existing audio files:**

```
yoto import ~/Music/album -o "Road Trip Mix"
```

Converts a folder of audio files (MP3, FLAC, WAV, etc.) into MKA-wrapped tracks in a new playlist folder.

**Sync to Yoto:**

```
yoto sync "Bedtime Songs"
```

Pushes local state to the Yoto API — uploads audio, generates icons and cover art if missing, and creates or updates the card.

## Playlist folder structure

```
Bedtime Songs/
  playlist.jsonl          # track order (JSON strings, one per line)
  description.txt         # playlist description, ≤500 chars (auto-generated if missing)
  cover.png               # 638x1011 portrait cover art (auto-generated if missing)
  .yoto-card-id           # Yoto card ID (written on first sync or pull)
  lullaby.mka             # audio + metadata + icon attachment
  twinkle-twinkle.mka
  rock-a-bye.mka
```

**playlist.jsonl** — bare JSON strings defining track order. Auto-generated alphabetically if absent. Editable with `yoto reorder` or any text editor.

```
"lullaby.mka"
"twinkle-twinkle.mka"
"rock-a-bye.mka"
```

**MKA containers** — Matroska Audio files that losslessly wrap any audio codec. Each MKA carries the original audio stream, Matroska metadata tags (artist, language, etc.), and a 16x16 icon attachment (PNG or animated GIF).

## Commands

### `yoto auth`

Authenticate with Yoto via OAuth device code flow. Tokens are stored in macOS Keychain.

### `yoto sync [path]`

Push local playlist state to Yoto. Uploads new/changed audio, icons, and cover art. Creates or updates the remote card.

```
yoto sync                     # sync playlist in current directory
yoto sync "Bedtime Songs"     # sync a specific folder
yoto sync --dry-run           # preview changes without executing
yoto sync --no-trim           # skip silence trimming on YouTube downloads
```

### `yoto pull [path | card-id]`

Pull remote playlist state to a local folder. Downloads audio, wraps in MKA, sets icons.

```
yoto pull abc12               # pull by card ID into a new folder
yoto pull "Bedtime Songs"     # update an existing linked folder from remote
yoto pull --all               # pull all playlists into subdirectories of cwd
yoto pull --dry-run           # preview changes
```

### `yoto status [path]`

Show the diff between local and remote state for a playlist.

### `yoto list`

Show all MYO cards on the authenticated Yoto account (card ID, title, track count).

### `yoto init [path]`

Scaffold a new empty playlist folder with an empty `playlist.jsonl`. Defaults to current directory.

### `yoto import <source>`

Bulk import: convert a folder of audio files into a playlist with MKA-wrapped tracks.

```
yoto import ~/Music/album                       # in-place
yoto import ~/Music/album -o "Road Trip Mix"    # into a new folder
```

### `yoto download [path]`

Resolve `.webloc` URLs in a playlist folder — downloads audio (via yt-dlp), trims silence, and wraps in MKA. Does not sync to Yoto.

```
yoto download                 # process .webloc files in current directory
yoto download --no-trim       # skip silence trimming
```

### `yoto reorder [playlist]`

Open `playlist.jsonl` in `$EDITOR` to reorder tracks. Validates JSON on save. Defaults to `playlist.jsonl` in the current directory.

### `yoto select-icon <track>`

Interactive icon selection for a single track. Generates 3 AI icon candidates, shows the best Yoto catalog match as a 4th option, and displays all four side-by-side in the terminal. Pick one or press `r` to regenerate.

### `yoto reset-icon <tracks...>`

Remove icon attachments from one or more MKA files. The next `yoto sync` will regenerate icons for those tracks.

### `yoto completions [shell]`

Print the shell completion setup command for zsh, bash, or fish. Auto-detects your shell if not specified.

## Configuration

Authentication is handled via macOS Keychain — no config file needed. Run `yoto auth` to log in.

AI image generation requires API keys in a `.env` file at the project root:

```
OPENAI_API_KEY=...           # cover art via OpenAI
GOOGLE_API_KEY=...           # cover art via Gemini
RETRODIFFUSION_API_KEY=...   # icon generation
TOGETHER_AI_KEY=...          # alternative icon generation
```

Select the image provider for cover art generation:

```
YOTO_IMAGE_PROVIDER=openai    # default; also supports "gemini"
```

## How it works

**Sync flow** — loads local playlist state (tracks, metadata, cover, description), fetches remote state, diffs them, then uploads changed audio through Yoto's transcode pipeline, uploads icons and cover art, and POSTs the assembled content JSON.

**Pull flow** — fetches the remote card with signed audio URLs, downloads tracks in parallel, wraps each in MKA with metadata and icon attachments, and writes `playlist.jsonl` and `cover.png`.

**Icon pipeline** — if a track already has an icon attachment in its MKA, that icon is used. Otherwise, the track title is matched against Yoto's public icon catalog via LLM. High-confidence matches are used directly; lower-confidence matches are compared against 3 AI-generated alternatives (via RetroDiffusion pixel art). The LLM picks the winner.

**Cover art** — if `cover.png` is missing, the tool first checks whether all tracks share identical embedded album art (e.g., from a ripped CD or tagged album). If so, it resizes that artwork to 638x1011 and uses it directly. Otherwise, a description is auto-generated from track metadata via Claude CLI, then an image is generated via the configured provider and cropped to fit. Delete `cover.png` to regenerate.

**YouTube downloads** — drop a `.webloc` file (Safari bookmark) into a playlist folder. On `yoto sync` or `yoto download`, the URL is resolved via yt-dlp, silence is trimmed from pre/post-roll, and the audio is wrapped in MKA. The `.webloc` is deleted after successful download.

## Development

Install dev dependencies:

```
pip install -e ".[dev]"
```

Run tests:

```
python -m pytest
```

Integration tests (require network access and yt-dlp) are marked separately:

```
python -m pytest -m integration
```

## Architecture

Two-layer design: `yoto_lib` is a standalone Python library; `yoto_cli` is a thin Click wrapper. The library is importable independently — no CLI framework dependency leaks into library code.

```
src/
  yoto_lib/
    auth.py              # OAuth device code flow, token refresh, Keychain storage
    api.py               # Yoto API client (content CRUD, upload pipeline, media)
    playlist.py          # local playlist model (folder ↔ Yoto content schema)
    sync.py              # local → remote sync engine
    pull.py              # remote → local pull engine
    mka.py               # MKA container: wrap, tags, attachments
    icons.py             # icon pipeline: matching, generation, ICNS
    icon_llm.py          # LLM-based icon matching via Claude CLI
    icon_catalog.py      # local cache for Yoto public icon catalog
    cover.py             # AI cover art generation
    description.py       # auto-generated playlist descriptions
    sources/             # source providers (.webloc → audio)
      youtube.py         # YouTube via yt-dlp
    image_providers/     # pluggable AI image generation
      openai_provider.py
      gemini_provider.py
      retrodiffusion_provider.py
      together_provider.py
      dalle2_provider.py
  yoto_cli/
    main.py              # Click CLI: all commands
```
