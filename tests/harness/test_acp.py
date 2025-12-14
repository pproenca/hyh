"""Tests for ACP fire-and-forget emitter."""

import contextlib
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


def test_acp_worker_send_error_disables():
    """Worker should disable emitter after send failure."""
    # Create server that accepts then closes immediately
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    server.listen(1)

    emitter = ACPEmitter(host="127.0.0.1", port=port)

    # Accept connection then close it to trigger send error
    def accept_and_close():
        conn, _ = server.accept()
        time.sleep(0.1)  # Let emitter connect
        conn.close()

    threading.Thread(target=accept_and_close, daemon=True).start()

    # Emit while connection is being established
    emitter.emit({"event": "test1"})
    time.sleep(0.3)

    # Emit after connection closed - should trigger error path
    emitter.emit({"event": "test2"})
    time.sleep(0.3)

    assert emitter._disabled is True
    emitter.close()
    server.close()


def test_acp_worker_cleanup_on_shutdown_with_connection():
    """Worker should clean up socket on shutdown when connection was established."""
    # Create server that stays open
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    server.listen(1)

    connections = []

    def accept_connections():
        try:
            conn, _ = server.accept()
            connections.append(conn)
            # Keep connection open but drain data
            while True:
                try:
                    data = conn.recv(1024)
                    if not data:
                        break
                except OSError:
                    break
        except OSError:
            pass

    accept_thread = threading.Thread(target=accept_connections, daemon=True)
    accept_thread.start()

    emitter = ACPEmitter(host="127.0.0.1", port=port)
    emitter.emit({"event": "test"})
    time.sleep(0.2)  # Let connection establish

    # Close should clean up
    emitter.close()

    # Close server resources
    for conn in connections:
        with contextlib.suppress(OSError):
            conn.close()
    server.close()
