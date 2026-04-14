# yoto-library

[![CI](https://github.com/steverice/yoto-library/actions/workflows/ci.yml/badge.svg)](https://github.com/steverice/yoto-library/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/yoto-library?logo=pypi&logoColor=white)](https://pypi.org/project/yoto-library/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-3776AB?logo=python&logoColor=white)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Platform: macOS](https://img.shields.io/badge/platform-macOS-lightgrey?logo=apple)](https://github.com)
[![Built with Claude Code](https://img.shields.io/badge/Built%20with-Claude%20Code-6b48ff?logo=anthropic)](https://claude.ai/code)
[![Conventional Commits](https://img.shields.io/badge/Conventional%20Commits-1.0.0-yellow?logo=conventionalcommits&logoColor=white)](https://conventionalcommits.org)
[![Gitmoji](https://img.shields.io/badge/gitmoji-%20😜%20😍-FFDD67)](https://gitmoji.dev)

Manage Yoto Player Create-Your-Own (CYO) playlists as folders on disk, with two-way sync to the Yoto API.

Each playlist is a folder. Audio files live in MKA containers that carry metadata and icon attachments. Cover art and track icons are auto-generated via AI when missing. Sync pushes local state to Yoto; pull downloads remote state to a local folder.

## Prerequisites

**Required:**

- Python 3.10+
- [FFmpeg](https://ffmpeg.org/) — audio processing, MKA muxing, silence detection
- [mkvtoolnix](https://mkvtoolnix.download/) — MKA metadata and attachment read/write

**Optional:**

- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — YouTube audio downloads (for `.webloc` support)
- [bsdiff](https://github.com/nicupavel/bsdiff) — byte-perfect export patches (install via `brew install bsdiff`; `bspatch` ships with macOS)

**AI services (for icon and cover art generation):**

- [Claude CLI](https://docs.anthropic.com/en/docs/claude-code) — descriptions, icon matching, cover text quality checks (uses your Claude subscription)
- `node` (v18+) — required for web scraping lyrics sources. Install via `brew install node` or from [nodejs.org](https://nodejs.org). Also requires `jsdom`: `npm install jsdom` (run once in the yoto-library directory).
- [RetroDiffusion](https://www.retrodiffusion.ai/) — 16x16 pixel art icon generation (`RETRODIFFUSION_API_KEY`)
- [Together AI](https://www.together.ai/) — album art recomposition via FLUX Kontext (`TOGETHER_API_KEY`)
- [Google Gemini](https://aistudio.google.com/) — text rendering for cover art repair (`GEMINI_API_KEY`)
- [OpenAI](https://platform.openai.com/) — text-to-image cover generation when no album art exists (`OPENAI_API_KEY`)

On macOS with Homebrew:

```
brew install ffmpeg mkvtoolnix yt-dlp
```

## Installation

```
pip install yoto-library
```

This installs the `yoto` command.

To install from source (requires [uv](https://docs.astral.sh/uv/)):

```
git clone https://github.com/steverice/yoto-library.git
cd yoto-library
uv sync
```

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
yoto sync --print             # print cover art after sync
yoto sync --no-print          # skip print prompt
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

### `yoto export <playlist>`

Export MKA tracks back to their original audio format. If a `source.patch` attachment exists, the export is byte-perfect via bspatch.

```
yoto export "Bedtime Songs"                    # export to Bedtime Songs-exported/
yoto export "Bedtime Songs" -o ~/Music/out     # export to a specific folder
```

### `yoto lyrics <playlist>`

Fetch and store lyrics for tracks in a playlist. Checks source tags first, then falls back to the LRCLIB API.

```
yoto lyrics "Bedtime Songs"           # fetch missing lyrics
yoto lyrics "Bedtime Songs" --force   # re-fetch all lyrics
yoto lyrics "Bedtime Songs" --show    # display stored lyrics in a pager
yoto lyrics "Bedtime Songs" --clear   # remove stored lyrics from tracks
```

### `yoto cover [path]`

Generate cover art for a playlist folder without syncing.

```
yoto cover                          # generate cover in current directory
yoto cover --style cartoon          # generate with a specific art style
yoto cover --force                  # regenerate even if cover.png exists
yoto cover --backup                 # rename existing cover.png before generating
yoto cover --ignore-album-art       # skip album art reuse, generate from prompt
```

Available styles: `cartoon`, `cel`, `chalk`, `crayon`, `gouache`, `papercraft`, `storybook` (default), `watercolor`. The chosen style is saved to `.yoto-style` in the playlist folder and reused on future regenerations.

### `yoto reorder [playlist]`

Open `playlist.jsonl` in `$EDITOR` to reorder tracks. Validates JSON on save. Defaults to `playlist.jsonl` in the current directory.

### `yoto select-icon <track>`

Interactive icon selection for a single track (or multiple tracks in sequence). Generates three pixel art icon options, finds the best match from Yoto's icon catalog, scores all candidates with an LLM, and displays them in bordered panels for selection. Pick one or press `r` to regenerate.

### `yoto reset-icon <tracks...>`

Remove icon attachments from one or more MKA files. The next `yoto sync` will regenerate icons for those tracks.

### `yoto print [path]`

Print cover art to a photo printer via macOS CUPS. Optionally applies an ICC color profile for accurate color reproduction.

```
yoto print                                  # print cover in current directory
yoto print "Bedtime Songs"                  # print a specific playlist's cover
yoto print --yes                             # skip confirmation prompt
yoto print --profile /path/to/profile.icc   # use ICC color profile
```

If no `cover.png` exists, offers to generate one first. If a profile is specified but not found, warns and offers to continue without color management.

### `yoto lyrics [path]`

Fetch and embed lyrics for tracks in a playlist folder. Tries embedded tags, then user-configured web scraping sources, then the LRCLIB public API.

```
yoto lyrics                          # fetch lyrics for current directory
yoto lyrics "Bedtime Songs"          # fetch lyrics for a specific folder
yoto lyrics --clear                  # remove stored lyrics from tracks
yoto lyrics --add-source <url>       # analyze a website and add it as a lyrics source
```

### `yoto billing`

Show provider balances, subscription usage, and lifetime cost tracking.

```
yoto billing                    # show all billing info
yoto billing --reset            # reset all lifetime cost data
yoto billing --reset openai     # reset costs for a specific provider group
```

### `yoto completions [shell]`

Print the shell completion setup command for zsh, bash, or fish. Auto-detects your shell if not specified.

## Configuration

Authentication is handled via macOS Keychain — no config file needed. Run `yoto auth` to log in.

AI services require API keys set as environment variables (e.g., in `.env` at the project root):

```
RETRODIFFUSION_API_KEY=...   # retrodiffusion.ai — icon generation
TOGETHER_API_KEY=...         # together.ai — album art recomposition (FLUX Kontext)
GEMINI_API_KEY=...           # aistudio.google.com — text layer rendering
OPENAI_API_KEY=...           # platform.openai.com — text-to-image cover generation
```

Each service handles a specific part of the pipeline — see [AI providers](#ai-providers) below.

**Lyrics sources** — web scraping configs live in `~/.yoto/lyrics/*.json`. Each file defines one source. Use `yoto lyrics --add-source <url>` to add a new source interactively — Claude analyzes the site's HTML and generates the JS snippets automatically.

**Printing** — requires a photo printer configured in macOS System Settings:

- `YOTO_PRINTER` — CUPS printer name (default: `Canon_SELPHY_CP1300`)
- `YOTO_ICC_PROFILE` — path to ICC color profile (optional; skips color management if unset)

## How it works

**Sync flow** — loads local playlist state (tracks, metadata, cover, description), fetches remote state, diffs them, then uploads changed audio through Yoto's transcode pipeline, uploads icons and cover art, and POSTs the assembled content JSON.

**Pull flow** — fetches the remote card with signed audio URLs, downloads tracks in parallel, wraps each in MKA with metadata and icon attachments, and writes `playlist.jsonl` and `cover.png`.

**Icon pipeline** — if a track already has an icon attachment in its MKA, that icon is used. Otherwise, the track title is matched against Yoto's public icon catalog via LLM. High-confidence matches are used directly; lower-confidence matches are compared against 3 AI-generated alternatives (via RetroDiffusion pixel art). The LLM picks the winner.

**Cover art** — if `cover.png` is missing, the tool first checks whether all tracks share identical embedded album art (e.g., from a ripped CD or tagged album). If so, FLUX Kontext recomposes the square art into a 638x1011 portrait layout. Claude checks the result for text quality — if text is mangled after 3 attempts, a repair pipeline kicks in: Claude OCRs the original text, Gemini renders a styled text layer, Claude picks placement coordinates, and PIL composites the text onto the artwork. If no shared album art exists, OpenAI generates a cover from scratch using track metadata. Delete `cover.png` to regenerate.

Text-to-image covers accept a `--style` flag to control the visual art direction (e.g., `cartoon`, `watercolor`, `gouache`). The choice is persisted in `.yoto-style` so future regenerations reuse it.

Use `yoto cover --force` to regenerate an existing cover. Set `YOTO_RECOMPOSE_ATTEMPTS` (default 3) to control how many FLUX attempts before falling back to the text repair pipeline.

- `YOTO_WORKERS` — max parallel workers for downloads, uploads, imports, exports (default: 4)

**YouTube downloads** — drop a `.webloc` file (Safari bookmark) into a playlist folder. On `yoto sync` or `yoto download`, the URL is resolved via yt-dlp, silence is trimmed from pre/post-roll, and the audio is wrapped in MKA. The `.webloc` is deleted after successful download.

## AI providers

Each AI task uses a specific provider chosen for best results at that task:

| Task | Provider | Key | Pricing |
|------|----------|-----|---------|
| Icon generation (16x16 pixel art) | [RetroDiffusion](https://www.retrodiffusion.ai/) | `RETRODIFFUSION_API_KEY` | ~$0.003/image |
| Album art recomposition | [FLUX Kontext Pro](https://www.together.ai/) via Together AI | `TOGETHER_API_KEY` | ~$0.04/image |
| Text layer rendering (cover repair) | [Gemini 2.5 Flash](https://aistudio.google.com/) | `GEMINI_API_KEY` | ~$0.07/image |
| Text-to-image cover generation | [OpenAI gpt-image-1.5](https://platform.openai.com/) | `OPENAI_API_KEY` | ~$0.013/image |
| Descriptions, icon matching, cover evaluation (Sonnet) | [Claude CLI](https://docs.anthropic.com/en/docs/claude-code) | Subscription | Included with Claude subscription |

**Getting API keys:**

- **RetroDiffusion** — sign up at [retrodiffusion.ai](https://www.retrodiffusion.ai/), key is on your dashboard
- **Together AI** — sign up at [together.ai](https://www.together.ai/), create key in Settings → API Keys
- **Gemini** — create a key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey) (free tier available)
- **OpenAI** — sign up at [platform.openai.com](https://platform.openai.com/), create key in API Keys
- **Claude CLI** — install from [docs.anthropic.com](https://docs.anthropic.com/en/docs/claude-code), uses your Claude subscription

> **Important:** If `ANTHROPIC_API_KEY` is set in your environment, the Claude CLI will use it for API-key billing instead of your subscription. This can result in significant per-token charges. Make sure `ANTHROPIC_API_KEY` is **not** set (or excluded from your `.env`) to use your Claude subscription.

## iTerm2 icon display

If you use iTerm2, the `select-icon` command can improve pixel art rendering by fixing a color space issue in some iTerm2 color presets (notably "Dark Background"). To enable this:

1. Enable iTerm2's Python API: **Preferences > General > Magic > "Enable Python API"**

The `iterm2` Python package will be installed automatically on first use. Without the API enabled, icons display normally but may show faint horizontal banding.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, commit conventions, and release process.

## Architecture

Two-layer design: `yoto_lib` (standalone library) and `yoto_cli` (thin Click wrapper). See [ARCHITECTURE.md](ARCHITECTURE.md) for details.

## Disclaimer

This project is not affiliated with, endorsed by, or associated with Yoto Limited. "Yoto" is a trademark of Yoto Limited.

## License

MIT. See [LICENSE](LICENSE).
