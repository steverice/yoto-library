import time

import pytest

from yoto_lib.yoto.auth import TokenSet


class TestTokenSet:
    def test_from_auth_response(self):
        response = {
            "access_token": "eyJhbGciOiJSUzI1NiJ9.eyJleHAiOjE3NDkwMDAwMDB9.sig",
            "refresh_token": "rt_abc123",
            "token_type": "Bearer",
            "expires_in": 86400,
            "scope": "profile offline_access openid",
            "id_token": "id.token.here",
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


class TestDeviceCodeFlow:
    def test_request_device_code(self, mocker):
        from yoto_lib.yoto.auth import request_device_code

        mock_response = mocker.Mock()
        mock_response.json.return_value = {
            "device_code": "dev123",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://login.yotoplay.com/activate",
            "verification_uri_complete": "https://login.yotoplay.com/activate?user_code=ABCD-EFGH",
            "interval": 5,
            "expires_in": 300,
        }
        mock_response.raise_for_status = mocker.Mock()
        mock_post = mocker.patch("httpx.post", return_value=mock_response)

        result = request_device_code()

        assert result["device_code"] == "dev123"
        assert result["user_code"] == "ABCD-EFGH"
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs[1]["json"]["client_id"] == "kT1e1feuj42SxERTSWearGDWWmNeQ15x"

    def test_poll_for_token_success(self, mocker):
        from yoto_lib.yoto.auth import poll_for_token

        mock_response = mocker.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "at_ok",
            "refresh_token": "rt_ok",
            "token_type": "Bearer",
            "expires_in": 86400,
            "scope": "profile offline_access openid",
            "id_token": "id.tok",
        }
        mocker.patch("httpx.post", return_value=mock_response)

        tokens = poll_for_token("dev123", interval=0, max_attempts=1)

        assert tokens.access_token == "at_ok"
        assert tokens.refresh_token == "rt_ok"

    def test_poll_retries_on_authorization_pending(self, mocker):
        from yoto_lib.yoto.auth import poll_for_token

        pending_response = mocker.Mock()
        pending_response.status_code = 403
        pending_response.json.return_value = {"error": "authorization_pending"}

        success_response = mocker.Mock()
        success_response.status_code = 200
        success_response.json.return_value = {
            "access_token": "at_ok",
            "refresh_token": "rt_ok",
            "token_type": "Bearer",
            "expires_in": 86400,
            "scope": "profile offline_access openid",
            "id_token": "id.tok",
        }
        mocker.patch("httpx.post", side_effect=[pending_response, success_response])
        mocker.patch("time.sleep")

        tokens = poll_for_token("dev123", interval=0, max_attempts=5)
        assert tokens.access_token == "at_ok"

    def test_poll_slows_down_on_slow_down(self, mocker):
        from yoto_lib.yoto.auth import poll_for_token

        slow_response = mocker.Mock()
        slow_response.status_code = 403
        slow_response.json.return_value = {"error": "slow_down"}

        success_response = mocker.Mock()
        success_response.status_code = 200
        success_response.json.return_value = {
            "access_token": "at_ok",
            "refresh_token": "rt_ok",
            "token_type": "Bearer",
            "expires_in": 86400,
            "scope": "profile offline_access openid",
            "id_token": "id.tok",
        }
        mocker.patch("httpx.post", side_effect=[slow_response, success_response])
        mock_sleep = mocker.patch("time.sleep")

        poll_for_token("dev123", interval=5, max_attempts=5)
        # After slow_down, interval increases by 5
        mock_sleep.assert_any_call(10)

    def test_poll_raises_on_expired_token(self, mocker):
        from yoto_lib.yoto.auth import poll_for_token, AuthError

        expired_response = mocker.Mock()
        expired_response.status_code = 403
        expired_response.json.return_value = {"error": "expired_token"}
        mocker.patch("httpx.post", return_value=expired_response)

        with pytest.raises(AuthError, match="expired"):
            poll_for_token("dev123", interval=0, max_attempts=1)


class TestTokenRefresh:
    def test_refresh_tokens(self, mocker):
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
        from yoto_lib.yoto.auth import get_valid_token, AuthError

        mocker.patch("yoto_lib.yoto.auth.load_tokens", return_value=None)

        with pytest.raises(AuthError, match="Not authenticated"):
            get_valid_token(interactive=False)
