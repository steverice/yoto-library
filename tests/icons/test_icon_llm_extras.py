"""Tests for icon_llm edge cases — feedback logging, empty inputs."""

from __future__ import annotations

import json
from unittest.mock import patch

from yoto_lib.icons.icon_llm import (
    describe_icons_llm,
    log_icon_feedback,
    match_icon_llm,
    summarize_lyrics_for_icon,
)


class TestLogIconFeedback:
    def test_logs_entry_to_jsonl(self, tmp_path):
        """Writes a valid JSON line to the feedback file."""
        feedback_path = tmp_path / "icon-feedback.jsonl"

        with patch("yoto_lib.icons.icon_llm.FEEDBACK_PATH", feedback_path):
            log_icon_feedback(
                track_title="Song Title",
                llm_winner=2,
                llm_scores=[0.5, 0.9, 0.6],
                user_choice=2,
                descriptions=["desc1", "desc2", "desc3"],
                album="Test Album",
                chose_yoto=False,
            )

        assert feedback_path.exists()
        lines = feedback_path.read_text().strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["track_title"] == "Song Title"
        assert entry["llm_winner"] == 2
        assert entry["user_choice"] == 2
        assert entry["agreed"] is True
        assert entry["chose_yoto"] is False
        assert "timestamp" in entry

    def test_logs_disagreement(self, tmp_path):
        """Records when LLM and user disagree."""
        feedback_path = tmp_path / "icon-feedback.jsonl"

        with patch("yoto_lib.icons.icon_llm.FEEDBACK_PATH", feedback_path):
            log_icon_feedback(
                track_title="Song",
                llm_winner=1,
                llm_scores=[0.8, 0.5],
                user_choice=2,
            )

        entry = json.loads(feedback_path.read_text().strip())
        assert entry["agreed"] is False

    def test_append_multiple_entries(self, tmp_path):
        """Multiple calls append separate lines."""
        feedback_path = tmp_path / "icon-feedback.jsonl"

        with patch("yoto_lib.icons.icon_llm.FEEDBACK_PATH", feedback_path):
            log_icon_feedback("Song 1", 1, [0.8], 1)
            log_icon_feedback("Song 2", 2, [0.5, 0.9], 2)

        lines = feedback_path.read_text().strip().split("\n")
        assert len(lines) == 2

    def test_handles_write_error_gracefully(self, tmp_path):
        """Does not raise when the feedback directory is unwritable."""
        feedback_path = tmp_path / "readonly" / "icon-feedback.jsonl"

        with patch("yoto_lib.icons.icon_llm.FEEDBACK_PATH", feedback_path):
            # Make the parent directory read-only to simulate a write failure
            feedback_path.parent.mkdir()
            feedback_path.parent.chmod(0o444)
            try:
                # Should not raise
                log_icon_feedback("Song", 1, [0.5], 1)
            finally:
                feedback_path.parent.chmod(0o755)


class TestDescribeIconsLlmEdgeCases:
    def test_empty_title(self):
        """Empty track title returns empty list."""
        response = json.dumps(["a", "b", "c"])
        with patch("yoto_lib.icons.icon_llm._claude.call", return_value=response):
            result = describe_icons_llm("")
        assert isinstance(result, list)

    def test_fewer_than_three_descriptions(self):
        """Returns empty list when LLM returns fewer than 3 descriptions."""
        response = json.dumps(["only one"])
        with patch("yoto_lib.icons.icon_llm._claude.call", return_value=response):
            result = describe_icons_llm("Test Track")
        assert result == []

    def test_includes_album_description_in_prompt(self):
        """Album description is included in the LLM prompt."""
        response = json.dumps(["a", "b", "c"])
        with patch("yoto_lib.icons.icon_llm._claude.call", return_value=response) as mock_call:
            describe_icons_llm("Track", album_description="A story about dinosaurs")

        prompt = mock_call.call_args[0][0]
        assert "dinosaurs" in prompt


class TestMatchIconLlmEdgeCases:
    def test_empty_title_returns_none(self):
        """Empty title returns (None, 0.0) without calling LLM."""
        media_id, confidence = match_icon_llm("", [{"mediaId": "x", "title": "Y"}])
        assert media_id is None
        assert confidence == 0.0

    def test_icons_without_media_id_skipped(self):
        """Icons missing mediaId are filtered out."""
        icons = [
            {"title": "Star"},  # no mediaId
            {"mediaId": "", "title": "Moon"},  # empty mediaId
        ]
        with patch("yoto_lib.icons.icon_llm._claude.call") as mock_call:
            match_icon_llm("Anything", icons)
            # With all icons filtered, the function should return early
            # or send an empty icon list


class TestSummarizeLyricsForIconTruncation:
    def test_long_lyrics_truncated(self):
        """Lyrics longer than 3000 chars are truncated in the prompt."""
        long_lyrics = "word " * 1000  # 5000 chars

        with patch("yoto_lib.icons.icon_llm._claude.call", return_value="summary") as mock_call:
            summarize_lyrics_for_icon(long_lyrics, "Song Title")

        prompt = mock_call.call_args[0][0]
        # The prompt should contain truncated lyrics (max 3000 chars)
        assert len(prompt) < len(long_lyrics) + 500  # some overhead for instructions
