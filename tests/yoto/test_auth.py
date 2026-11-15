import base64
import hashlib
import threading
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from yoto_lib.yoto.auth import TokenSet


class TestTokenSet:
    def test_from_auth_response(self):
        response = {
            "access_token": "eyJhbGciOiJSUzI1NiJ9.eyJleHAiOjE3NDkwMDAwMDB9.sig",
            "refresh_token": "rt_abc123",
            "token_type": "Bearer",
            "expires_in": 86400,
            "scope": "user:content:manage offline_access",
        }
        tokens = TokenSet.from_auth_response(response)
        assert tokens.access_token == response["access_token"]
        assert tokens.refresh_token == "rt_abc123"
        assert tokens.token_type == "Bearer"
        assert isinstance(tokens.expires_at, float)

    def test_is_expired_when_past(self):
        tokens = TokenSet(
            access_token="tok",
            refresh_token="rt",
            token_type="Bearer",
            expires_at=time.time() - 100,
        )
        assert tokens.is_expired()

    def test_is_not_expired_when_future(self):
        tokens = TokenSet(
            access_token="tok",
            refresh_token="rt",
            token_type="Bearer",
            expires_at=time.time() + 3600,
        )
        assert not tokens.is_expired()

    def test_needs_refresh_30s_before_expiry(self):
        tokens = TokenSet(
            access_token="tok",
            refresh_token="rt",
            token_type="Bearer",
            expires_at=time.time() + 20,  # 20s left, within 30s threshold
        )
        assert tokens.needs_refresh()

    def test_no_refresh_needed_when_plenty_of_time(self):
        tokens = TokenSet(
            access_token="tok",
            refresh_token="rt",
            token_type="Bearer",
            expires_at=time.time() + 3600,
        )
        assert not tokens.needs_refresh()

    def test_serialize_deserialize_roundtrip(self):
        tokens = TokenSet(
            access_token="tok",
            refresh_token="rt",
            token_type="Bearer",
            expires_at=1749000000.0,
        )
        serialized = tokens.to_json()
        restored = TokenSet.from_json(serialized)
        assert restored.access_token == tokens.access_token
        assert restored.refresh_token == tokens.refresh_token
        assert restored.expires_at == tokens.expires_at


class TestTokenStorage:
    def test_save_and_load_tokens(self, mocker):
        stored = {}
        mocker.patch("keyring.set_password", side_effect=lambda svc, acct, val: stored.update({(svc, acct): val}))
        mocker.patch("keyring.get_password", side_effect=lambda svc, acct: stored.get((svc, acct)))

        from yoto_lib.yoto.auth import load_tokens, save_tokens

        tokens = TokenSet(
            access_token="tok",
            refresh_token="rt",
            token_type="Bearer",
            expires_at=1749000000.0,
        )
        save_tokens(tokens)
        loaded = load_tokens()
        assert loaded is not None
        assert loaded.access_token == "tok"
        assert loaded.refresh_token == "rt"

    def test_load_returns_none_when_no_tokens(self, mocker):
        mocker.patch("keyring.get_password", return_value=None)

        from yoto_lib.yoto.auth import load_tokens

        assert load_tokens() is None

    def test_delete_tokens(self, mocker):
        mock_delete = mocker.patch("keyring.delete_password")

        from yoto_lib.yoto.auth import delete_tokens

        delete_tokens()
        mock_delete.assert_called_once()


@pytest.fixture
def client_id(monkeypatch):
    monkeypatch.setenv("YOTO_CLIENT_ID", "test-client-id")
    return "test-client-id"


@pytest.fixture
def callback_server(monkeypatch):
    """A real loopback callback server on an ephemeral port."""
    from yoto_lib.yoto import auth

    monkeypatch.setattr(auth, "REDIRECT_PORT", 0)
    server = auth._start_callback_server()
    yield server
    server.server_close()


def _hit_callback(server, query: str, path: str = "/callback") -> threading.Thread:
    host, port = server.server_address[:2]

    def go():
        httpx.get(f"http://{host}:{port}{path}?{query}", timeout=5)

    thread = threading.Thread(target=go)
    thread.start()
    return thread


