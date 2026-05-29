"""Tests for the icon selection orchestration in yoto_lib.icons.select."""

from __future__ import annotations

import io
import threading
from unittest.mock import MagicMock, patch

from PIL import Image


def _png_16() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), "black").save(buf, format="PNG")
    return buf.getvalue()


class TestReferenceThreading:
    def test_generate_round_forwards_reference(self):
        """_generate_round passes reference_image + strength to generate_retrodiffusion_icons."""
        from yoto_lib.icons.select import _generate_round

        batch = [(_png_16(), Image.new("RGB", (16, 16), "black"))]
        with (
            patch("yoto_lib.icons.icon_llm.describe_icons_llm", return_value=["a", "b", "c"]),
            patch("yoto_lib.icons.icon_llm.match_icon_llm", return_value=(None, 0.0)),
            patch("yoto_lib.icons.generate_retrodiffusion_icons", return_value=batch) as mock_gen,
        ):
            _generate_round(
                title="Track",
                album_desc=None,
                lyrics_summary=None,
                catalog=[],
                existing_img=None,
                reference_image=b"ref-bytes",
                strength=0.6,
            )

        assert mock_gen.call_args.kwargs["reference_image"] == b"ref-bytes"
        assert mock_gen.call_args.kwargs["strength"] == 0.6

    def test_select_forwards_reference_to_generate_round(self, tmp_path):
        """select_icons_for_tracks forwards reference_image + strength to each round."""
        from yoto_lib.icons.select import select_icons_for_tracks

        track = tmp_path / "song.mka"
        track.write_bytes(b"x")

        with (
            patch("yoto_lib.icons.icon_catalog.get_catalog", return_value=[]),
            patch("yoto_lib.icons.select._read_album_description", return_value=None),
            patch("yoto_lib.icons.select._ensure_lyrics_summary", return_value=None),
            patch("yoto_lib.icons.select._get_existing_icon", return_value=None),
            patch("yoto_lib.icons.select._generate_round", return_value=None) as mock_round,
        ):
            select_icons_for_tracks([track], MagicMock(), reference_image=b"ref-bytes", strength=0.6)

        assert mock_round.call_args.kwargs["reference_image"] == b"ref-bytes"
        assert mock_round.call_args.kwargs["strength"] == 0.6


class TestScoringCancellation:
    def test_selecting_icon_cancels_in_flight_scoring(self, tmp_path):
        """Choosing an icon while scoring is still running cancels its token."""
        from yoto_lib.icons.select import IconCandidate, IconSelectionRound, select_icons_for_tracks

        track = tmp_path / "song.mka"
        track.write_bytes(b"x")

        captured: dict[str, object] = {}
        started = threading.Event()

        def fake_compare(
            title,
            candidates,
            *,
            yoto_icon=None,
            descriptions=None,
            album_description=None,
            cancel_token=None,
        ):
            captured["token"] = cancel_token
            started.set()
            # Simulate long-running scoring that respects cancellation, with a
            # ceiling so a regression can never hang the test forever.
            cancel_token._event.wait(timeout=5)
            return 1, []

        round_obj = IconSelectionRound(
            candidates=[IconCandidate(image=Image.new("RGB", (16, 16), "black"), label="ai")],
            winner=0,
            scores=[],
            descriptions=["ai"],
            batch=[(_png_16(), Image.new("RGB", (16, 16), "black"))],
            yoto_bytes=None,
        )

        def fake_choose(_round, _future):
            started.wait(2)  # let scoring get in flight before we choose
            return "1"

        with (
            patch("yoto_lib.icons.icon_catalog.get_catalog", return_value=[]),
            patch("yoto_lib.icons.select._read_album_description", return_value=None),
            patch("yoto_lib.icons.select._ensure_lyrics_summary", return_value=None),
            patch("yoto_lib.icons.select._get_existing_icon", return_value=None),
            patch("yoto_lib.icons.select._generate_round", return_value=round_obj),
            patch("yoto_lib.icons.icon_llm.compare_icons_llm", side_effect=fake_compare),
            patch("yoto_lib.icons.select._apply_chosen_icon"),
            patch("yoto_lib.icons.icon_llm.log_icon_feedback"),
        ):
            select_icons_for_tracks([track], MagicMock(), choose_icon=fake_choose)

        token = captured["token"]
        assert token is not None
        assert token.cancelled() is True
