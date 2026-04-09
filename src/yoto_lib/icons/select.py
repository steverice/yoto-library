"""Interactive icon selection workflow for Yoto tracks.

Orchestrates: lyrics summarization, icon description, parallel generation,
Yoto catalog matching, LLM comparison, and user selection via callbacks.
"""

from __future__ import annotations

import io
import logging
import tempfile
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image

from yoto_lib.mka import read_tags, write_tags

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable

    from yoto_lib.yoto.api import YotoAPI


# ── Data containers ──────────────────────────────────────────────────────────


class IconCandidate:
    """One icon option presented to the user."""

    __slots__ = ("image", "is_existing", "is_yoto", "label", "score", "yoto_media_id")

    def __init__(
        self,
        image: Image.Image,
        label: str,
        score: float | None = None,
        is_yoto: bool = False,
        is_existing: bool = False,
        yoto_media_id: str | None = None,
    ):
        self.image = image
        self.label = label
        self.score = score
        self.is_yoto = is_yoto
        self.is_existing = is_existing
        self.yoto_media_id = yoto_media_id


class IconSelectionRound:
    """The result of one generation round, ready for user selection."""

    __slots__ = ("batch", "candidates", "descriptions", "scores", "winner", "yoto_bytes")

    def __init__(
        self,
        candidates: list[IconCandidate],
        winner: int,
        scores: list[float],
        descriptions: list[str],
        batch: list[tuple[bytes, Image.Image]],
        yoto_bytes: bytes | None = None,
    ):
        self.candidates = candidates
        self.winner = winner
        self.scores = scores
        self.descriptions = descriptions
        self.batch = batch
        self.yoto_bytes = yoto_bytes


# ── Callbacks ────────────────────────────────────────────────────────────────

# on_step(status: str) — update the main progress status text
# on_inner(label: str | None, key: str) — create/remove a sub-task
#   label=None means remove; label=str means create
# on_generation_progress(done_count: int) — called after each icon completes
# on_icon_gen_start(index: int, description: str) — sub-task for one icon
# on_icon_gen_done(index: int) — sub-task completed
# on_warn(message: str) — display a warning
# on_error(message: str) — display an error
# choose_icon(round: IconSelectionRound) -> int | "r"
#   Return 1-based index of user choice, or "r" for regenerate


def _ensure_lyrics_summary(
    track_path: Path,
    title: str,
) -> str | None:
    """Read cached lyrics summary or generate one from raw lyrics.

    If a summary is generated, it is persisted as a tag on the MKA.
    Returns the summary string, or None if no lyrics are available.
    """
    from yoto_lib.icons.icon_llm import summarize_lyrics_for_icon

    tags = read_tags(track_path)
    summary = tags.get("lyrics_summary")
    if summary:
        return summary

    raw_lyrics = tags.get("lyrics")
    if not raw_lyrics:
        return None

    summary = summarize_lyrics_for_icon(raw_lyrics, title)
    if summary:
        write_tags(track_path, {"lyrics_summary": summary})
    return summary


def _read_album_description(track_path: Path) -> str | None:
    """Read description.txt from the track's parent folder."""
    desc_path = track_path.resolve().parent / "description.txt"
    if desc_path.exists():
        return desc_path.read_text(encoding="utf-8")
    return None


def _get_existing_icon(track_path: Path) -> Image.Image | None:
    """Read existing icon attachment from an MKA, if any."""
    from yoto_lib.mka import get_attachment

    try:
        existing_bytes = get_attachment(track_path, "icon")
        if existing_bytes:
            return Image.open(io.BytesIO(existing_bytes)).convert("RGBA").resize((16, 16), Image.NEAREST)  # ty: ignore[unresolved-attribute]
    except Exception:  # noqa: S110
        pass
    return None


