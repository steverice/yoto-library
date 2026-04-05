"""Yoto API client for content management, uploads, and transcoding."""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

import httpx

from yoto_lib.auth import get_valid_token

logger = logging.getLogger(__name__)

API_BASE = "https://api.yotoplay.com"


class YotoAPIError(Exception):
    pass


def _guess_audio_content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".flac": "audio/flac",
        ".ogg": "audio/ogg",
        ".m4a": "audio/mp4",
        ".aac": "audio/aac",
        ".mka": "audio/x-matroska",
        ".wma": "audio/x-ms-wma",
    }.get(suffix, "application/octet-stream")


class YotoAPI:
    def __init__(self, interactive: bool = True):
        token = get_valid_token(interactive=interactive)
        self._client = httpx.Client(
            base_url=API_BASE,
            headers={"Authorization": f"{token.token_type} {token.access_token}"},
            timeout=30.0,
        )

    # ── Content operations ────────────────────────────────────────────────────

    def get_my_content(self, show_deleted: bool = False) -> list[dict]:
        logger.debug("GET /content/mine show_deleted=%s", show_deleted)
        response = self._client.get(
            "/content/mine",
            params={"showdeleted": str(show_deleted).lower()},
        )
        response.raise_for_status()
        cards = response.json().get("cards", [])
        logger.debug("GET /content/mine -> %d cards", len(cards))
        return cards

    def get_content(self, card_id: str, playable: bool = False) -> dict:
        logger.debug("GET /content/%s playable=%s", card_id, playable)
        params = {}
        if playable:
            params["playable"] = "true"
            params["signingType"] = "s3"
        response = self._client.get(f"/content/{card_id}", params=params)
        response.raise_for_status()
        data = response.json()
        return data.get("card", data)

    def create_or_update_content(self, content: dict) -> dict:
        logger.debug("POST /content card_id=%s", content.get("cardId", "new"))
        response = self._client.post("/content", json=content)
        if response.status_code >= 400:
            try:
                body = response.json()
            except Exception:
                body = response.text
            logger.error("POST /content failed: %s %s", response.status_code, body)
            raise YotoAPIError(
                f"{response.status_code} from POST /content: {body}"
            )
        data = response.json()
        card = data.get("card", data)
        logger.debug("POST /content -> card_id=%s", card.get("cardId"))
        return card

    def delete_content(self, card_id: str) -> dict:
        logger.debug("DELETE /content/%s", card_id)
        response = self._client.delete(f"/content/{card_id}")
        response.raise_for_status()
        return response.json()

    # ── Audio upload pipeline ─────────────────────────────────────────────────

    def get_upload_url(self, sha256_hex: str, filename: str | None = None) -> dict:
        logger.debug("GET uploadUrl sha256=%s...%s filename=%s", sha256_hex[:8], sha256_hex[-8:], filename)
        params = {"sha256": sha256_hex}
        if filename:
            params["filename"] = filename
        response = self._client.get(
            "/media/transcode/audio/uploadUrl",
            params=params,
        )
        response.raise_for_status()
        data = response.json()
        upload = data.get("upload", data)
        logger.debug("GET uploadUrl -> upload_id=%s needs_upload=%s", upload.get("uploadId"), upload.get("uploadUrl") is not None)
        return upload

    def upload_audio_file(self, upload_url: str, file_path: Path) -> None:
        content_type = _guess_audio_content_type(file_path)
        data = file_path.read_bytes()
        logger.debug("PUT upload %s (%d bytes, %s)", file_path.name, len(data), content_type)
        response = httpx.put(
            upload_url,
            content=data,
            headers={"Content-Type": content_type},
            timeout=300.0,
        )
        response.raise_for_status()
        logger.debug("PUT upload %s -> %s", file_path.name, response.status_code)

    def poll_transcode(
        self, upload_id: str, max_attempts: int = 30, interval: float = 0.5
    ) -> dict:
        logger.debug("poll_transcode: %s (max %d attempts)", upload_id, max_attempts)
        for attempt in range(max_attempts):
            response = self._client.get(
                f"/media/upload/{upload_id}/transcoded",
                params={"loudnorm": "false"},
            )
            response.raise_for_status()
            raw = response.json()
            data = raw.get("transcode", raw)
            if "transcodedSha256" in data:
                logger.debug("poll_transcode: %s complete (attempt %d): %s", upload_id, attempt + 1, data)
                return data
            time.sleep(interval)
        logger.error("poll_transcode: %s timed out after %d attempts", upload_id, max_attempts)
        raise YotoAPIError(
            f"Transcoding timed out after {max_attempts} attempts for upload {upload_id}"
        )

    def upload_and_transcode(self, file_path: Path) -> dict:
        """Upload and transcode an audio file. Extracts MKA to native format first."""
        import tempfile

        if file_path.suffix.lower() == ".mka":
            from yoto_lib.mka import extract_audio

            logger.debug("upload_and_transcode: extracting %s from MKA", file_path.name)
            with tempfile.TemporaryDirectory(prefix="yoto-upload-") as tmpdir:
                upload_path = extract_audio(file_path, Path(tmpdir))
                return self._do_upload_and_transcode(upload_path)
        else:
            return self._do_upload_and_transcode(file_path)

    def _do_upload_and_transcode(self, file_path: Path) -> dict:
        logger.debug("_do_upload_and_transcode: %s", file_path.name)
        sha256_hex = hashlib.sha256(file_path.read_bytes()).hexdigest()
        upload_info = self.get_upload_url(sha256_hex, filename=file_path.name)

        if upload_info["uploadUrl"] is not None:
            self.upload_audio_file(upload_info["uploadUrl"], file_path)
        else:
            logger.debug("_do_upload_and_transcode: %s already uploaded, skipping", file_path.name)

        return self.poll_transcode(upload_info["uploadId"])

    # ── Media uploads ─────────────────────────────────────────────────────────

    def upload_icon(self, file_path: Path, auto_convert: bool = False) -> dict:
        logger.debug("POST upload icon %s", file_path.name)
        content_type = "image/gif" if file_path.suffix == ".gif" else "image/png"
        response = self._client.post(
            "/media/displayIcons/user/me/upload",
            content=file_path.read_bytes(),
            params={"autoConvert": str(auto_convert).lower()},
            headers={"Content-Type": content_type},
        )
        response.raise_for_status()
        result = response.json()
        logger.debug("POST upload icon -> %s", result.get("displayIcon", result).get("mediaId"))
        return result

    def upload_cover(self, file_path: Path) -> dict:
        logger.debug("POST upload cover %s", file_path.name)
        content_type = "image/png" if file_path.suffix == ".png" else "image/jpeg"
        response = self._client.post(
            "/media/coverImage/user/me/upload",
            content=file_path.read_bytes(),
            params={"autoconvert": "true"},
            headers={"Content-Type": content_type},
        )
        response.raise_for_status()
        return response.json()

    def get_public_icons(self) -> list[dict]:
        logger.debug("GET /media/displayIcons/user/yoto")
        response = self._client.get("/media/displayIcons/user/yoto")
        response.raise_for_status()
        icons = response.json().get("displayIcons", [])
        logger.debug("GET /media/displayIcons/user/yoto -> %d icons", len(icons))
        return icons

    def get_user_icons(self) -> list[dict]:
        logger.debug("GET /media/displayIcons/user/me")
        response = self._client.get("/media/displayIcons/user/me")
        response.raise_for_status()
        icons = response.json().get("displayIcons", [])
        logger.debug("GET /media/displayIcons/user/me -> %d icons", len(icons))
        return icons
