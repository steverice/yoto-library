"""LLM-based icon matching and comparison via Anthropic SDK."""

from __future__ import annotations

import base64
import json

# Zone thresholds
CONFIDENCE_HIGH = 0.8
CONFIDENCE_LOW = 0.4


def _call_anthropic(
    system: str,
    user_content: str | list,
    max_tokens: int = 256,
) -> str:
    """Call Claude Haiku and return the text response.

    Raises on any failure so callers can handle gracefully.
    """
    import anthropic

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user_content}],
    )
    return response.content[0].text


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

    system = (
        "You match track titles to icons. Respond with ONLY a JSON object: "
        '{"mediaId": "<best_match_id>", "confidence": <0.0-1.0>}. '
        'If nothing fits, return {"mediaId": "none", "confidence": 0.0}. '
        "No explanation, no markdown, just JSON."
    )

    user_msg = (
        f'Track title: "{track_title}"\n\n'
        f"Which icon best represents this track?\n\n"
        f"Icons:\n" + "\n".join(icon_lines)
    )

    try:
        text = _call_anthropic(system, user_msg)
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

    content: list[dict] = []
    for i, img_bytes in enumerate(all_images, 1):
        label = f"Option {i}"
        if yoto_icon is not None and i == len(all_images):
            label += " (Yoto library icon)"
        content.append({"type": "text", "text": f"{label}:"})
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.b64encode(img_bytes).decode(),
            },
        })

    content.append({
        "type": "text",
        "text": (
            f'\nTrack title: "{track_title}"\n'
            f"Which icon best represents this track? "
            f"Score each from 0.0-1.0 on relevance and visual clarity."
        ),
    })

    system = (
        "You evaluate icons for audio tracks. Respond with ONLY a JSON object: "
        '{"winner": <1-indexed>, "scores": [<score_per_option>]}. '
        "No explanation, no markdown, just JSON."
    )

    try:
        text = _call_anthropic(system, content, max_tokens=256)
        data = json.loads(text)
        winner = int(data["winner"])
        scores = [float(s) for s in data["scores"]]
        return winner, scores
    except Exception:
        return 1, []
