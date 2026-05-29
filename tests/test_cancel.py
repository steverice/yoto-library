"""Tests for the CancelToken cooperative-cancellation primitive."""

from __future__ import annotations

import subprocess
import sys
import time


def _spawn_sleeper(seconds: int = 30) -> subprocess.Popen:
    """Start a long-lived child process we can later try to terminate."""
    return subprocess.Popen([sys.executable, "-c", f"import time; time.sleep({seconds})"])


def _wait_exit(proc: subprocess.Popen, timeout: float = 3.0) -> int | None:
    """Poll until the process exits or the timeout elapses; return its returncode."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code = proc.poll()
        if code is not None:
            return code
        time.sleep(0.02)
    return proc.poll()


class TestCancelToken:
    def test_new_token_is_not_cancelled(self):
        from yoto_lib.cancel import CancelToken

        assert CancelToken().cancelled() is False

    def test_cancel_sets_cancelled_state(self):
        from yoto_lib.cancel import CancelToken

        token = CancelToken()
        token.cancel()
        assert token.cancelled() is True

    def test_cancel_terminates_registered_subprocess(self):
        from yoto_lib.cancel import CancelToken

        token = CancelToken()
        proc = _spawn_sleeper()
        try:
            token.register(proc)
            assert proc.poll() is None  # still running before cancel
            token.cancel()
            assert _wait_exit(proc) is not None  # terminated promptly
        finally:
            proc.kill()

    def test_register_after_cancel_terminates_immediately(self):
        from yoto_lib.cancel import CancelToken

        token = CancelToken()
        token.cancel()
        proc = _spawn_sleeper()
        try:
            token.register(proc)
            assert _wait_exit(proc) is not None
        finally:
            proc.kill()

    def test_unregister_stops_tracking(self):
        from yoto_lib.cancel import CancelToken

        token = CancelToken()
        proc = _spawn_sleeper(seconds=1)
        try:
            token.register(proc)
            token.unregister(proc)
            token.cancel()
            # Not terminated by the token; it finishes on its own ~1s later.
            assert proc.poll() is None
        finally:
            proc.kill()