def _generate_round(
    title: str,
    album_desc: str | None,
    lyrics_summary: str | None,
    catalog: list[dict],
    existing_img: Image.Image | None,
    on_step: Callable[[str], None] | None = None,
    on_inner: Callable[[str | None, str], None] | None = None,
    on_generation_progress: Callable[[int], None] | None = None,
    on_icon_gen_start: Callable[[int, str], None] | None = None,
    on_icon_gen_done: Callable[[int], None] | None = None,
) -> IconSelectionRound | None:
    """Run one round of icon generation and evaluation.

    Returns an IconSelectionRound ready for user presentation, or None if
    generation failed entirely.
    """
    from yoto_lib.icons import download_icon, generate_retrodiffusion_icons
    from yoto_lib.icons.icon_llm import (
        compare_icons_llm,
        describe_icons_llm,
        match_icon_llm,
    )

    _step = on_step or (lambda s: None)
    _inner = on_inner or (lambda label, key: None)

    # Start Yoto catalog matching in parallel with description + generation
    yoto_executor = ThreadPoolExecutor(max_workers=1)

    def _match_yoto() -> tuple:
        _inner("Matching catalog", "catalog")
        mid, conf = match_icon_llm(title, catalog)
        _inner(None, "catalog")
        return mid, conf

    yoto_future: Future = yoto_executor.submit(_match_yoto)

    # Describe icons
    _step("describing icons")
    _inner("Describing icons", "describe")
    descriptions = describe_icons_llm(title, album_description=album_desc, lyrics_summary=lyrics_summary)
    _inner(None, "describe")
    if not descriptions:
        descriptions = [title, title, title]

    # Generate icons
    _step("generating icon 1/3")
    batch = generate_retrodiffusion_icons(
        descriptions,
        on_progress=on_generation_progress,
        on_icon_start=on_icon_gen_start,
        on_icon_done=on_icon_gen_done,
    )

    # Collect Yoto result
    yoto_media_id, _yoto_confidence = yoto_future.result()
    yoto_executor.shutdown(wait=False)

    yoto_img: Image.Image | None = None
    yoto_title: str | None = None
    yoto_bytes: bytes | None = None

    if yoto_media_id:
        yoto_bytes = download_icon(yoto_media_id)
        if yoto_bytes:
            yoto_img = Image.open(io.BytesIO(yoto_bytes)).convert("RGBA").resize((16, 16), Image.NEAREST)  # ty: ignore[unresolved-attribute]
            for icon in catalog:
                if icon.get("mediaId") == yoto_media_id:
                    yoto_title = icon.get("title", "") or icon.get("name", "")
                    break

    if not batch:
        return None

    # Evaluate icons via LLM
    raw_bytes_list = [rb for rb, _ in batch]
    _inner("Evaluating icons", "evaluate")
    winner, scores = compare_icons_llm(
        title,
        raw_bytes_list,
        yoto_icon=yoto_bytes if yoto_img is not None else None,
        descriptions=descriptions,
        album_description=album_desc,
    )
    _inner(None, "evaluate")

    # Build candidate list
    icons_16 = [processed for _, processed in batch]
    candidates: list[IconCandidate] = []

    for i, img in enumerate(icons_16):
        score = scores[i] if i < len(scores) else None
        label = descriptions[i] if i < len(descriptions) else "AI"
        candidates.append(IconCandidate(image=img, label=label, score=score))

    if yoto_img is not None:
        yoto_score = scores[len(icons_16)] if len(icons_16) < len(scores) else None
        candidates.append(
            IconCandidate(
                image=yoto_img,
                label=f'"{yoto_title}"',
                score=yoto_score,
                is_yoto=True,
                yoto_media_id=yoto_media_id,
            )
        )

    if existing_img is not None:
        candidates.append(
            IconCandidate(
                image=existing_img,
                label="current",
                is_existing=True,
            )
        )

    return IconSelectionRound(
        candidates=candidates,
        winner=winner,
        scores=scores,
        descriptions=descriptions,
        batch=batch,
        yoto_bytes=yoto_bytes,
    )


def _apply_chosen_icon(
    track_path: Path,
    chosen_img: Image.Image,
) -> None:
    """Save the chosen icon as an MKA attachment and set the macOS Finder icon."""
    from yoto_lib.icons import set_macos_file_icon
    from yoto_lib.mka import set_attachment

    buf = io.BytesIO()
    chosen_img.save(buf, format="PNG")
    icon_bytes = buf.getvalue()

    tmpdir = Path(tempfile.mkdtemp(prefix="yoto-icon-"))
    icon_tmp = tmpdir / "chosen_icon.png"
    try:
        icon_tmp.write_bytes(icon_bytes)
        set_attachment(track_path, icon_tmp, name="icon", mime_type="image/png")
    finally:
        icon_tmp.unlink(missing_ok=True)
        tmpdir.rmdir()

    set_macos_file_icon(track_path, chosen_img)


