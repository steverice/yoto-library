## v0.2.0 (2026-04-12)

### ✨ Features

- add `yoto lyrics --clear` to remove stored lyrics from tracks

### 🐛🚑️ Fixes

- re-upload cover when `.yoto-cover-hash` is missing
- resolve `.webloc` files during `yoto import` before scanning for audio
- convert palette-mode images to RGB before ICC transform in `_icc_convert`
- pre-fetch HTML in Python to bypass TLS-fingerprint-based 403 blocks

### 💚👷 CI & Build

- remove automatic PyPI workflow trigger from release workflow

### 📝💡 Documentation

- update installation for PyPI and document two-step release process

## v0.1.3 (2026-04-10)

### 🐛🚑️ Fixes

- trigger PyPI workflow via workflow_dispatch from release workflow

## v0.1.2 (2026-04-10)

### 🐛🚑️ Fixes

- upgrade GitHub Actions to Node.js 24 compatible versions

### 💚👷 CI & Build

- make release and publish workflows idempotent
- split release and PyPI publish into separate workflows

## v0.1.1 (2026-04-10)

### 🐛🚑️ Fixes

- resolve ty lint failures in CI due to environment-dependent import resolution

### 💚👷 CI & Build

- rework publish workflow with commitizen version bumping
- add commit message validation for pull requests

### 📝💡 Documentation

- add CI status and PyPI badges to README
- add conventional commits and gitmoji badges to README
- fix emoji table in CONTRIBUTING.md to match cz-conventional-gitmoji
- add CONTRIBUTING.md and update docs for commitizen workflow

### 🧹 chore

- add PyPI metadata (readme, license, authors, classifiers, urls)
- add pre-commit hooks for ruff, ty, and commitizen

## v0.1.0 (2026-04-09)

### ✨ Features

