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

### CLI output

All terminal output goes through the shared rich `Console` in `src/yoto_cli/progress.py`.
Never use `click.echo()` or `print()` directly.

- **Success**: `success(msg)` — green ✓ prefix
- **Error**: `error(msg)` — red ✗ prefix
- **Warning**: `warning(msg)` — yellow ⚠ prefix
- **Info**: `_console.print(msg)` — no prefix
- **During progress bars**: use `progress.console.print()` (same console, coordinates with Live display)
- **Prompts**: `rich.prompt.Prompt.ask(question, console=_console)`
- **Tables**: `rich.table.Table` printed via `_console.print(table)`
- **Separators**: `rich.rule.Rule(title=name)` printed via `_console.print(rule)`

Progress bars use `make_progress()` from `progress.py`. For commands with multi-step
operations, use nested tasks (outer = command-level, inner = current operation).
For parallel operations, add one inner task per concurrent job.
Cost tracking is automatic — providers record costs via `get_tracker().record()`.

**iTerm2 integration** — `src/yoto_cli/iterm_colors.py` uses iTerm2's Python API (optional dependency) to fix color space rendering for pixel art icons. Pattern: detect iTerm2 via `TERM_PROGRAM` env var, attempt API connection (catch `SystemExit` on failure), apply session-local overrides via `LocalWriteOnlyProfile` (doesn't modify the underlying profile), restore after. Silent graceful degradation — never fails the command.

**HTTP client** — httpx (not requests).

**LLM calls** — go through the `claude` CLI as a subprocess (`claude -p <prompt> --output-format json`). See `icon_llm.py` for the pattern. Do not use the Anthropic Python SDK for LLM calls in this project. The `--add-source` wizard calls Claude with `allowed_tools="Read"` to analyze downloaded HTML files.

**Image generation** — pluggable providers in `image_providers/`. Provider selected by `YOTO_IMAGE_PROVIDER` env var. Follow the existing provider interface when adding new providers.

**Environment** — API keys loaded from `.env` via `python-dotenv`. No other config files.

**Parallelism** — `YOTO_WORKERS` (default 4) controls the max parallel workers used for downloads, uploads, imports, and exports. Read via `int(os.environ.get("YOTO_WORKERS", "4"))` at module level; always cap with `min(WORKERS, len(jobs))` to avoid spawning idle threads.

## Architecture rules

- **Library/CLI separation** — `yoto_lib` must be importable without Click. No CLI framework imports in library code. The library is designed to be called by other tools (e.g., a Quick Look plugin).
- **No global config file** — env vars for provider selection, Keychain for auth. This is a deliberate choice, not an oversight. **Exception: `~/.yoto/lyrics/*.json` are per-source scraping configs (not a central config file — each is self-contained).**
- **Sync is one-directional** — `sync` is always local→remote (local wins). `pull` is always remote→local (remote wins). There is no conflict resolution, and none should be added.
- **One chapter per track** — each track maps to one Yoto chapter, giving the user dial control per song on the physical player. Do not collapse tracks into shared chapters.
- **Cover art dimensions** — 638x1011 pixels (portrait). Print-ready at 300dpi for 54x86mm physical cards.
- **Icons** — 16x16 PNG or animated GIF, stored as Matroska attachments in MKA files. macOS file icons are set via nearest-neighbor upscaled ICNS.

## Code quality standards

**Type annotations** — every function and method must have full type annotations: all parameters and return type. Use specific types (`list[str]`, `dict[str, Any]`, `tuple[bytes | None, str]`) rather than bare `list`, `dict`, `tuple`. Annotate `self` and `cls` only when the return type needs it (e.g., classmethod returning `Self`).

**Union syntax** — use `X | None`, never `Optional[X]`. All source files have `from __future__ import annotations` so the `|` syntax works at any Python version. Do not import `Optional` from typing.

**Exception handling** — never write bare `except:` or `except Exception:`. Catch the narrowest applicable type:
- `except ImportError:` for optional-dependency imports
- `except subprocess.CalledProcessError:` for subprocess calls
- `except (OSError, httpx.HTTPError):` for file I/O and network operations
- `except (KeyError, ValueError, TypeError):` for data parsing/validation
- `except json.JSONDecodeError:` for JSON parsing

If a block genuinely needs to catch everything (e.g., a top-level CLI error boundary), add a comment explaining why.

**Idioms** — prefer generator expressions over materialised lists when the result is consumed once: `any(x for x in items)` not `any([x for x in items])`. Use `Path` objects throughout; never convert to `str` for path operations. Use f-strings, not `%` formatting or `.format()`.

**Imports** — use `TYPE_CHECKING` guards for imports needed only by annotations. Keep runtime imports minimal. Group: stdlib, third-party, local — separated by blank lines.

**No dead code** — do not leave commented-out code, unused imports, or placeholder `pass` statements in non-empty blocks. Delete rather than comment out.

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
| `node` + `jsdom` | Web scraping lyrics sources via `scrape_runner.js` | for lyrics scraping |

These are invoked as subprocesses, not Python bindings.

## Design specs

Architectural rationale and feature designs live in `docs/superpowers/specs/`. Read these before making changes to understand why things are the way they are — the code shows what, the specs explain why.
