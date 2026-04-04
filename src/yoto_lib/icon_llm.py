"""LLM-based icon matching and comparison via Claude CLI."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

# Zone thresholds
CONFIDENCE_HIGH = 0.8
CONFIDENCE_LOW = 0.4


def _extract_json(text: str) -> str:
    """Strip markdown code fences from Claude output to get raw JSON."""
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text.strip()


def _call_claude(prompt: str, *, allowed_tools: str = "", timeout: int = 120) -> str | None:
    """Call Claude CLI and return the response text, or None on failure."""
    cmd = [
        "claude", "-p", prompt,
        "--output-format", "json",
        "--model", "haiku",
    ]
    if allowed_tools:
        cmd += ["--allowedTools", allowed_tools]
    else:
        cmd += ["--tools", ""]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            return None
        wrapper = json.loads(result.stdout)
        if wrapper.get("is_error"):
            return None
        text = wrapper.get("result", result.stdout).strip()
        return _extract_json(text)
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def describe_icons_llm(
    track_title: str,
    album_description: str | None = None,
) -> list[str]:
    """Generate 3 visual descriptions for a track icon using Claude Haiku.

    The LLM interprets the track title and returns concrete visual concepts
    that an image generation model can render as 16x16 pixel art icons.

    Returns a list of 3 short visual descriptions, or empty list on failure.
    """
    context = ""
    if album_description:
        context = f"\n\nAlbum/show description:\n{album_description}\n"

    prompt = (
        f'I need 3 different visual concepts for a 16x16 pixel art icon '
        f'representing a children\'s audio track called "{track_title}".{context}\n'
        f'Each concept should be a concrete visual subject — an object, animal, '
        f'scene, or symbol that captures the track\'s meaning. '
        f'Do NOT describe characters from the show. '
        f'Focus on the emotion or concept the title conveys.\n\n'
        f'Return ONLY a JSON array of 3 short image prompts (under 15 words each). '
        f'Example: ["a smiling sun with rainbow", "two hands holding a heart", '
        f'"a bright yellow star with sparkles"]\n'
        f'No explanation, no markdown, just the JSON array.'
    )

    try:
        text = _call_claude(prompt)
        if text is None:
            return []
        descriptions = json.loads(text)
        if isinstance(descriptions, list) and len(descriptions) >= 3:
            return [str(d) for d in descriptions[:3]]
        return []
    except Exception:
        return []


def match_icon_llm(
    track_title: str,
    icons: list[dict],
) -> tuple[str | None, float]:
    """Match a track title to the best Yoto icon using Claude Haiku.

    Returns (mediaId, confidence) or (None, 0.0) if no match / failure.
    """
    if not icons or not track_title:
        return None, 0.0

    icon_lines = []
    for icon in icons:
        media_id = icon.get("mediaId", "")
        title = icon.get("title", "") or icon.get("name", "")
        if media_id and title:
            icon_lines.append(f"- mediaId: \"{media_id}\", title: \"{title}\"")

    if not icon_lines:
        return None, 0.0

    prompt = (
        f'Given the track title "{track_title}", which of these Yoto icons best '
        f'represents it? Return ONLY a JSON object: '
        f'{{"mediaId": "<best_match_id>", "confidence": <0.0-1.0>}}. '
        f'If nothing fits, return {{"mediaId": "none", "confidence": 0.0}}. '
        f'No explanation, no markdown, just JSON.\n\n'
        f'Icons:\n' + "\n".join(icon_lines)
    )

    try:
        text = _call_claude(prompt)
        if text is None:
            return None, 0.0
        data = json.loads(text)
        media_id = data.get("mediaId", "none")
        confidence = float(data.get("confidence", 0.0))
        if media_id == "none" or not media_id:
            return None, 0.0
        return media_id, confidence
    except Exception:
        return None, 0.0


def compare_icons_llm(
    track_title: str,
    candidates: list[bytes],
    yoto_icon: bytes | None = None,
) -> tuple[int, list[float]]:
    """Compare candidate icon images using Claude Haiku with vision.

    Writes images to temp files and asks Claude CLI to read and evaluate them.

    Args:
        track_title: The track title the icon should represent.
        candidates: List of PNG bytes (AI-generated icons).
        yoto_icon: Optional PNG bytes for the Yoto catalog icon (appended last).

    Returns:
        (winner, scores) where winner is 1-indexed. On failure, returns (1, []).
    """
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
            if yoto_icon is not None and i == len(paths):
                label += " (Yoto library icon)"
            file_list.append(f"{label}: {p}")

        prompt = (
            f'Read each of these icon images and evaluate which best represents '
            f'the track titled "{track_title}". '
            f'Score each from 0.0-1.0 on relevance and visual clarity. '
            f'Return ONLY a JSON object: '
            f'{{"winner": <1-indexed>, "scores": [<score_per_option>]}}. '
            f'No explanation, no markdown, just JSON.\n\n'
            + "\n".join(file_list)
        )

        try:
            text = _call_claude(prompt, allowed_tools="Read", timeout=60)
            if text is None:
                return 1, []
            data = json.loads(text)
            winner = int(data["winner"])
            scores = [float(s) for s in data["scores"]]
            return winner, scores
        except Exception:
            return 1, []
