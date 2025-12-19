"""
ACP Emitter - Queue-based non-blocking telemetry to Claude Code.

Design:
- emit() pushes to queue and returns immediately (< 1ms)
- Background thread drains queue and sends over socket
- If connect fails, log once to stderr and disable
- Daemon never stalls on socket operations
"""

import contextlib
import json
import queue
import socket
import sys
import threading
from typing import Any


class ACPEmitter:
    """Queue-based non-blocking telemetry emitter.

    Thread-safe: Uses threading.Event for the disabled flag to avoid
    data races in Python 3.13t (free-threaded).
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 9100) -> None:
        self._host = host
        self._port = port
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._disabled = threading.Event()  # Thread-safe flag
        self._warned = False
        self._thread = threading.Thread(target=self._worker, daemon=True)
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
            if entry is None:  # Shutdown signal
                break
            if self._disabled.is_set():
                continue

            # Lazy connect
            if sock is None:
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(2.0)
                    sock.connect((self._host, self._port))
                except OSError:
                    self._disabled.set()
                    sock = None
                    if not self._warned:
                        msg = f"ACP: Claude Code not available on port {self._port}"
                        print(msg, file=sys.stderr)
                        self._warned = True
                    continue

            # Send
            try:
                msg = json.dumps(entry) + "\n"
                sock.sendall(msg.encode())
            except OSError:
                self._disabled.set()
                with contextlib.suppress(OSError):
                    sock.close()
                sock = None

        # Cleanup on shutdown
        if sock:
            with contextlib.suppress(OSError):
                sock.close()

    def close(self) -> None:
        """Clean shutdown - signal worker thread to exit."""
        self._queue.put(None)
        self._thread.join(timeout=1.0)
