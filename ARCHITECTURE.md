# Architecture

## MKA as transparent storage

Yoto Library uses MKA (Matroska Audio) as its internal storage format. Each track is a single `.mka` file containing:

```
track.mka
├── audio stream    — original codec, untouched (AAC, MP3, FLAC, etc.)
├── Matroska tags   — TITLE, ARTIST, YOTO_SOURCE_FORMAT, etc.
├── icon attachment  — PNG or GIF, named "icon"
└── source.patch    — bsdiff patch for byte-perfect export (optional)
```

MKA is chosen because Matroska supports arbitrary metadata tags, file attachments, and any audio codec — all in a single container with standard tooling (mkvtoolnix, ffmpeg).

## Transparent middleman principle

MKA is never exposed to external systems. It exists solely as enriched local storage:

- **Yoto gets native audio.** On upload, `extract_audio()` remuxes the MKA back to its native container (e.g., `.m4a` for AAC) via `ffmpeg -c copy`. Yoto's transcode API receives exactly the same file it would get from a direct upload.

- **Export gives back the original.** `yoto export` extracts audio and applies a stored `bsdiff` patch to reconstruct the original file byte-for-byte.

```
Import:  original.m4a ──wrap──> track.mka + source.patch
Upload:  track.mka ──extract──> track.m4a ──upload──> Yoto API
Export:  track.mka ──extract──> track.m4a ──bspatch──> original.m4a
```

## Source format tag

Every MKA stores a `YOTO_SOURCE_FORMAT` tag (e.g., `m4a`, `mp3`, `flac`) recording the original file's container format. This is used by `extract_audio()` to choose the correct output format without needing to probe the codec.

## Binary diff/patch (bsdiff)

At import time, the pipeline:

1. Wraps the source file in MKA
2. Extracts it back via `ffmpeg -c copy` (deterministic reconstruction)
3. Computes `bsdiff(reconstructed, original)` — typically 50-100KB
4. Stores the patch as an MKA attachment named `source.patch`

The patch is computed against the **extracted audio stream**, not the MKA file itself. Since the audio stream never changes regardless of tag/icon edits to the MKA, the patch remains valid through the file's entire lifecycle.

**Dependencies:**
- `bsdiff` — required at import time for patch generation. Optional; if missing, import succeeds but export won't be byte-perfect. Install via `brew install bsdiff`.
- `bspatch` — required at export time. Ships with macOS (no install needed).

## Codec-to-container mapping

`extract_audio()` maps audio codecs to their native containers:

| Codec | Extension | ffmpeg format |
|-------|-----------|---------------|
| AAC   | .m4a      | ipod          |
| ALAC  | .m4a      | ipod          |
| MP3   | .mp3      | mp3           |
| Opus  | .ogg      | ogg           |
| Vorbis| .ogg      | ogg           |
| FLAC  | .flac     | flac          |
| PCM   | .wav      | wav           |

Unknown codecs fall back to MP3 transcoding as a safe default.

## Deterministic extraction (critical for bsdiff)

The bsdiff round-trip only works if `extract_audio()` produces **identical output** every time it's called on the same MKA, regardless of what metadata or attachments have been added since the patch was generated. This required solving three separate problems with ffmpeg's default behavior:

### Problem 1: MKA attachments are treated as video streams

When an MKA file has attachments (icon PNGs, the source.patch itself), ffmpeg sees them as additional streams. A PNG attachment becomes a "video" stream. Without explicit stream selection, ffmpeg tries to copy the PNG into the output container (e.g., M4A), which fails because the M4A muxer can't handle image dimensions it doesn't know about:

```
Stream #0:2 -> #0:0 (copy)     ← PNG attachment
[ipod @ ...] dimensions not set
Could not write header: Invalid argument
```

**Fix:** `-map 0:a` — explicitly select only audio streams, ignoring attachments and any other non-audio data.

### Problem 2: MKA metadata leaks into extracted output

By default, ffmpeg copies container-level metadata (Matroska tags like TITLE, ARTIST) into the output file's metadata. This means adding or changing tags in the MKA produces a different extracted file — the audio bytes are the same, but the container metadata differs, changing the file's hash:

```
# After wrap + write_tags({source_format: m4a})
extract → 16,191 bytes, hash 6edcb74c...

# After write_tags({source_format: m4a, title: Test})
extract → 16,219 bytes, hash 7e7b5ec7...   ← different!
```

