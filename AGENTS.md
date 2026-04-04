# AGENTS.md

Instructions for AI coding agents working on this codebase.

## Project structure

```
src/
  yoto_lib/            # standalone Python library (no CLI dependencies)
  yoto_cli/            # thin Click CLI wrapper
tests/                 # pytest test suite
docs/superpowers/      # design specs and implementation plans
  specs/               # rationale behind architectural decisions
  plans/               # step-by-step implementation plans
validation/            # standalone validation scripts (MKA transcode, icon grid)
data/                  # sample playlists (gitignored)
```

## Key conventions

**MKA metadata** — read/write via `ffmpeg` and `mkvtoolnix` subprocess calls. Never use Mutagen or other Python audio tag libraries. See `mka.py` for the pattern.

**playlist.jsonl** — bare JSON strings, one per line. Forward-compatible with objects (a future version may mix strings and objects for per-track overrides). Do not change this format.

**Authentication** — macOS Keychain via the `keyring` package. No config files for auth. Token refresh is automatic.

**CLI framework** — Click. All commands live in `src/yoto_cli/main.py`.

**HTTP client** — httpx (not requests).

**LLM calls** — go through the `claude` CLI as a subprocess (`claude -p <prompt> --output-format json`). See `icon_llm.py` for the pattern. Do not use the Anthropic Python SDK for LLM calls in this project.

**Image generation** — pluggable providers in `image_providers/`. Provider selected by `YOTO_IMAGE_PROVIDER` env var. Follow the existing provider interface when adding new providers.

**Environment** — API keys loaded from `.env` via `python-dotenv`. No other config files.

## Architecture rules

- **Library/CLI separation** — `yoto_lib` must be importable without Click. No CLI framework imports in library code. The library is designed to be called by other tools (e.g., a Quick Look plugin).
- **No global config file** — env vars for provider selection, Keychain for auth. This is a deliberate choice, not an oversight.
- **Sync is one-directional** — `sync` is always local→remote (local wins). `pull` is always remote→local (remote wins). There is no conflict resolution, and none should be added.
- **One chapter per track** — each track maps to one Yoto chapter, giving the user dial control per song on the physical player. Do not collapse tracks into shared chapters.
- **Cover art dimensions** — 638x1011 pixels (portrait). Print-ready at 300dpi for 54x86mm physical cards.
- **Icons** — 16x16 PNG or animated GIF, stored as Matroska attachments in MKA files. macOS file icons are set via nearest-neighbor upscaled ICNS.

## Testing

```
python -m pytest                    # all tests
python -m pytest -m integration     # network-dependent tests (require yt-dlp)
python -m pytest tests/test_foo.py  # single module
```

Tests mock external calls (Yoto API, subprocess, Claude CLI). See `conftest.py` for shared fixtures. Tests use pytest's built-in `tmp_path` for temporary filesystem state.

## External tool dependencies

| Tool | Used for | Required |
|------|----------|----------|
| ffmpeg | audio processing, MKA muxing, silence detection | yes |
| mkvtoolnix | MKA metadata tag and attachment read/write | yes |
| yt-dlp | YouTube audio download | for `.webloc` support |
| claude CLI | auto-descriptions, LLM-based icon matching | for AI features |

These are invoked as subprocesses, not Python bindings.

## Design specs

Architectural rationale and feature designs live in `docs/superpowers/specs/`. Read these before making changes to understand why things are the way they are — the code shows what, the specs explain why.
