"""LLM-based icon matching and comparison via Claude CLI."""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path

from yoto_lib.providers.base import check_status_on_error
from yoto_lib.providers.claude_provider import ClaudeProvider

logger = logging.getLogger(__name__)

# Zone thresholds
CONFIDENCE_HIGH = 0.8
CONFIDENCE_LOW = 0.4

_claude = ClaudeProvider()


@check_status_on_error(ClaudeProvider)
def summarize_lyrics_for_icon(lyrics: str, track_title: str) -> str | None:
    """Summarize lyrics into a short visual description for icon generation.

    Returns 1-2 sentences of concrete visual imagery, or None on failure.
    """
    # Truncate very long lyrics to avoid blowing up the prompt
    truncated = lyrics[:3000] if len(lyrics) > 3000 else lyrics

    prompt = (
        f'Given these lyrics for a children\'s audio track called "{track_title}", '
        f"describe the key visual imagery in 1-2 sentences. "
        f"Focus on concrete objects, animals, settings, and actions that could be "
        f"depicted in a tiny 16x16 pixel art icon. No abstract themes or emotions.\n\n"
        f"Lyrics:\n{truncated}\n\n"
        f"Return ONLY the visual description, no explanation or preamble."
    )

    text = _claude.call(prompt)
    if not text or not text.strip():
        return None
    return text.strip()


@check_status_on_error(ClaudeProvider)
def describe_icons_llm(
    track_title: str,
    album_description: str | None = None,
    lyrics_summary: str | None = None,
) -> list[str]:
    """Generate 3 visual descriptions for a track icon using Claude Haiku.

    The LLM interprets the track title and returns concrete visual concepts
    that an image generation model can render as 16x16 pixel art icons.

    Returns a list of 3 short visual descriptions, or empty list on failure.
    """
    context = ""
    if album_description:
        context = f"\n\nAlbum/show description:\n{album_description}\n"

    if lyrics_summary:
        context += f"\n\nLyrics context: {lyrics_summary}\n"

    prompt = (
        f"I need 3 different visual concepts for a 16x16 pixel art icon "
        f'representing a children\'s audio track called "{track_title}".{context}\n'
        f"Each concept should be a concrete subject — an object, animal, or symbol "
        f"that captures the track's meaning. Think emoji: would you recognize "
        f"this at a glance if it were tiny? The silhouette alone should be readable.\n"
        f"Pairs are fine (fishing rod + fish, baseball + bat) but avoid hands, "
        f"faces, fine detail, or anything needing more than a few bold shapes.\n"
        f"Do NOT describe characters from the show.\n\n"
        f"Make the 3 concepts genuinely different from each other — explore "
        f"literal, metaphorical, and mood-based angles rather than 3 variations "
        f"of the same idea.\n\n"
        f"Return ONLY a JSON array of 3 short image prompts (under 8 words each). "
        f'Example: ["fishing rod and fish", "compass on a map", "sunset over calm water"]\n'
        f"No explanation, no markdown, just the JSON array."
    )

    try:
        logger.debug("describe_icons_llm: title='%s'", track_title)
        text = _claude.call(prompt)
        if text is None:
            return []
        descriptions = json.loads(text)
        if isinstance(descriptions, list) and len(descriptions) >= 3:
            result = [str(d) for d in descriptions[:3]]
            logger.debug("describe_icons_llm: result=%s", result)
            return result  # ty: ignore[invalid-return-type]
        return []
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return []


@check_status_on_error(ClaudeProvider)
def match_icon_llm(
    track_title: str,
    icons: list[dict],
) -> tuple[str | None, float]:
    """Match a track title to the best Yoto icon using Claude Haiku.

    Returns (mediaId, confidence) or (None, 0.0) if no match / failure.
    """
    if not icons or not track_title:
        return None, 0.0

    logger.debug("match_icon_llm: title='%s' %d icons", track_title, len(icons))
    icon_lines = []
    for icon in icons:
        media_id = icon.get("mediaId", "")
        title = icon.get("title", "") or icon.get("name", "")
        if media_id and title:
            icon_lines.append(f'- mediaId: "{media_id}", title: "{title}"')

    if not icon_lines:
        return None, 0.0

    prompt = (
        f'Given the track title "{track_title}", which of these Yoto icons best '
        f"represents it? Return ONLY a JSON object: "
        f'{{"mediaId": "<best_match_id>", "confidence": <0.0-1.0>}}. '
        f'If nothing fits, return {{"mediaId": "none", "confidence": 0.0}}. '
        f"No explanation, no markdown, just JSON.\n\n"
        f"Icons:\n" + "\n".join(icon_lines)
    )

    try:
        text = _claude.call(prompt)
        if text is None:
            return None, 0.0
        data = json.loads(text)
        media_id = data.get("mediaId", "none")
        confidence = float(data.get("confidence", 0.0))
        logger.debug("match_icon_llm: result mediaId=%s confidence=%.2f", media_id, confidence)
        if media_id == "none" or not media_id:
            return None, 0.0
        return media_id, confidence
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return None, 0.0


