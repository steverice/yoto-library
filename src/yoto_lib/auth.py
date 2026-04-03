"""OAuth 2.0 Device Code flow for Yoto API, with Keychain token storage."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

import httpx
import keyring

AUTH_BASE = "https://login.yotoplay.com"
API_AUDIENCE = "https://api.yotoplay.com"
CLIENT_ID = "kT1e1feuj42SxERTSWearGDWWmNeQ15x"
SCOPES = "profile offline_access openid"
KEYCHAIN_SERVICE = "yoto-library"
KEYCHAIN_ACCOUNT = "tokens"
REFRESH_MARGIN_SECONDS = 30


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str
    token_type: str
    expires_at: float

    @classmethod
    def from_auth_response(cls, response: dict) -> TokenSet:
        return cls(
            access_token=response["access_token"],
            refresh_token=response["refresh_token"],
            token_type=response["token_type"],
            expires_at=time.time() + response["expires_in"],
        )

    def is_expired(self) -> bool:
        return time.time() >= self.expires_at

    def needs_refresh(self) -> bool:
        return time.time() >= self.expires_at - REFRESH_MARGIN_SECONDS

    def to_json(self) -> str:
        return json.dumps({
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "token_type": self.token_type,
            "expires_at": self.expires_at,
        })

    @classmethod
    def from_json(cls, data: str) -> TokenSet:
        d = json.loads(data)
        return cls(**d)


def save_tokens(tokens: TokenSet) -> None:
    keyring.set_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT, tokens.to_json())


def load_tokens() -> TokenSet | None:
    data = keyring.get_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
    if data is None:
        return None
    return TokenSet.from_json(data)


def delete_tokens() -> None:
    keyring.delete_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)


class AuthError(Exception):
    pass


def request_device_code() -> dict:
    response = httpx.post(
        f"{AUTH_BASE}/oauth/device/code",
        json={
            "client_id": CLIENT_ID,
            "scope": SCOPES,
            "audience": API_AUDIENCE,
        },
    )
    response.raise_for_status()
    return response.json()


def poll_for_token(
    device_code: str, interval: int = 5, max_attempts: int = 60
) -> TokenSet:
    for _ in range(max_attempts):
        response = httpx.post(
            f"{AUTH_BASE}/oauth/token",
            json={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
                "client_id": CLIENT_ID,
            },
        )
        if response.status_code == 200:
            return TokenSet.from_auth_response(response.json())

        error = response.json().get("error")
        if error == "authorization_pending":
            time.sleep(interval)
        elif error == "slow_down":
            interval += 5
            time.sleep(interval)
        elif error == "expired_token":
            raise AuthError("Device code expired. Please restart authentication.")
        else:
            raise AuthError(f"Unexpected auth error: {error}")

    raise AuthError("Polling timed out waiting for authorization.")


def refresh_tokens(tokens: TokenSet) -> TokenSet:
    response = httpx.post(
        f"{AUTH_BASE}/oauth/token",
        json={
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "refresh_token": tokens.refresh_token,
        },
    )
    response.raise_for_status()
    return TokenSet.from_auth_response(response.json())


def get_valid_token(interactive: bool = True) -> TokenSet:
    tokens = load_tokens()

    if tokens is not None and not tokens.needs_refresh():
        return tokens

    if tokens is not None and tokens.refresh_token:
        try:
            new_tokens = refresh_tokens(tokens)
            save_tokens(new_tokens)
            return new_tokens
        except httpx.HTTPStatusError:
            pass  # refresh failed, fall through to re-auth

    if not interactive:
        raise AuthError(
            "Not authenticated. Run 'yoto auth' to log in."
        )

    return run_device_code_flow()


def run_device_code_flow() -> TokenSet:
    device = request_device_code()
    print(f"\nOpen this URL in your browser: {device['verification_uri']}")
    print(f"Enter code: {device['user_code']}\n")
    print(f"Or open directly: {device['verification_uri_complete']}\n")
    print("Waiting for authorization...")

    tokens = poll_for_token(
        device["device_code"],
        interval=device.get("interval", 5),
    )
    save_tokens(tokens)
    print("Authenticated successfully!")
    return tokens