class TestClientId:
    def test_reads_env(self, client_id):
        from yoto_lib.yoto.auth import get_client_id

        assert get_client_id() == "test-client-id"

    def test_missing_env_explains_setup(self, monkeypatch):
        from yoto_lib.yoto.auth import AuthError, get_client_id

        monkeypatch.delenv("YOTO_CLIENT_ID", raising=False)
        with pytest.raises(AuthError, match=r"YOTO_CLIENT_ID is not set.*127\.0\.0\.1:8787/callback"):
            get_client_id()


class TestPkce:
    def test_challenge_is_s256_of_verifier(self):
        from yoto_lib.yoto.auth import generate_pkce_pair

        verifier, challenge = generate_pkce_pair()
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        assert challenge == expected
        assert 43 <= len(verifier) <= 128

    def test_authorize_url(self):
        from yoto_lib.yoto.auth import build_authorize_url

        url = build_authorize_url("cid", "chal", "st")
        parsed = urlparse(url)
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == "https://login.yotoplay.com/authorize"
        assert params == {
            "audience": "https://api.yotoplay.com",
            "scope": "user:content:manage offline_access",
            "response_type": "code",
            "client_id": "cid",
            "code_challenge": "chal",
            "code_challenge_method": "S256",
            "redirect_uri": "http://127.0.0.1:8787/callback",
            "state": "st",
        }


class TestCallback:
    def test_returns_code(self, callback_server):
        from yoto_lib.yoto.auth import wait_for_callback

        thread = _hit_callback(callback_server, "code=abc&state=st")
        assert wait_for_callback(callback_server, "st", timeout=5) == "abc"
        thread.join()

    def test_ignores_other_paths(self, callback_server):
        from yoto_lib.yoto.auth import wait_for_callback

        favicon = _hit_callback(callback_server, "", path="/favicon.ico")
        callback_server.handle_request()
        favicon.join()
        assert callback_server.params is None
        thread = _hit_callback(callback_server, "code=abc&state=st")
        assert wait_for_callback(callback_server, "st", timeout=5) == "abc"
        thread.join()

    def test_rejects_state_mismatch(self, callback_server):
        from yoto_lib.yoto.auth import AuthError, wait_for_callback

        thread = _hit_callback(callback_server, "code=abc&state=evil")
        with pytest.raises(AuthError, match="state"):
            wait_for_callback(callback_server, "st", timeout=5)
        thread.join()

    def test_surfaces_error_description(self, callback_server):
        from yoto_lib.yoto.auth import AuthError, wait_for_callback

        thread = _hit_callback(callback_server, "error=access_denied&error_description=Nope&state=st")
        with pytest.raises(AuthError, match="Nope"):
            wait_for_callback(callback_server, "st", timeout=5)
        thread.join()

    def test_times_out(self, callback_server):
        from yoto_lib.yoto.auth import AuthError, wait_for_callback

        callback_server.timeout = 0.05
        with pytest.raises(AuthError, match="Timed out"):
            wait_for_callback(callback_server, "st", timeout=0.1)

    def test_port_in_use_is_auth_error(self, callback_server, monkeypatch):
        from yoto_lib.yoto import auth

        monkeypatch.setattr(auth, "REDIRECT_PORT", callback_server.server_address[1])
        with pytest.raises(auth.AuthError, match="Could not listen"):
            auth._start_callback_server()