**Fix:** `-map_metadata -1` — strip all metadata from the output. The extracted file contains only the raw audio stream in its native container, making it independent of whatever tags are stored in the MKA.

### Problem 3: OGG muxer assigns random serial numbers

The OGG container format uses a serial number in its page headers. ffmpeg's OGG muxer generates a new random serial number on every invocation, so two extractions of the same audio produce different files:

```
extract_audio(mka, out1) → hash f4cb9131...
extract_audio(mka, out2) → hash 005e66da...  ← different serial number
```

**Fix:** `-fflags +bitexact` — forces ffmpeg to use deterministic values for fields that would otherwise be random (OGG serial numbers, MP4 creation timestamps, etc.).

### The full extraction command

All three fixes combine into the extraction command used by `extract_audio()`:

```
ffmpeg -y -i track.mka \
    -map 0:a \              # only audio streams (ignore attachments)
    -c copy \               # no re-encoding
    -map_metadata -1 \      # strip MKA metadata from output
    -fflags +bitexact \     # deterministic muxer output
    -f ipod track.m4a       # native container format
```

This produces byte-identical output regardless of what tags, icons, or other attachments have been added to the MKA since the bsdiff patch was generated.

## Provider architecture

All external service integrations extend the `Provider` abstract base class
(`src/yoto_lib/providers/base.py`). Each provider implements a `check_status()`
classmethod that reports its health -- this might check a statuspage.io API,
verify a CLI tool is on PATH, or simply return healthy.

```
Provider (ABC)
  ├── check_status() -> ProviderStatus   (classmethod, abstract)
  └── subclasses: OpenAI, Flux, RetroDiffusion, Claude

StatusPageMixin
  └── check_status() via _fetch_statuspage()   (cached 5 min, thread-safe)
  └── Used by: OpenAIProvider, ClaudeProvider
```

The `@check_status_on_error` decorator goes on **logic functions** that use
providers. On error (exception or None return), it calls `check_status()` on
each listed provider and logs a warning if any are unhealthy:

```python
@check_status_on_error(ClaudeProvider)
def compare_icons_llm(track_title, candidates, ...): ...
```

This keeps provider health concerns completely out of business logic. Adding a
new provider: extend `Provider`, implement `check_status()` (use `StatusPageMixin`
if backed by statuspage.io), add the decorator to functions that use it.

## AI provider strategy

Each AI-powered feature uses a hardcoded provider chosen for best results at that specific task. Providers are not interchangeable — the pipeline depends on each model's specific strengths.

**Icon pipeline:** RetroDiffusion generates 16x16 pixel art icons. Claude CLI (Haiku) matches track titles to Yoto's catalog; Claude CLI (Sonnet) compares candidates visually.

**Cover recomposition pipeline** (when shared album art exists):

1. **FLUX Kontext Pro** (Together AI) — recomposes square album art into portrait layout. Retried up to 3 times (configurable via `YOTO_RECOMPOSE_ATTEMPTS`).
2. **Claude CLI** (Sonnet) — checks if text survived the recomposition.
3. **Claude CLI** (Sonnet) — compares padded vs recomposed versions, picks the better one.
4. If text is mangled after all attempts, the repair pipeline runs:
   - **Claude CLI** (Sonnet) — OCRs original album text
   - **Gemini 2.5 Flash Image** (AI Studio) — renders styled text on black background (called inline via `google-genai` SDK, not through a Provider subclass)
   - **Claude CLI** (Sonnet) — picks placement coordinates on the portrait image
   - **PIL** — chroma keys black background and composites text at coordinates

All Claude CLI calls in the cover pipeline use Sonnet for better visual judgment and OCR accuracy.

**Text-to-image cover generation** (when no shared album art exists): A two-step pipeline using OpenAI `gpt-image-1.5`:

1. **Generate illustration** — creates a cover illustration from track metadata with no text in the image. The prompt explicitly requests clear space in the upper portion for a title.
2. **Add title via edit** — a separate edit call adds the playlist title as a decorative banner. This is a deliberate two-step process because single-pass generation frequently crops title text at image boundaries.

## Upload pipeline

Yoto's transcode API accepts any audio format — including MKA — and transcodes to Opus/OGG for playback. MKA files are uploaded directly without extraction; the transcoder handles the Matroska container and finds the audio stream inside.

The content schema's `format` field must match the **transcoded output** format (`"opus"`), not the input format. Yoto hardware firmware v2.21.4+ supports Opus playback.