def select_icons_for_tracks(
    tracks: list[Path],
    api: YotoAPI,
    *,
    on_step: Callable[[str], None] | None = None,
    on_inner: Callable[[str | None, str], None] | None = None,
    on_generation_progress: Callable[[int], None] | None = None,
    on_icon_gen_start: Callable[[int, str], None] | None = None,
    on_icon_gen_done: Callable[[int], None] | None = None,
    on_warn: Callable[[str], None] | None = None,
    on_error: Callable[[str], None] | None = None,
    choose_icon: Callable[[IconSelectionRound], str] | None = None,
    on_track_start: Callable[[int, int, Path], None] | None = None,
    on_round_ready: Callable[[], None] | None = None,
    on_round_cleanup: Callable[[], None] | None = None,
    on_scores_missing: Callable[[], None] | None = None,
    on_applied: Callable[[Path], None] | None = None,
    on_skipped: Callable[[Path], None] | None = None,
) -> None:
    """Run the interactive icon selection workflow for one or more tracks.

    This is the core library function for the select-icon command. All user
    interaction is mediated through callbacks so the CLI can wire up its own
    display layer (Rich progress bars, arrow key selection, iTerm2 color
    management, etc.).

    Args:
        tracks: List of MKA file paths to process.
        api: Authenticated YotoAPI instance.
        on_step: Called with a status string when the workflow advances.
        on_inner: Called with (label, key) to create a sub-task, or
            (None, key) to remove it.
        on_generation_progress: Called with the count of completed icons.
        on_icon_gen_start: Called with (index, description) when an icon
            generation starts.
        on_icon_gen_done: Called with index when an icon generation completes.
        on_warn: Called with a warning message string.
        on_error: Called with an error message string.
        choose_icon: Called with an IconSelectionRound. Must return "r" to
            regenerate, or a 1-based string index of the chosen candidate.
        on_track_start: Called with (track_index, total_tracks, track_path)
            before each track.
        on_round_ready: Called after generation completes but before user
            selection begins (e.g. to close a progress bar).
        on_round_cleanup: Called after user selection ends (e.g. to restore
            terminal state).
        on_scores_missing: Called when LLM evaluation timed out.
        on_applied: Called with the track path after an icon is attached.
        on_skipped: Called with the track path when an icon is skipped.
    """
    from yoto_lib.icons.icon_catalog import get_catalog
    from yoto_lib.icons.icon_llm import log_icon_feedback

    _warn = on_warn or (lambda m: None)
    _error_fn = on_error or (lambda m: None)
    _choose = choose_icon or (lambda r: str(r.winner))

    catalog = get_catalog(api)

    for i, track_path in enumerate(tracks):
        title = track_path.stem
        album_desc = _read_album_description(track_path)
        lyrics_summary = _ensure_lyrics_summary(track_path, title)
        existing_img = _get_existing_icon(track_path)

        if on_track_start:
            on_track_start(i, len(tracks), track_path)

        round_result = _generate_round(
            title=title,
            album_desc=album_desc,
            lyrics_summary=lyrics_summary,
            catalog=catalog,
            existing_img=existing_img,
            on_step=on_step,
            on_inner=on_inner,
            on_generation_progress=on_generation_progress,
            on_icon_gen_start=on_icon_gen_start,
            on_icon_gen_done=on_icon_gen_done,
        )

        if round_result is None:
            _error_fn(f"Icon generation failed for {track_path.name}")
            if on_skipped:
                on_skipped(track_path)
            continue

        if not round_result.scores and on_scores_missing:
            on_scores_missing()

        if on_round_ready:
            on_round_ready()

        # Selection loop: user picks or regenerates
        skipped = False
        while True:
            raw = _choose(round_result)

            if raw == "r":
                # Regenerate
                round_result = _generate_round(
                    title=title,
                    album_desc=album_desc,
                    lyrics_summary=lyrics_summary,
                    catalog=catalog,
                    existing_img=existing_img,
                    on_step=on_step,
                    on_inner=on_inner,
                    on_generation_progress=on_generation_progress,
                    on_icon_gen_start=on_icon_gen_start,
                    on_icon_gen_done=on_icon_gen_done,
                )
                if round_result is None:
                    _error_fn(f"Icon generation failed for {track_path.name}")
                    skipped = True
                    break
                if not round_result.scores and on_scores_missing:
                    on_scores_missing()
                continue

            try:
                choice = int(raw)
                if not 1 <= choice <= len(round_result.candidates):
                    _warn("Invalid choice.")
                    continue
            except ValueError:
                _warn("Invalid choice.")
                continue

            chosen_candidate = round_result.candidates[choice - 1]

            if chosen_candidate.is_existing:
                skipped = True
                break

            # Log feedback for tuning
            log_icon_feedback(
                track_title=title,
                llm_winner=round_result.winner,
                llm_scores=round_result.scores,
                user_choice=choice,
                descriptions=round_result.descriptions,
                album=track_path.resolve().parent.name,
                chose_yoto=chosen_candidate.is_yoto,
            )

            # Apply the icon
            _apply_chosen_icon(track_path, chosen_candidate.image)
            if on_applied:
                on_applied(track_path)
            break

        if on_round_cleanup:
            on_round_cleanup()

        if skipped and on_skipped:
            on_skipped(track_path)
