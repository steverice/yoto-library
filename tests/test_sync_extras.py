"""Tests for sync helper functions and edge cases."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yoto_lib.sync import _parse_remote_state, _infer_track_info


class TestInferTrackInfo:
    def test_returns_format_and_channels_from_probe(self):
        """Infers codec and channel info from ffprobe output."""
        fake_probe = {
            "streams": [
                {"codec_type": "audio", "codec_name": "aac", "channels": 2},
            ],
        }
        with patch("yoto_lib.mka.probe_audio", return_value=fake_probe):
            result = _infer_track_info(Path("track.mka"))

        assert result["format"] == "aac"
        assert result["channels"] == "stereo"

    def test_mono_channel(self):
        """Correctly identifies mono audio."""
        fake_probe = {
            "streams": [
                {"codec_type": "audio", "codec_name": "mp3", "channels": 1},
            ],
        }
        with patch("yoto_lib.mka.probe_audio", return_value=fake_probe):
            result = _infer_track_info(Path("track.mka"))

        assert result["format"] == "mp3"
        assert result["channels"] == "mono"

    def test_returns_empty_on_probe_failure(self):
        """Returns empty dict when ffprobe fails."""
        with patch("yoto_lib.mka.probe_audio", side_effect=OSError("not found")):
            result = _infer_track_info(Path("missing.mka"))

        assert result == {}

    def test_returns_empty_when_no_audio_streams(self):
        """Returns empty dict when there are no audio streams."""
        fake_probe = {
            "streams": [
                {"codec_type": "video", "codec_name": "png"},
            ],
        }
        with patch("yoto_lib.mka.probe_audio", return_value=fake_probe):
            result = _infer_track_info(Path("icon_only.mka"))

        assert result == {}


class TestParseRemoteStateTrackInfo:
    def test_extracts_format_and_channels(self):
        """Extracts track format and channels from remote content."""
        remote = {
            "content": {
                "chapters": [
                    {
                        "key": "ch1",
                        "title": "Song",
                        "tracks": [{
                            "trackUrl": "yoto:#abc",
                            "format": "opus",
                            "channels": "stereo",
                        }],
                    }
                ]
            }
        }
        state = _parse_remote_state(remote)
        assert state["track_info"]["Song"] == {"format": "opus", "channels": "stereo"}

    def test_no_format_info_omitted(self):
        """Tracks without format/channels are not added to track_info."""
        remote = {
            "content": {
                "chapters": [
                    {
                        "key": "ch1",
                        "title": "Song",
                        "tracks": [{"trackUrl": "yoto:#abc"}],
                    }
                ]
            }
        }
        state = _parse_remote_state(remote)
        assert "Song" not in state["track_info"]

    def test_description_from_remote(self):
        """Extracts description from top-level 'description' key."""
        remote = {
            "content": {"chapters": []},
            "description": "A great playlist",
        }
        state = _parse_remote_state(remote)
        assert state["description"] == "A great playlist"

    def test_description_defaults_to_none(self):
        """Returns None when no description is present."""
        remote = {"content": {"chapters": []}}
        state = _parse_remote_state(remote)
        assert state["description"] is None