## CLI presentation layer

The CLI uses [rich](https://rich.readthedocs.io/) for all terminal output. The shared
`Console(stderr=True)` in `src/yoto_cli/progress.py` is the single output channel —
progress bars, log messages, status updates, and errors all route through it so rich
can coordinate live displays (progress bars) with printed output (messages scroll above
the bar).

Key rich features used:
- **Progress** — animated bars with SpinnerColumn, CostColumn, nested/parallel tasks
- **Panel + Columns** — icon selection display (ANSI pixel art wrapped in panels)
- **Table** — card listing (`yoto list`)
- **Rule** — track separators in multi-track `select-icon`
- **Prompt** — interactive input replacing `click.prompt()`
- **RichHandler** — Python logging integration (stderr, no formatter)
- **Markup** — `[green]✓[/green]` style inline coloring for messages

## Lyrics pipeline

Lyrics are sourced in three stages, tried in order:

1. **Embedded tags** — audio files often carry lyrics in their metadata (ID3 USLT, Vorbis LYRICS tag, etc.). These are read via ffprobe during import and stored as the `LYRICS` Matroska tag.

2. **Web scraping sources** — user-configured sources in `~/.yoto/lyrics/*.json`. Each file defines an index page URL plus two JS snippets: `index_js` (extracts `[{title, url}]` from the index) and `lyrics_js` (extracts lyrics text from a song page). A Node.js script (`scrape_runner.js`) runs these snippets against the page HTML using jsdom. Title matching uses `difflib.SequenceMatcher` with a 0.6 threshold.

3. **LRCLIB API** — public lyrics API at `lrclib.net`. Requires both artist and title.

### Adding a new lyrics source

Run `yoto lyrics --add-source <index-url>`. The wizard:
1. Downloads the index page HTML
2. Calls Claude Sonnet (with `allowed_tools="Read"`) to analyze the HTML and write `index_js`
3. Picks a sample song, downloads its page
4. Calls Claude Sonnet again to write `lyrics_js`
5. Validates both snippets run correctly
6. Shows a preview and saves the config to `~/.yoto/lyrics/<name>.json`

The config format is intentionally simple — just a name, URL, and two JS strings. Configs are human-readable and editable.

### Upgrade path: JS-rendered sites

The current implementation fetches HTML via `httpx` and runs JS snippets against it using jsdom (a pure-JS DOM implementation). This works for plain HTML sites.

For JS-rendered sites (SPAs that load content via JavaScript), swap `scrape_runner.js` to use `puppeteer-core` with `chrome-headless-shell` (~50MB download):

```bash
npm install puppeteer-core
npx puppeteer browsers install chrome-headless-shell
```

Then update `scrape_runner.js` to use `browser.newPage()` + `page.goto()` + `page.evaluate()` instead of jsdom. The config format (`index_js`, `lyrics_js`) stays identical — no config migration needed.

## Cover printing

The `printer` module (`src/yoto_lib/printer.py`) handles printing cover art to a Canon
Selphy CP1300 (or any CUPS-configured photo printer). The pipeline mirrors how Adobe
Lightroom handles color-managed printing:

1. **Validate** — check cover.png exists and has the expected portrait aspect ratio (~0.628)
2. **Crop** — center-crop to exact 54:86mm proportions (typically a few pixels of adjustment)
3. **ICC convert** — Pillow's `ImageCms` applies the Canon Selphy ICC device link profile
   via lcms2. The profile is a pre-baked color transform (device link class), so it's
   applied directly with `buildTransform(profile, profile, "RGB", "RGB")`.
4. **Print** — `lpr` sends the PNG to the printer via CUPS with borderless 54x86mm paper

Configuration via environment variables: `YOTO_PRINTER` (CUPS name) and `YOTO_ICC_PROFILE`
(ICC profile path).

## Release pipeline

Releases are published via a manual `workflow_dispatch` GitHub Actions workflow. The pipeline:

1. **Lint + test** — ruff, ty, pytest across Python 3.10–3.13
2. **Version bump** — commitizen reads commits since the last tag, determines the bump type (patch/minor/major), updates `pyproject.toml` and `CHANGELOG.md`, commits and tags
3. **Build** — builds sdist and wheel, validating the package before pushing
4. **Push** — pushes the bump commit and tag to `main`
5. **Publish** — uploads to TestPyPI, then PyPI via OIDC trusted publishing
6. **Release** — creates a GitHub release with the commitizen changelog for the new version
