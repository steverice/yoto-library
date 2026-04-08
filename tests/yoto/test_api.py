import hashlib
import httpx
import pytest
from unittest.mock import MagicMock

from yoto_lib.yoto.api import YotoAPI


@pytest.fixture
def api(mocker):
    mock_token = mocker.Mock()
    mock_token.access_token = "test_token"
    mock_token.token_type = "Bearer"
    mocker.patch("yoto_lib.yoto.api.get_valid_token", return_value=mock_token)
    return YotoAPI()


class TestYotoAPIContent:
    def test_get_my_content(self, api, mocker):
        mock_response = mocker.Mock()
        mock_response.json.return_value = {
            "cards": [
                {"cardId": "abc12", "title": "Test Playlist", "updatedAt": "2026-04-01T00:00:00Z"},
            ]
        }
        mock_response.raise_for_status = mocker.Mock()
        mocker.patch.object(api._client, "get", return_value=mock_response)

        cards = api.get_my_content()
        assert len(cards) == 1
        assert cards[0]["cardId"] == "abc12"

    def test_get_content_by_id(self, api, mocker):
        mock_response = mocker.Mock()
        mock_response.json.return_value = {
            "cardId": "abc12",
            "title": "Test",
            "content": {"chapters": []},
        }
        mock_response.raise_for_status = mocker.Mock()
        mocker.patch.object(api._client, "get", return_value=mock_response)

        card = api.get_content("abc12")
        assert card["cardId"] == "abc12"

    def test_get_content_with_playable_urls(self, api, mocker):
        mock_response = mocker.Mock()
        mock_response.json.return_value = {"cardId": "abc12"}
        mock_response.raise_for_status = mocker.Mock()
        mock_get = mocker.patch.object(api._client, "get", return_value=mock_response)

        api.get_content("abc12", playable=True)
        call_kwargs = mock_get.call_args
        assert "playable" in str(call_kwargs)

    def test_create_or_update_content(self, api, mocker):
        mock_response = mocker.Mock()
        mock_response.json.return_value = {"cardId": "new01"}
        mock_response.status_code = 200
        mocker.patch.object(api._client, "post", return_value=mock_response)

        content = {"title": "New Playlist", "content": {"chapters": []}}
        result = api.create_or_update_content(content)
        assert result["cardId"] == "new01"

    def test_delete_content(self, api, mocker):
        mock_response = mocker.Mock()
        mock_response.json.return_value = {"status": "ok"}
        mock_response.raise_for_status = mocker.Mock()
        mocker.patch.object(api._client, "delete", return_value=mock_response)

        result = api.delete_content("abc12")
        assert result["status"] == "ok"


class TestYotoAPIAudioUpload:
    def test_get_upload_url_returns_url_and_id(self, api, mocker):
        mock_response = mocker.Mock()
        mock_response.json.return_value = {
            "uploadId": "up123",
            "uploadUrl": "https://s3.example.com/upload",
        }
        mock_response.raise_for_status = mocker.Mock()
        mocker.patch.object(api._client, "get", return_value=mock_response)

        result = api.get_upload_url("abcdef1234567890" * 4)
        assert result["uploadId"] == "up123"
        assert result["uploadUrl"] is not None

    def test_get_upload_url_returns_none_url_for_duplicate(self, api, mocker):
        mock_response = mocker.Mock()
        mock_response.json.return_value = {
            "uploadId": "up123",
            "uploadUrl": None,
        }
        mock_response.raise_for_status = mocker.Mock()
        mocker.patch.object(api._client, "get", return_value=mock_response)

        result = api.get_upload_url("abcdef1234567890" * 4)
        assert result["uploadUrl"] is None

    def test_upload_audio_file(self, api, mocker, sample_wav):
        mock_response = mocker.Mock()
        mock_response.raise_for_status = mocker.Mock()
        mock_put = mocker.patch("httpx.put", return_value=mock_response)

        api.upload_audio_file("https://s3.example.com/upload", sample_wav)
        mock_put.assert_called_once()

    def test_poll_transcode(self, api, mocker):
        mock_response = mocker.Mock()
        mock_response.json.return_value = {
            "transcodedSha256": "t_sha_ok",
            "transcodedInfo": {
                "duration": 120.5,
                "fileSize": 1024000,
                "channels": "stereo",
                "format": "aac",
            },
        }
        mock_response.raise_for_status = mocker.Mock()
        mocker.patch.object(api._client, "get", return_value=mock_response)

        result = api.poll_transcode("up123", max_attempts=1, interval=0)
        assert result["transcodedSha256"] == "t_sha_ok"

    def test_poll_transcode_retries_until_ready(self, api, mocker):
        pending = mocker.Mock()
        pending.json.return_value = {}
        pending.raise_for_status = mocker.Mock()

        ready = mocker.Mock()
        ready.json.return_value = {"transcodedSha256": "t_sha_ok"}
        ready.raise_for_status = mocker.Mock()

        mocker.patch.object(api._client, "get", side_effect=[pending, pending, ready])
        mocker.patch("time.sleep")

        result = api.poll_transcode("up123", max_attempts=5, interval=0)
        assert result["transcodedSha256"] == "t_sha_ok"

    def test_upload_and_transcode_full_pipeline(self, api, mocker, sample_wav):
        mocker.patch.object(api, "get_upload_url", return_value={
            "uploadId": "up123",
            "uploadUrl": "https://s3.example.com/upload",
        })
        mocker.patch.object(api, "upload_audio_file")
        mocker.patch.object(api, "poll_transcode", return_value={
            "transcodedSha256": "t_sha_ok",
            "transcodedInfo": {"duration": 120.5},
        })

        result = api.upload_and_transcode(sample_wav)
        assert result["transcodedSha256"] == "t_sha_ok"


class TestYotoAPIMediaUpload:
    def test_upload_icon(self, api, mocker, tmp_path):
        icon_path = tmp_path / "icon.gif"
        icon_path.write_bytes(b"GIF89a\x10\x00\x10\x00" + b"\x00" * 50)

        mock_response = mocker.Mock()
        mock_response.json.return_value = {
            "displayIcon": {"mediaId": "icon_sha", "mediaUrl": "https://..."}
        }
        mock_response.raise_for_status = mocker.Mock()
        mocker.patch.object(api._client, "post", return_value=mock_response)

        result = api.upload_icon(icon_path, auto_convert=False)
        assert result["displayIcon"]["mediaId"] == "icon_sha"

    def test_upload_cover(self, api, mocker, tmp_path):
        cover_path = tmp_path / "cover.png"
        cover_path.write_bytes(b"\x89PNG" + b"\x00" * 50)

        mock_response = mocker.Mock()
        mock_response.json.return_value = {
            "coverImage": {"mediaId": "cover_sha", "mediaUrl": "https://..."}
        }
        mock_response.raise_for_status = mocker.Mock()
        mocker.patch.object(api._client, "post", return_value=mock_response)

        result = api.upload_cover(cover_path)
        assert result["coverImage"]["mediaId"] == "cover_sha"

    def test_get_public_icons(self, api, mocker):
        mock_response = mocker.Mock()
        mock_response.json.return_value = {
            "displayIcons": [
                {"mediaId": "abc", "title": "Music Note"},
                {"mediaId": "def", "title": "Star"},
            ]
        }
        mock_response.raise_for_status = mocker.Mock()
        mocker.patch.object(api._client, "get", return_value=mock_response)

        icons = api.get_public_icons()
        assert len(icons) == 2
        assert icons[0]["title"] == "Music Note"
