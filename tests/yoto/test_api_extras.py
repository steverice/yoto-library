"""Tests for API helper functions and error paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from yoto_lib.yoto.api import YotoAPI, YotoAPIError, _guess_audio_content_type


class TestGuessAudioContentType:
    def test_mp3(self):
        assert _guess_audio_content_type(Path("track.mp3")) == "audio/mpeg"

    def test_wav(self):
        assert _guess_audio_content_type(Path("track.wav")) == "audio/wav"

    def test_flac(self):
        assert _guess_audio_content_type(Path("track.flac")) == "audio/flac"

    def test_ogg(self):
        assert _guess_audio_content_type(Path("track.ogg")) == "audio/ogg"

    def test_m4a(self):
        assert _guess_audio_content_type(Path("track.m4a")) == "audio/mp4"

    def test_aac(self):
        assert _guess_audio_content_type(Path("track.aac")) == "audio/aac"

    def test_mka(self):
        assert _guess_audio_content_type(Path("track.mka")) == "audio/x-matroska"

    def test_wma(self):
        assert _guess_audio_content_type(Path("track.wma")) == "audio/x-ms-wma"

    def test_unknown_extension(self):
        assert _guess_audio_content_type(Path("track.xyz")) == "application/octet-stream"

    def test_case_insensitive(self):
        assert _guess_audio_content_type(Path("track.MP3")) == "audio/mpeg"
        assert _guess_audio_content_type(Path("track.Flac")) == "audio/flac"


class TestPollTranscodeTimeout:
    def test_timeout_raises_error(self, mocker):
        """poll_transcode raises YotoAPIError when max_attempts exceeded."""
        mock_token = mocker.Mock()
        mock_token.access_token = "test_token"
        mock_token.token_type = "Bearer"
        mocker.patch("yoto_lib.yoto.api.get_valid_token", return_value=mock_token)
        api = YotoAPI()

        # Return a response without transcodedSha256 every time
        pending = mocker.Mock()
        pending.json.return_value = {"status": "processing"}
        pending.raise_for_status = mocker.Mock()
        mocker.patch.object(api._client, "get", return_value=pending)
        mocker.patch("time.sleep")

        with pytest.raises(YotoAPIError, match="timed out"):
            api.poll_transcode("up123", max_attempts=3, interval=0)


class TestCreateOrUpdateContentError:
    def test_http_error_raises_api_error(self, mocker):
        """create_or_update_content raises YotoAPIError on HTTP 4xx/5xx."""
        mock_token = mocker.Mock()
        mock_token.access_token = "test_token"
        mock_token.token_type = "Bearer"
        mocker.patch("yoto_lib.yoto.api.get_valid_token", return_value=mock_token)
        api = YotoAPI()

        mock_response = mocker.Mock()
        mock_response.status_code = 400
        mock_response.json.return_value = {"error": "bad request"}
        mocker.patch.object(api._client, "post", return_value=mock_response)

        with pytest.raises(YotoAPIError, match="400"):
            api.create_or_update_content({"title": "test"})


class TestUploadAndTranscodeSkipsDuplicate:
    def test_skips_upload_for_duplicate(self, mocker):
        """When uploadUrl is None (duplicate), upload_audio_file is not called."""
        mock_token = mocker.Mock()
        mock_token.access_token = "test_token"
        mock_token.token_type = "Bearer"
        mocker.patch("yoto_lib.yoto.api.get_valid_token", return_value=mock_token)
        api = YotoAPI()

        mocker.patch.object(
            api,
            "get_upload_url",
            return_value={
                "uploadId": "up123",
                "uploadUrl": None,  # duplicate
            },
        )
        mock_upload = mocker.patch.object(api, "upload_audio_file")
        mocker.patch.object(
            api,
            "poll_transcode",
            return_value={
                "transcodedSha256": "abc",
            },
        )

        sample = mocker.Mock()
        sample.read_bytes.return_value = b"\x00" * 10
        sample.name = "track.mka"

        api.upload_and_transcode(sample)
        mock_upload.assert_not_called()


class TestGetContentUnwrapsCard:
    def test_unwraps_card_wrapper(self, mocker):
        """get_content returns data.card when present."""
        mock_token = mocker.Mock()
        mock_token.access_token = "test_token"
        mock_token.token_type = "Bearer"
        mocker.patch("yoto_lib.yoto.api.get_valid_token", return_value=mock_token)
        api = YotoAPI()

        mock_response = mocker.Mock()
        mock_response.json.return_value = {"card": {"cardId": "abc12", "title": "Test"}}
        mock_response.raise_for_status = mocker.Mock()
        mocker.patch.object(api._client, "get", return_value=mock_response)

        result = api.get_content("abc12")
        assert result["cardId"] == "abc12"

    def test_returns_data_when_no_card_wrapper(self, mocker):
        """get_content returns data directly when no card wrapper."""
        mock_token = mocker.Mock()
        mock_token.access_token = "test_token"
        mock_token.token_type = "Bearer"
        mocker.patch("yoto_lib.yoto.api.get_valid_token", return_value=mock_token)
        api = YotoAPI()

        mock_response = mocker.Mock()
        mock_response.json.return_value = {"cardId": "abc12", "title": "Test"}
        mock_response.raise_for_status = mocker.Mock()
        mocker.patch.object(api._client, "get", return_value=mock_response)

        result = api.get_content("abc12")
        assert result["cardId"] == "abc12"
