"""
ACP Emitter - Queue-based non-blocking telemetry to Claude Code.
"""

import contextlib
import json
import queue
import socket
import sys
import threading
from typing import Any, Final


class ACPEmitter:
    """Queue-based non-blocking telemetry emitter.

    Thread-safe: Uses threading.Event for the disabled flag to avoid
    data races in Python 3.13t (free-threaded).
    """

    __slots__ = ("_disabled", "_host", "_port", "_queue", "_thread", "_warned")

    def __init__(self, host: str = "127.0.0.1", port: int = 9100) -> None:
        self._host: Final[str] = host
        self._port: Final[int] = port
        self._queue: Final[queue.Queue[dict[str, Any] | None]] = queue.Queue()
        self._disabled: Final[threading.Event] = threading.Event()
        # Thread-safe flag for warning dedup
        self._warned: Final[threading.Event] = threading.Event()
        self._thread: Final[threading.Thread] = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def emit(self, entry: dict[str, Any]) -> None:
        """Push to queue and return immediately. Strictly non-blocking."""
        if not self._disabled.is_set():
            self._queue.put_nowait(entry)

    def _worker(self) -> None:
        """Background thread: drain queue, send over socket."""
        sock: socket.socket | None = None
        while True:
            entry = self._queue.get()
            if entry is None:
                break
            if self._disabled.is_set():
                continue

            if sock is None:
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(2.0)
                    sock.connect((self._host, self._port))
                except OSError:
                    self._disabled.set()
                    # Close socket before discarding to prevent resource leak
                    # (sock may be None if socket() itself failed)
                    if sock is not None:
                        with contextlib.suppress(OSError):
                            sock.close()
                    sock = None
                    if not self._warned.is_set():
                        self._warned.set()
                        msg = f"ACP: Claude Code not available on port {self._port}"
                        print(msg, file=sys.stderr)
                    continue

            try:
                msg = json.dumps(entry) + "\n"
                sock.sendall(msg.encode())
            except OSError:
                self._disabled.set()
                with contextlib.suppress(OSError):
                    sock.close()
                sock = None

        if sock:
            with contextlib.suppress(OSError):
                sock.close()

    def close(self) -> None:
        self._queue.put(None)
        self._thread.join(timeout=1.0)