@check_status_on_error(ClaudeProvider)
def compare_icons_llm(
    track_title: str,
    candidates: list[bytes],
    yoto_icon: bytes | None = None,
    descriptions: list[str] | None = None,
    album_description: str | None = None,
) -> tuple[int, list[float]]:
    """Compare candidate icon images using Claude Sonnet with vision.

    Writes images to temp files and asks Claude CLI to read and evaluate them.

    Args:
        track_title: The track title the icon should represent.
        candidates: List of PNG bytes (AI-generated icons).
        yoto_icon: Optional PNG bytes for the Yoto catalog icon (appended last).
        descriptions: Visual descriptions used to generate each candidate.
        album_description: Album/show description for context.

    Returns:
        (winner, scores) where winner is 1-indexed. On failure, returns (1, []).
    """
    logger.debug(
        "compare_icons_llm: title='%s' %d candidates (yoto=%s)", track_title, len(candidates), yoto_icon is not None
    )
    all_images = list(candidates)
    if yoto_icon is not None:
        all_images.append(yoto_icon)

    if not all_images:
        return 1, []

    with tempfile.TemporaryDirectory(prefix="yoto-compare-") as tmpdir:
        tmp = Path(tmpdir)
        paths = []
        for i, img_bytes in enumerate(all_images):
            p = tmp / f"option_{i + 1}.png"
            p.write_bytes(img_bytes)
            paths.append(p)

        file_list = []
        for i, p in enumerate(paths, 1):
            label = f"Option {i}"
            if descriptions and i <= len(descriptions):
                label += f' (prompted as: "{descriptions[i - 1]}")'
            if yoto_icon is not None and i == len(paths):
                label += " (Yoto library icon)"
            file_list.append(f"{label}: {p}")

        context = ""
        if album_description:
            context = f"\nAlbum/show context: {album_description}\n"

        prompt = (
            f"You are evaluating 16x16 pixel art icons for a children's audio "
            f'track called "{track_title}".{context}\n'
            f"Read each icon image and evaluate it on these criteria:\n"
            f"1. Does it clearly depict the intended subject at tiny 16x16 size?\n"
            f"2. Is it recognizable at a glance — bold shapes, not muddy detail?\n"
            f"3. Does it capture the meaning or emotion of the track title?\n\n"
            f"Think through each option briefly, then return a JSON object:\n"
            f'{{"winner": <1-indexed>, "scores": [<score_per_option>]}}\n'
            f"Scores should be 0.0-1.0. End your response with the JSON.\n\n" + "\n".join(file_list)
        )

        try:
            text = _claude.call(prompt, allowed_tools="Read", timeout=120, model="sonnet")
            if text is None:
                return 1, []
            data = json.loads(text)
            winner = int(data["winner"])
            scores = [float(s) for s in data["scores"]]
            logger.debug("compare_icons_llm: winner=%d scores=%s", winner, scores)
            return winner, scores
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            return 1, []


FEEDBACK_PATH = Path.home() / ".yoto" / "icon-feedback.jsonl"


def log_icon_feedback(
    track_title: str,
    llm_winner: int,
    llm_scores: list[float],
    user_choice: int,
    descriptions: list[str] | None = None,
    album: str | None = None,
    chose_yoto: bool = False,
) -> None:
    """Log LLM vs user icon choice for tuning analysis."""
    from datetime import datetime, timezone

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "track_title": track_title,
        "album": album,
        "descriptions": descriptions,
        "llm_winner": llm_winner,
        "llm_scores": llm_scores,
        "user_choice": user_choice,
        "agreed": llm_winner == user_choice,
        "chose_yoto": chose_yoto,
    }

    try:
        FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(FEEDBACK_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass
