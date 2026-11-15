"""OAuth 2.0 PKCE browser login for the Yoto API, with Keychain token storage."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import time
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import keyring

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

AUTH_BASE = "https://login.yotoplay.com"
API_AUDIENCE = "https://api.yotoplay.com"
CLIENT_ID_ENV = "YOTO_CLIENT_ID"
DEVELOPER_DASHBOARD_URL = "https://dashboard.yoto.dev"
# `user:content:manage` covers MYO content and (via `user:icons:manage`) custom icons. Yoto's scope docs
# do not name a scope for audio or cover-image uploads; those are assumed to fall under it.
SCOPES = "user:content:manage offline_access"
REDIRECT_HOST = "127.0.0.1"
REDIRECT_PORT = 8787
REDIRECT_PATH = "/callback"
REDIRECT_URI = f"http://{REDIRECT_HOST}:{REDIRECT_PORT}{REDIRECT_PATH}"
CALLBACK_TIMEOUT_SECONDS = 300.0
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
        return json.dumps(
            {
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "token_type": self.token_type,
                "expires_at": self.expires_at,
            }
        )

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


def get_client_id() -> str:
    client_id = os.environ.get(CLIENT_ID_ENV, "").strip()
    if not client_id:
        raise AuthError(
            f"{CLIENT_ID_ENV} is not set. Register an app at {DEVELOPER_DASHBOARD_URL} "
            f"with redirect URI {REDIRECT_URI}, then set {CLIENT_ID_ENV} to its client ID (e.g. in .env)."
        )
    return client_id


def generate_pkce_pair() -> tuple[str, str]:
    """Return a `(code_verifier, code_challenge)` pair using the S256 method."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_authorize_url(client_id: str, code_challenge: str, state: str) -> str:
    params = {
        "audience": API_AUDIENCE,
        "scope": SCOPES,
        "response_type": "code",
        "client_id": client_id,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "redirect_uri": REDIRECT_URI,
        "state": state,
    }
    return f"{AUTH_BASE}/authorize?{urlencode(params)}"


class _CallbackHandler(BaseHTTPRequestHandler):
    server: _CallbackServer

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != REDIRECT_PATH:
            self.send_response(404)
            self.end_headers()
            return
        self.server.params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        ok = "code" in self.server.params
        body = "Signed in to Yoto. You can close this tab." if ok else "Yoto sign-in failed. Check the terminal."
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - signature set by BaseHTTPRequestHandler
        logger.debug("callback server: " + format, *args)


class _CallbackServer(HTTPServer):
    params: dict[str, str] | None = None


def _start_callback_server() -> _CallbackServer:
    try:
        server = _CallbackServer((REDIRECT_HOST, REDIRECT_PORT), _CallbackHandler)
    except OSError as exc:
        raise AuthError(f"Could not listen on {REDIRECT_HOST}:{REDIRECT_PORT} for the sign-in callback: {exc}") from exc
    server.timeout = 1.0
    return server


def wait_for_callback(server: _CallbackServer, state: str, timeout: float = CALLBACK_TIMEOUT_SECONDS) -> str:
    """Serve requests until the redirect arrives; return the authorization code."""
    deadline = time.monotonic() + timeout
    while server.params is None:
        if time.monotonic() >= deadline:
            raise AuthError("Timed out waiting for Yoto sign-in.")
        server.handle_request()

    params = server.params
    if "error" in params:
        detail = params.get("error_description") or params["error"]
        raise AuthError(f"Yoto sign-in failed: {detail}")
    if params.get("state") != state:
        raise AuthError("Sign-in callback had a mismatched state parameter; refusing it.")
    if "code" not in params:
        raise AuthError("Sign-in callback did not include an authorization code.")
    return params["code"]


def exchange_code(client_id: str, code: str, code_verifier: str) -> TokenSet:
    logger.debug("exchanging authorization code for tokens")
    response = httpx.post(
        f"{AUTH_BASE}/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code_verifier": code_verifier,
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
    )
    if response.status_code != 200:
        raise AuthError(f"Token exchange failed ({response.status_code}): {response.text}")
    return TokenSet.from_auth_response(response.json())


def run_browser_login(on_url: Callable[[str], None] | None = None) -> TokenSet:
    """Sign in through the browser with OAuth PKCE and a loopback redirect, then store the tokens.

    `on_url` receives the authorization URL so the caller can show it in case the
    browser does not open on its own.
    """
    logger.debug("starting PKCE browser login")
    client_id = get_client_id()
    verifier, challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(16)
    url = build_authorize_url(client_id, challenge, state)

    server = _start_callback_server()
    try:
        if on_url is not None:
            on_url(url)
        webbrowser.open(url)
        code = wait_for_callback(server, state)
    finally:
        server.server_close()

    tokens = exchange_code(client_id, code, verifier)
    save_tokens(tokens)
    return tokens


def refresh_tokens(tokens: TokenSet) -> TokenSet:
    logger.debug("refreshing access token")
    response = httpx.post(
        f"{AUTH_BASE}/oauth/token",
        json={
            "grant_type": "refresh_token",
            "client_id": get_client_id(),
            "refresh_token": tokens.refresh_token,
        },
    )
    response.raise_for_status()
    new_tokens = TokenSet.from_auth_response(response.json())
    logger.debug("token refresh successful")
    return new_tokens


def get_valid_token(interactive: bool = True) -> TokenSet:
    tokens = load_tokens()
    logger.debug(
        "get_valid_token: cached=%s needs_refresh=%s", tokens is not None, tokens.needs_refresh() if tokens else "n/a"
    )

    if tokens is not None and not tokens.needs_refresh():
        return tokens

    if tokens is not None and tokens.refresh_token:
        try:
            new_tokens = refresh_tokens(tokens)
            save_tokens(new_tokens)
            return new_tokens
        except httpx.HTTPStatusError:
            logger.debug("token refresh failed, will re-authenticate")
            # refresh failed, fall through to re-auth

    if not interactive:
        raise AuthError("Not authenticated. Run 'yoto auth' to log in.")

    return run_browser_login(on_url=lambda url: logger.warning("Sign in to Yoto in your browser: %s", url))
