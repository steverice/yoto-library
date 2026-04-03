"""Yoto API client for content management, uploads, and transcoding."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import httpx

from yoto_lib.auth import get_valid_token

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
        response = self._client.get(
            "/content/mine",
            params={"showdeleted": str(show_deleted).lower()},
        )
        response.raise_for_status()
        return response.json().get("cards", [])

    def get_content(self, card_id: str, playable: bool = False) -> dict:
        params = {}
        if playable:
            params["playable"] = "true"
            params["signingType"] = "s3"
        response = self._client.get(f"/content/{card_id}", params=params)
        response.raise_for_status()
        return response.json()

    def create_or_update_content(self, content: dict) -> dict:
        response = self._client.post("/content", json=content)
        response.raise_for_status()
        return response.json()

    def delete_content(self, card_id: str) -> dict:
        response = self._client.delete(f"/content/{card_id}")
        response.raise_for_status()
        return response.json()

    # ── Audio upload pipeline ─────────────────────────────────────────────────

    def get_upload_url(self, sha256_hex: str, filename: str | None = None) -> dict:
        params = {"sha256": sha256_hex}
        if filename:
            params["filename"] = filename
        response = self._client.get(
            "/media/transcode/audio/uploadUrl",
            params=params,
        )
        response.raise_for_status()
        return response.json()

    def upload_audio_file(self, upload_url: str, file_path: Path) -> None:
        content_type = _guess_audio_content_type(file_path)
        data = file_path.read_bytes()
        response = httpx.put(
            upload_url,
            content=data,
            headers={"Content-Type": content_type},
            timeout=300.0,
        )
        response.raise_for_status()

    def poll_transcode(
        self, upload_id: str, max_attempts: int = 30, interval: float = 0.5
    ) -> dict:
        for _ in range(max_attempts):
            response = self._client.get(
                f"/media/upload/{upload_id}/transcoded",
                params={"loudnorm": "false"},
            )
            response.raise_for_status()
            data = response.json()
            if "transcodedSha256" in data:
                return data
            time.sleep(interval)
        raise YotoAPIError(
            f"Transcoding timed out after {max_attempts} attempts for upload {upload_id}"
        )

    def upload_and_transcode(self, file_path: Path) -> dict:
        sha256_hex = hashlib.sha256(file_path.read_bytes()).hexdigest()
        upload_info = self.get_upload_url(sha256_hex, filename=file_path.name)

        if upload_info["uploadUrl"] is not None:
            self.upload_audio_file(upload_info["uploadUrl"], file_path)

        return self.poll_transcode(upload_info["uploadId"])

    # ── Media uploads ─────────────────────────────────────────────────────────

    def upload_icon(self, file_path: Path, auto_convert: bool = False) -> dict:
        content_type = "image/gif" if file_path.suffix == ".gif" else "image/png"
        response = self._client.post(
            "/media/displayIcons/user/me/upload",
            content=file_path.read_bytes(),
            params={"autoConvert": str(auto_convert).lower()},
            headers={"Content-Type": content_type},
        )
        response.raise_for_status()
        return response.json()

    def upload_cover(self, file_path: Path) -> dict:
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
        response = self._client.get("/media/displayIcons/user/yoto")
        response.raise_for_status()
        return response.json().get("displayIcons", [])

    def get_user_icons(self) -> list[dict]:
        response = self._client.get("/media/displayIcons/user/me")
        response.raise_for_status()
        return response.json().get("displayIcons", [])