- clean up video titles with Claude Haiku during download
- add Anthropic SDK support to ClaudeProvider
- add Together AI balance to providers command
- rename 'yoto billing' to 'yoto providers', add status section
- lyrics command improvements and progress bars
- strengthen icon fill instructions in RetroD prompts
- add --add-source wizard to lyrics command
- add Claude-powered lyrics source wizard
- insert scrape sources step into get_lyrics pipeline
- add lyrics_scrape module with config-driven jsdom scraping provider
- add scrape_runner.js Node.js jsdom runner for lyrics scraping
- fixed-width centered icon columns in select-icon display
- add @check_status_on_error decorator to logic functions
- add ClaudeProvider, consolidate _call_claude into single class
- extend all providers from Provider ABC with check_status
- add Provider ABC, StatusPageMixin, and @check_status_on_error decorator
- wire lyrics summary into select-icon prompt generation
- add lyrics summary generation and wire into icon description prompt
- add 'yoto lyrics' command for backfilling lyrics on existing tracks
- fetch lyrics during import (source tags → LRCLIB)
- add lyrics fetch module (source tags + LRCLIB fallback)
- add lyrics and lyrics_summary to MKA tag mappings
- use Rich tables for billing display with aligned columns
- add print job status polling with Rich spinner
- rework ICC profile handling — add --profile flag, support both profile types
- add --print/--no-print to sync and folder to SyncResult
- add yoto print command
- add ICC conversion and print pipeline to printer module
- add printer module with cover validation and crop
- thread --ignore-album-art flag through sync pipeline
- upgrade from gpt-image-1 to gpt-image-1.5 (better quality, lower cost)
- use low quality for cover generation in CLI command
- use low quality for cover illustration generation (37% cost reduction)
- split OpenAI cost keys by quality level, fix pricing
- add quality parameter to OpenAIProvider generate() and edit()
- auto-install iterm2 package on first use in iTerm2
- fix iTerm2 icon banding via Python API sRGB color space override
- arrow key icon selection via ANSI erase+reprint (no Live)
- persist session costs to billing.json automatically
- add yoto billing command
- add balance and subscription usage queries
- add billing persistence layer
- add records() accessor to CostTracker
- arrow key navigation for icon selection
- integrate rich UI adoption + parallelization (specs 1 & 2)
- **phases4-6**: parallel import/export + wired progress bars in CLI
- **phase3**: parallel track uploads in sync with start/done callbacks
- **phase2**: streaming pull downloads with byte-level progress callbacks
- **phase1**: parallel yt-dlp downloads with real-time progress callbacks
- add nested progress bars and documentation updates
- replace all click.echo/prompt with rich output helpers
- enrich tracks with iTunes album art on re-import
- replace cover spinner with progress bar showing generation steps
- add real-time cost tracking to progress bars and command summaries
- add title via OpenAI edit pass instead of in-prompt text
- add --backup option to yoto cover
- remove padded fallback — always use AI recomposition
- check for stretched/distorted elements in recomposed covers
- Claude describes text style for Gemini rendering
- save repaired cover to debug dir
- configurable FLUX retry attempts via YOTO_RECOMPOSE_ATTEMPTS
- save FLUX recomposition attempts to temp dir for debugging
- retry FLUX recomposition up to 3 times before text repair
- two-stage text repair pipeline for recomposed covers
- add --style flag and fix FLUX image upload
- add FLUX provider via Together AI for image recomposition
- replace outpainting with multimodal recomposition
- replace tqdm with rich progress bars; add bars to pull/download/import/export/cover
- use Vertex AI mask-based inpainting for Gemini outpainting
- **cover**: wire reframe_album_art into try_shared_album_art
- **cover**: add reframe_album_art orchestrator
- **cover**: add Claude vision comparison for cover candidates
- **gemini**: add image edit() method for outpainting
- **openai**: add image edit() method for outpainting
- **cover**: add pad_to_cover for album art reframing
- wire iTunes artwork lookup into import pipeline with e2e test
- **itunes**: add enrich_from_itunes orchestrator with caching
- **itunes**: add album art embedding via ffmpeg re-mux
- **itunes**: add iTunes Search API client, album matching, and URL rewriting
- reuse shared album art as playlist cover
- extract MKA to native format for upload, add `yoto export` for byte-perfect round-trips
- `select-icon` accepts multiple tracks (e.g. `yoto select-icon *.mka`)
- add debug logging throughout for post-mortem troubleshooting
- add `yoto cover` command for standalone cover art generation
- improve icon scoring with Sonnet, richer prompt, and feedback logging
- LLM-driven icon generation with album context
- add context-aware shell completions for CLI commands
- add tqdm progress bar to select-icon
- use Claude CLI for LLM calls, side-by-side icons with scores
- lexical exact match shortcut skips LLM call
- select-icon shows best Yoto match as 4th option
- resolve_icons uses three-zone LLM confidence system
- add generate_retrodiffusion_batch for 3-at-once icon generation
- add LLM icon matching and image comparison
- add local icon catalog cache with 24h TTL
- add local icon catalog cache with 24h TTL
- add generate_batch to RetroDiffusionProvider
- improve select-icon with inline ANSI preview and regenerate option
- auto-generate description during import and sync
- add description.py for auto-generating playlist descriptions
- copy source metadata to MKA during import
- add read_source_tags() for reading metadata from any audio format
- expand TAG_MAP with genre, composer, album, date, track, disc
- add yoto download command for standalone webloc resolution
- integrate webloc resolution into sync with --no-trim flag
- **sources**: add resolve_weblocs dispatcher with provider registry
- **sources**: add silence-based audio trimming
- replace preview-icon with select-icon command
- **sources**: add YouTube provider with yt-dlp download
- **sources**: add webloc plist parsing
- black bg prompt + flood-fill background removal for RD icons
- add multiple icon generation strategies with Retro Diffusion as default
- add preview-icon command and save raw AI grid images
- add reset-icon command to clear track icons before re-sync
- refactor icon pipeline to write back to MKA, add AI icon generation
- add rich progress bars to sync and pull commands
- load .env from parent dirs, default reorder to cwd
- import strips track numbers and deletes source files
- parallel pull with icons, fix ICNS, migrate to google-genai
- add yoto pull --all and fix pull response parsing
- **tests**: add integration smoke tests for full CLI workflow
- **cli**: wire all commands to library modules and add CLI tests
- icon pipeline and ICNS generation
- cover art pipeline with resize, prompt builder, and generation
- image provider interface with OpenAI and Gemini implementations
- pull engine for remote-to-local playlist download
- add sync engine orchestrating local-to-remote playlist uploads
- playlist model bridging filesystem and Yoto API content schema
- MKA container handling with tags and attachments
- add validation scripts for MKA transcode and AI icon grid
- Yoto API client with content CRUD, upload pipeline, and media endpoints
- auth module with device code flow and Keychain storage
- project scaffolding with CLI skeleton and dependencies

