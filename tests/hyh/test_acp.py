"""Tests for ACP fire-and-forget emitter."""

import contextlib
import json
import socket
import threading

import pytest

from hyh.acp import ACPEmitter
from tests.hyh.conftest import wait_until


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
    # Condition-based wait for message receipt (replaces time.sleep(0.1))
    wait_until(
        lambda: len(mock_server["received"]) >= 1,
        timeout=2.0,
        message="Message not received by mock server",
    )
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


def test_acp_worker_send_error_disables(monkeypatch):
    """Worker should disable emitter after send failure.

    Uses monkeypatch to inject a controlled socket that fails on send.
    This is the industry-standard approach for testing network error handling
    without relying on timing-dependent real network behavior.
    """
    send_count = 0
    connect_called = threading.Event()
    send_failed = threading.Event()

    class MockSocket:
        """Mock socket that fails on second send."""

        def __init__(self, *args, **kwargs):
            pass

        def settimeout(self, timeout):
            pass

        def connect(self, address):
            connect_called.set()

        def sendall(self, data):
            nonlocal send_count
            send_count += 1
            if send_count > 1:
                send_failed.set()
                raise OSError("Connection reset by peer")

        def close(self):
            pass

    # Patch socket.socket to return our mock
    monkeypatch.setattr(socket, "socket", MockSocket)

    emitter = ACPEmitter(host="127.0.0.1", port=9999)
    emitter.emit({"event": "test1"})

    # Wait for first message to be sent
    wait_until(
        lambda: send_count >= 1,
        timeout=5.0,
        message="First message should be sent",
    )

    # Send second message which will trigger the failure
    emitter.emit({"event": "test2"})

    # Wait for send failure to be detected
    wait_until(
        lambda: send_failed.is_set(),
        timeout=5.0,
        message="Send failure should occur",
    )

    # Wait for emitter to be disabled
    wait_until(
        lambda: emitter._disabled_event.is_set(),
        timeout=5.0,
        message="Emitter should be disabled after send failure",
    )

    assert emitter._disabled_event.is_set() is True
    emitter.close()


def test_acp_worker_cleanup_on_shutdown_with_connection():
    """Worker should clean up socket on shutdown when connection was established."""

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    server.listen(1)

    connections = []
    connection_accepted = threading.Event()

    def accept_connections():
        try:
            conn, _ = server.accept()
            connections.append(conn)
            connection_accepted.set()
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
    # Wait for connection to be established (replaces time.sleep(0.2))
    connection_accepted.wait(timeout=2.0)

    # Close should clean up
    emitter.close()

    for conn in connections:
        with contextlib.suppress(OSError):
            conn.close()
    server.close()


def test_acp_disabled_flag_is_thread_safe():
    """_disabled should be thread-safe (use threading.Event for Python 3.13t free-threading).

    Per CLAUDE.md Python 3.13t Concurrency Doctrine: threads are compute units.
    Simple boolean flags without synchronization are data races in free-threaded Python.
    """
    emitter = ACPEmitter(port=59999)  # Nothing listening

    # _disabled should be a threading.Event, not a boolean
    assert isinstance(emitter._disabled_event, threading.Event), (
        "_disabled must be threading.Event for thread-safe access in Python 3.13t"
    )

    # Verify Event semantics work correctly
    assert not emitter._disabled_event.is_set()  # Initially not disabled

    emitter.emit({"event": "test"})
    # Wait for worker to attempt connection and disable (replaces time.sleep(0.2))
    wait_until(
        lambda: emitter._disabled_event.is_set(),
        timeout=2.0,
        message="Emitter should be disabled after connection failure",
    )

    assert emitter._disabled_event.is_set()  # Should be disabled after connection failure
    emitter.close()
