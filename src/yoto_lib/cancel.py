"""Cooperative cancellation for long-running subprocess work.

A worker thread running a cancellable task registers its live
``subprocess.Popen`` with a :class:`CancelToken`. Another thread can then call
:meth:`CancelToken.cancel` to set the flag and send ``SIGTERM`` to every
registered process, so a blocking ``communicate()`` returns promptly instead of
running to completion.
"""

from __future__ import annotations

import contextlib
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import subprocess


class CancelToken:
    """A cancellation signal that also terminates registered subprocesses."""

    __slots__ = ("_event", "_lock", "_procs")

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._procs: set[subprocess.Popen] = set()

    def cancelled(self) -> bool:
        """Whether cancellation has been requested."""
        return self._event.is_set()

    def register(self, proc: subprocess.Popen) -> None:
        """Track a live subprocess so :meth:`cancel` can terminate it.

        If the token is already cancelled, the process is terminated at once.
        """
        with self._lock:
            already_cancelled = self._event.is_set()
            if not already_cancelled:
                self._procs.add(proc)
        if already_cancelled:
            _terminate(proc)

    def unregister(self, proc: subprocess.Popen) -> None:
        """Stop tracking a subprocess that has finished on its own."""
        with self._lock:
            self._procs.discard(proc)

    def cancel(self) -> None:
        """Request cancellation and terminate every registered subprocess."""
        with self._lock:
            self._event.set()
            procs = list(self._procs)
            self._procs.clear()
        for proc in procs:
            _terminate(proc)


def _terminate(proc: subprocess.Popen) -> None:
    """Send SIGTERM to a process, ignoring one that has already exited."""
    with contextlib.suppress(OSError, ValueError):  # already reaped, or invalid handle
        proc.terminate()