### 🐛🚑️ Fixes

- properly mock env-dependent tests for CI
- resolve CI check failures
- build integration image inline instead of using ghcr.io
- resolve ty type checking errors across codebase
- rename FLUX (Together) display name to Together AI
- guard against empty response.data in OpenAI and FLUX providers
- use isolated_filesystem() in test_pull_all to prevent directory leak
- update stale test assertions
- detect local cover changes via hash, add --force-cover to sync
- warn user when icon evaluation times out instead of showing silent "score: ?"
- lyrics --force now re-fetches from LRCLIB and clears stale summary
- replace model names with descriptive labels in progress bars
- switch ICC conversion from sips to Pillow ImageCms
- correct progress bar step count for --ignore-album-art
- pass quality to images.edit() API call and add test coverage
- suppress iTerm2 'problem connecting' stderr message
- use full block █ for same-color pixels to avoid anti-aliasing seam
- mock add_title_to_illustration in cover generation test
- use is_subscription() instead of hardcoded prefix, remove redundant imports
- drop arrow key nav, use rich Table for icon display with Prompt.ask
- revert to ANSI rendering for icon display, keep arrow key navigation
- use rich Table instead of Panel+Columns for icon display
- use rich Text+Style for pixel art instead of raw ANSI
- constrain icon panel width and disable column expansion
- complete select-icon progress bar before showing icon panels
- color entire message for success and error helpers, not just icon
- make entire warning message yellow, not just the icon
- add status field to all inner progress tasks to prevent KeyError
- yt-dlp parsing, YOTO_WORKERS in pull, docs, eager callback, cache lock
- add nested progress bars for cover/select-icon, remove dead code
- route all cover/progress output through rich console
- surface reframe warnings to user in yellow text
- update test to match title inpainting behavior
- show three decimal places in progress bar cost display
- re-apply rich progress bars after cover command rewrites
- revert to manual mutual exclusion check
- error on --force --backup together
- stronger margin guidance — no text in top/bottom 10%
- prompt FLUX to keep text away from top/bottom edges
- center-crop FLUX output instead of stretching
- resize FLUX output directly instead of padding
- use Sonnet for cover comparison, fix stale target_h reference
- scale text layer to fit within Claude's placement box
- log Gemini finish_reason and prompt_feedback on text render failure
- handle empty Gemini response in text layer rendering
- use standard FLUX outpainting approach — black padding + "outpaint" prompt
- prompt says "any solid-colored borders" not "top and bottom"
- update prompt to reference solid-colored bars, not black
- pad FLUX canvas with edge color instead of black
- tell FLUX to extend scene into black areas, not solid fill
- use Sonnet instead of Haiku for text quality checks
- use exact cover dimensions for FLUX canvas, no rounding
- use data URIs instead of tmpfiles.org for FLUX image input
- round FLUX recompose canvas to multiples of 16
- hardcode FLUX for recomposition, independent of YOTO_IMAGE_PROVIDER
- generic recompose prompt that works for any album art
- tell FLUX to leave blank space where text was
- simplify recompose prompt to avoid FLUX rendering it as text
- tell recompose prompt to remove text and logos
- recompose prompt asks for actual rearrangement, not outpainting
- use kontext fill approach for FLUX recomposition
- use pad_to_cover for recomposed images instead of stretch
- use gemini-2.5-flash-image model for recomposition
- use center-crop instead of stretch for recomposed covers
- allow -v/--verbose anywhere in the command line
- prevent description LLM from refusing non-children's content
- strengthen outpaint prompt to prevent stretching/distortion
- wrap image bytes in BytesIO for OpenAI edit API
- simplify icon description prompts for 16x16 legibility
- escape quotes in file paths for osascript icon setter
- update Gemini generate test to match generate_images API
- use word-boundary regex for A/B parsing in compare_covers
- convert to RGB before edge sampling in pad_to_cover
- cache artwork bytes per-album and clean up embed_album_art
- import autocomplete suggests unimported directories first
- prevent cover art title text from being cropped
- preserve existing cover URL when re-syncing without cover changes
- report transcoded format (opus) instead of input format (aac) in content schema
- match content schema to working MYO card format
- autocomplete falls back to all .mka files when filter matches none
- use plain completion type so zsh uses filtered results
- match_public_icon reads 'title' field, falls back to 'name'
- reset-icon now clears Finder icon and handles apostrophes in paths
- add Callable import under TYPE_CHECKING
- move json import to top-level, fix callable type annotation
- clarify comment on audio cleanup in finally block
- add _trim_silence stub to prevent NameError before Task 3
- cache AI-generated icons to disk after upload
- skip progress bar when not in interactive terminal
- sync schema to match Yoto API expected structure
- content schema (array chapters, key/type fields), text progress, unwrap POST response
- status diff now correctly matches local files to remote tracks
- unwrap API response container keys at the source
- MKA tag/attachment bugs, list track counts, status guard