class TestBrowserLogin:
    def test_exchange_code_posts_form(self, mocker, client_id):
        from yoto_lib.yoto.auth import exchange_code

        mock_response = mocker.Mock(status_code=200)
        mock_response.json.return_value = {
            "access_token": "at",
            "refresh_token": "rt",
            "token_type": "Bearer",
            "expires_in": 86400,
        }
        mock_post = mocker.patch("httpx.post", return_value=mock_response)

        tokens = exchange_code("cid", "code123", "verifier123")

        assert tokens.access_token == "at"
        assert mock_post.call_args.kwargs["data"] == {
            "grant_type": "authorization_code",
            "client_id": "cid",
            "code_verifier": "verifier123",
            "code": "code123",
            "redirect_uri": "http://127.0.0.1:8787/callback",
        }

    def test_exchange_code_failure_is_auth_error(self, mocker):
        from yoto_lib.yoto.auth import AuthError, exchange_code

        mocker.patch("httpx.post", return_value=mocker.Mock(status_code=403, text="unauthorized_client"))
        with pytest.raises(AuthError, match="unauthorized_client"):
            exchange_code("cid", "code", "v")

    def test_run_browser_login_end_to_end(self, mocker, client_id, callback_server):
        from yoto_lib.yoto import auth

        mocker.patch.object(auth, "_start_callback_server", return_value=callback_server)
        saved = mocker.patch.object(auth, "save_tokens")
        exchanged = TokenSet(access_token="at", refresh_token="rt", token_type="Bearer", expires_at=time.time() + 60)
        exchange = mocker.patch.object(auth, "exchange_code", return_value=exchanged)
        threads = []

        def fake_open(url):
            state = parse_qs(urlparse(url).query)["state"][0]
            threads.append(_hit_callback(callback_server, f"code=xyz&state={state}"))
            return True

        mocker.patch("webbrowser.open", side_effect=fake_open)
        shown = []

        tokens = auth.run_browser_login(on_url=shown.append)

        for t in threads:
            t.join()
        assert tokens is exchanged
        assert len(shown) == 1
        assert shown[0].startswith("https://login.yotoplay.com/authorize?")
        cid, code, verifier = exchange.call_args.args
        assert (cid, code) == ("test-client-id", "xyz")
        assert parse_qs(urlparse(shown[0]).query)["code_challenge"][0] == (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        )
        saved.assert_called_once_with(exchanged)


class TestTokenRefresh:
    def test_refresh_tokens(self, mocker, client_id):
        from yoto_lib.yoto.auth import refresh_tokens

        mock_response = mocker.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "at_new",
            "refresh_token": "rt_new",
            "token_type": "Bearer",
            "expires_in": 86400,
        }
        mock_response.raise_for_status = mocker.Mock()
        mocker.patch("httpx.post", return_value=mock_response)

        old_tokens = TokenSet(
            access_token="at_old",
            refresh_token="rt_old",
            token_type="Bearer",
            expires_at=time.time() - 100,
        )
        new_tokens = refresh_tokens(old_tokens)

        assert new_tokens.access_token == "at_new"
        assert new_tokens.refresh_token == "rt_new"


class TestGetValidToken:
    def test_returns_valid_token_from_keychain(self, mocker):
        from yoto_lib.yoto.auth import get_valid_token

        valid = TokenSet(
            access_token="at_ok",
            refresh_token="rt_ok",
            token_type="Bearer",
            expires_at=time.time() + 3600,
        )
        mocker.patch("yoto_lib.yoto.auth.load_tokens", return_value=valid)

        token = get_valid_token(interactive=False)
        assert token.access_token == "at_ok"

    def test_refreshes_expiring_token(self, mocker):
        from yoto_lib.yoto.auth import get_valid_token

        expiring = TokenSet(
            access_token="at_old",
            refresh_token="rt_old",
            token_type="Bearer",
            expires_at=time.time() + 10,  # within 30s margin
        )
        refreshed = TokenSet(
            access_token="at_new",
            refresh_token="rt_new",
            token_type="Bearer",
            expires_at=time.time() + 3600,
        )
        mocker.patch("yoto_lib.yoto.auth.load_tokens", return_value=expiring)
        mocker.patch("yoto_lib.yoto.auth.refresh_tokens", return_value=refreshed)
        mocker.patch("yoto_lib.yoto.auth.save_tokens")

        token = get_valid_token(interactive=False)
        assert token.access_token == "at_new"

    def test_raises_when_no_token_and_not_interactive(self, mocker):
        from yoto_lib.yoto.auth import AuthError, get_valid_token

        mocker.patch("yoto_lib.yoto.auth.load_tokens", return_value=None)

        with pytest.raises(AuthError, match="Not authenticated"):
            get_valid_token(interactive=False)
