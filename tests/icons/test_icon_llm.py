"""Tests for LLM-based icon matching and comparison."""

from __future__ import annotations

import json
from unittest.mock import patch

from yoto_lib.icons.icon_llm import (
    compare_icons_llm,
    describe_icons_llm,
    match_icon_llm,
    summarize_lyrics_for_icon,
)


class TestMatchIconLlm:
    def test_high_confidence_match(self):
        """Returns mediaId and confidence when LLM finds a strong match."""
        icons = [
            {"mediaId": "dino-id", "title": "Dinosaur"},
            {"mediaId": "star-id", "title": "Star"},
        ]
        response_json = json.dumps({"mediaId": "dino-id", "confidence": 0.92})

        with patch("yoto_lib.icons.icon_llm._claude.call", return_value=response_json):
            media_id, confidence = match_icon_llm("Dinosaur Stories", icons)

        assert media_id == "dino-id"
        assert confidence == 0.92

    def test_no_match_returns_none(self):
        """Returns (None, 0.0) when LLM says nothing fits."""
        icons = [{"mediaId": "star-id", "title": "Star"}]
        response_json = json.dumps({"mediaId": "none", "confidence": 0.0})

        with patch("yoto_lib.icons.icon_llm._claude.call", return_value=response_json):
            media_id, confidence = match_icon_llm("Quantum Physics Lecture", icons)

        assert media_id is None
        assert confidence == 0.0

    def test_api_failure_returns_none(self):
        """Returns (None, 0.0) when the Claude CLI call fails."""
        icons = [{"mediaId": "star-id", "title": "Star"}]

        with patch("yoto_lib.icons.icon_llm._claude.call", return_value=None):
            media_id, confidence = match_icon_llm("Anything", icons)

        assert media_id is None
        assert confidence == 0.0

    def test_empty_catalog_returns_none(self):
        """Returns (None, 0.0) when the icon catalog is empty."""
        media_id, confidence = match_icon_llm("Anything", [])
        assert media_id is None
        assert confidence == 0.0

    def test_malformed_json_returns_none(self):
        """Returns (None, 0.0) when the LLM returns unparseable output."""
        icons = [{"mediaId": "star-id", "title": "Star"}]

        with patch("yoto_lib.icons.icon_llm._claude.call", return_value="not json at all"):
            media_id, confidence = match_icon_llm("Anything", icons)

        assert media_id is None
        assert confidence == 0.0


def _make_red_png() -> bytes:
    """Create a minimal 16x16 red PNG."""
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (16, 16), "red").save(buf, format="PNG")
    return buf.getvalue()


class TestSummarizeLyricsForIcon:
    def test_returns_summary_on_success(self):
        with patch(
            "yoto_lib.icons.icon_llm._claude.call", return_value="A bear climbing a tall oak tree to reach a beehive"
        ):
            result = summarize_lyrics_for_icon(
                "Old MacDonald had a farm, E-I-E-I-O, and on his farm he had a cow",
                "Old MacDonald",
            )
        assert result == "A bear climbing a tall oak tree to reach a beehive"

    def test_returns_none_on_failure(self):
        with patch("yoto_lib.icons.icon_llm._claude.call", return_value=None):
            result = summarize_lyrics_for_icon("some lyrics", "Some Song")
        assert result is None

    def test_returns_none_for_empty_response(self):
        with patch("yoto_lib.icons.icon_llm._claude.call", return_value=""):
            result = summarize_lyrics_for_icon("some lyrics", "Some Song")
        assert result is None


class TestDescribeIconsWithLyrics:
    def test_includes_lyrics_summary_in_prompt(self):
        response_json = json.dumps(["bear in tree", "beehive", "farm animals"])

        with patch("yoto_lib.icons.icon_llm._claude.call", return_value=response_json) as mock_claude:
            result = describe_icons_llm(
                "Old MacDonald",
                lyrics_summary="Farm animals including cows, pigs, and chickens on a green pasture",
            )

        assert len(result) == 3
        # Verify the prompt included the lyrics summary
        call_prompt = mock_claude.call_args[0][0]
        assert "Farm animals including cows, pigs, and chickens" in call_prompt

    def test_works_without_lyrics_summary(self):
        response_json = json.dumps(["concept 1", "concept 2", "concept 3"])

        with patch("yoto_lib.icons.icon_llm._claude.call", return_value=response_json) as mock_claude:
            result = describe_icons_llm("Some Track")

        assert len(result) == 3
        call_prompt = mock_claude.call_args[0][0]
        assert "Lyrics context" not in call_prompt


class TestCompareIconsLlm:
    def test_picks_winner_from_candidates(self):
        """Returns the 1-indexed winner and scores for each candidate."""
        candidates = [_make_red_png(), _make_red_png(), _make_red_png()]
        response_json = json.dumps({"winner": 2, "scores": [0.5, 0.9, 0.6]})

        with patch("yoto_lib.icons.icon_llm._claude.call", return_value=response_json):
            winner, scores = compare_icons_llm("Dinosaur Story", candidates)

        assert winner == 2
        assert scores == [0.5, 0.9, 0.6]

    def test_with_yoto_candidate(self):
        """When a Yoto icon is included, it appears as the last candidate."""
        ai_icons = [_make_red_png(), _make_red_png(), _make_red_png()]
        yoto_icon = _make_red_png()
        response_json = json.dumps({"winner": 4, "scores": [0.5, 0.6, 0.5, 0.85]})

        with patch("yoto_lib.icons.icon_llm._claude.call", return_value=response_json):
            winner, scores = compare_icons_llm(
                "Dinosaur Story",
                ai_icons,
                yoto_icon=yoto_icon,
            )

        assert winner == 4
        assert len(scores) == 4

    def test_api_failure_returns_first(self):
        """On API failure, returns winner=1 (first candidate)."""
        candidates = [_make_red_png(), _make_red_png()]

        with patch("yoto_lib.icons.icon_llm._claude.call", return_value=None):
            winner, scores = compare_icons_llm("Title", candidates)

        assert winner == 1
        assert scores == []