### ♻️ Refactorings

- globally ignore S101, inline noqa for S311
- use inline noqa for S101 and S311 instead of per-file ignores
- globally ignore S101, S603, S607 in ruff config
- move is_subscription from costs.py to Provider.is_subscription
- move provider registry to providers/__init__.py
- add display_name to Provider base class
- rename FluxProvider to TogetherAIProvider, add BetterStackMixin
- make check_status() non-abstract with None default
- add ImageProvider ABC for image generation providers
- split CLI commands from main.py into commands/ subpackage
- extract select-icon business logic into yoto_lib/icons/select.py
- break icons/__init__.py circular dependency into focused submodules
- consolidate WORKERS constant into yoto_lib.config
- consolidate duplicate filename sanitization into mka.sanitize_filename
- reorganize tests/ to mirror src/ subpackage structure
- reorganize yoto_lib into functional subpackages
- rename sources/ to track_sources/
- extract GeminiProvider from cover.py inline code
- rename image_providers → providers
- clean up icon generation, remove dead textmodel code
- hardcode one provider per task, remove configurable switching
- add type annotations, narrow exceptions, fix idioms across codebase
- remove unnecessary MKA extraction before upload

### ✅🤡🧪 Tests

- add 117 tests covering gaps in unit test coverage
- add tests for provider status checks and ClaudeProvider
- add integration tests for billing command
- recompose prompt removes text for two-stage pipeline
- add ~75 tests for CLI commands, sync, pull, and image providers
- add end-to-end test for album art reframing
- add integration tests for YouTube download pipeline

### 🎨🏗️ Style & Architecture

- fix remaining ruff violations and add noqa suppressions
- auto-fix ruff lint and format violations
- consolidate compare_covers import to top of test file

### 📝💡 Documentation

- standardize README for open source, add MIT license
- add pre-commit checks section to AGENTS.md
- update file trees in AGENTS.md and README.md for subpackage layout
- sync all documentation with current codebase
- document lyrics scraping, Node.js dep, and upgrade path
- add provider architecture section to ARCHITECTURE.md
- add yoto print to README and ARCHITECTURE
- add OpenAI cost reduction spec, document two-step cover pipeline rationale
- all cover pipeline Claude calls use Sonnet, not Haiku
- update try_shared_album_art docstring to reflect reframing
- add README.md and AGENTS.md
