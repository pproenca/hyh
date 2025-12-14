"""Tests for ACP fire-and-forget emitter."""

import json
import socket
import threading
import time

import pytest

from harness.acp import ACPEmitter


@pytest.fixture
def mock_server():
    """Ephemeral TCP server to receive ACP messages."""
    received: list[str] = []
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def accept():
        try:
            conn, _ = server.accept()
            conn.settimeout(1.0)
            while True:
                data = conn.recv(4096)
                if not data:
                    break
                received.append(data.decode())
            conn.close()
        except (OSError, TimeoutError):
            pass

    t = threading.Thread(target=accept, daemon=True)
    t.start()
    yield {"port": port, "received": received}
    server.close()


def test_emitter_sends_json(mock_server):
    """ACPEmitter should send JSON lines."""
    emitter = ACPEmitter(port=mock_server["port"])
    emitter.emit({"event": "test", "data": 123})
    time.sleep(0.1)
    emitter.close()

    assert len(mock_server["received"]) == 1
    msg = json.loads(mock_server["received"][0].strip())
    assert msg["event"] == "test"


def test_emitter_graceful_on_no_server():
    """ACPEmitter should not crash if server unavailable."""
    emitter = ACPEmitter(port=59999)  # Nothing listening
    # Should not raise
    emitter.emit({"event": "test"})
    emitter.close()


def test_emitter_logs_once_on_failure(capsys):
    """ACPEmitter should log connection failure once, not spam."""
    emitter = ACPEmitter(port=59999)
    emitter.emit({"event": "1"})
    emitter.emit({"event": "2"})
    emitter.emit({"event": "3"})
    emitter.close()

    captured = capsys.readouterr()
    # Should only see one warning, not three
    assert captured.err.count("ACP") <= 1
