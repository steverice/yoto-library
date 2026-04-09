# AGENTS.md

Instructions for AI coding agents working on this codebase.

## Project structure

```
src/
  yoto_lib/            # standalone Python library (no CLI dependencies)
    billing/           # cost tracking, billing persistence, provider balances
    covers/            # cover art generation, iTunes metadata, printing
    icons/             # icon matching, generation, ICNS building
    lyrics/            # lyrics fetch, web scraping, source wizard
    providers/         # AI service providers (OpenAI, FLUX, Gemini, RetroD, Claude)
    track_sources/     # .webloc URL resolution (YouTube via yt-dlp)
    yoto/              # Yoto API client and OAuth authentication
  yoto_cli/            # thin Click CLI wrapper
tests/                 # pytest suite (mirrors src/ subpackage structure)
validation/            # standalone validation scripts
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

**Progress display** — two patterns depending on the operation:

- **Counted work** (N items to process): use `make_progress()` from `progress.py`. Add an outer task for the command (`total=N`) and inner tasks for sub-operations. Advance with `progress.update(task, advance=1, status=current_item)`. Cost tracking is automatic — providers record costs via `get_tracker().record()`. Use `progress.console.print()` for per-item result lines inside the `with make_progress()` block (not `_console.print()` — that would interleave with the live display). Any CLI loop over N items where each item might do I/O (network, subprocess, file read) should use this pattern.

- **Sequential multi-step work** (wizard, analysis pipeline — known step count): use `make_progress()` with `total=N` where N is the number of steps. The `on_step` callback advances the task and sets the status label. Use `_console.status()` only for a single indeterminate wait with no meaningful sub-steps to show.

**Library/CLI boundary for progress** — library functions must not call CLI or Rich directly. Instead, accept an `on_step: Callable[[str], None] | None = None` parameter and call it at each step (`if on_step: on_step("Doing X…")`). The CLI layer wraps the call in a status spinner and passes `lambda msg: spinner.update(msg)`. See `lyrics_source_wizard.py` / `main.py` `--add-source` for the reference implementation. Any library function that takes more than a second or has named phases should expose `on_step`.

**iTerm2 integration** — `src/yoto_cli/iterm_colors.py` uses iTerm2's Python API (optional dependency) to fix color space rendering for pixel art icons. Pattern: detect iTerm2 via `TERM_PROGRAM` env var, attempt API connection (catch `SystemExit` on failure), apply session-local overrides via `LocalWriteOnlyProfile` (doesn't modify the underlying profile), restore after. Silent graceful degradation — never fails the command.

**HTTP client** — httpx (not requests).

**LLM calls** — go through the `claude` CLI via `ClaudeProvider` in `providers/claude_provider.py`. The provider wraps `claude -p <prompt> --output-format json` as a subprocess. See `icon_llm.py` and `description.py` for usage patterns. Do not use the Anthropic Python SDK for LLM calls in this project. The `--add-source` wizard calls Claude with `allowed_tools="Read"` to analyze downloaded HTML files.

**Image generation** — provider classes live in `providers/`. Each AI task uses a hardcoded provider chosen for best results (not user-selectable). All providers extend the `Provider` ABC in `providers/base.py`. Follow the existing provider interface when adding new providers.

**Environment** — API keys loaded from `.env` via `python-dotenv`. No other config files.

**Parallelism** — `YOTO_WORKERS` (default 4) controls the max parallel workers used for downloads, uploads, imports, and exports. Read via `int(os.environ.get("YOTO_WORKERS", "4"))` at module level; always cap with `min(WORKERS, len(jobs))` to avoid spawning idle threads.

## Architecture rules

- **Library/CLI separation** — `yoto_lib` must be importable without Click. No CLI framework imports in library code. The library is designed to be called by other tools (e.g., a Quick Look plugin).
- **No global config file** — env vars for API keys, Keychain for auth. This is a deliberate choice, not an oversight. **Exception: `~/.yoto/lyrics/*.json` are per-source scraping configs (not a central config file — each is self-contained).**
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

## Pre-commit checks

Run `make check` before every commit. This runs linting and unit tests.
If formatting fails, run `make format` to auto-fix, then re-run `make check`.

Do not skip or bypass these checks.

## External tool dependencies

| Tool | Used for | Required |
|------|----------|----------|
| ffmpeg | audio processing, MKA muxing, silence detection | yes |
| mkvtoolnix | MKA metadata tag and attachment read/write | yes |
| yt-dlp | YouTube audio download | for `.webloc` support |
| claude CLI | auto-descriptions, LLM-based icon matching, cover evaluation | for AI features |
| `node` + `jsdom` | Web scraping lyrics sources via `scrape_runner.js` | for lyrics scraping |
| bsdiff | byte-perfect export patches at import time | optional (export won't be byte-perfect without it) |

These are invoked as subprocesses, not Python bindings.

## Design specs

Architectural rationale and feature designs live in `~/Documents/www/agent-specs/` (outside this repo). Read these before making changes to understand why things are the way they are -- the code shows what, the specs explain why.
