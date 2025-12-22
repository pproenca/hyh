"""
ACP Telemetry Robustness Tests.

Tests for ACPEmitter failure handling - telemetry failures should never crash daemon.
"""

import contextlib
import socket
import threading

import pytest

from hyh.acp import ACPEmitter
from tests.hyh.conftest import wait_until


class TestNonSerializableEvents:
    """Test handling of non-serializable event data.

    KNOWN ISSUE: If non-serializable objects are passed to emit(),
    the worker thread may crash without proper exception handling.
    """

    def test_non_serializable_object_in_event(self) -> None:
        """Non-JSON-serializable objects should not crash emitter."""
        emitter = ACPEmitter("localhost", 9999)

        try:
            # Object that can't be JSON serialized
            class NonSerializable:
                pass

            event = {"type": "test", "data": NonSerializable()}

            # This should not crash
            emitter.emit(event)

            # Wait for emitter to become disabled after processing error (replaces time.sleep(0.1))
            wait_until(
                lambda: emitter._disabled_event.is_set(),
                timeout=2.0,
                message="Emitter should process event without crashing",
            )

            # Emitter should still be functional (but may be disabled due to connection failure)
            emitter.emit({"type": "test", "data": "valid"})
        finally:
            emitter.close()

    def test_circular_reference_in_event(self) -> None:
        """Circular references in event data should be handled."""
        emitter = ACPEmitter("localhost", 9999)

        try:
            # Create circular reference
            event: dict = {"type": "test"}
            event["self"] = event

            # Should not crash
            emitter.emit(event)
            # Wait for emitter to process (replaces time.sleep(0.1))
            wait_until(
                lambda: emitter._disabled_event.is_set(),
                timeout=2.0,
                message="Emitter should handle circular reference",
            )
        finally:
            emitter.close()


class TestSocketFailures:
    """Test socket failure handling."""

    def test_connection_refused_handled(self) -> None:
        """Connection refused should disable emitter gracefully."""
        # Use a port that's definitely not listening
        emitter = ACPEmitter("localhost", 59999)

        try:
            # Emit should not raise even if connection fails
            emitter.emit({"type": "test"})
            # Wait for emitter to be disabled after connection failure (replaces time.sleep(0.2))
            wait_until(
                lambda: emitter._disabled_event.is_set(),
                timeout=2.0,
                message="Emitter should be disabled after connection failure",
            )

            # Emitter should be disabled after failure
            assert emitter._disabled_event.is_set()
        finally:
            emitter.close()

    def test_socket_timeout_handled(self) -> None:
        """Socket timeout should be handled gracefully."""
        # This is difficult to test without a mock server
        # that delays responses
        emitter = ACPEmitter("localhost", 59999)

        try:
            for _ in range(5):
                emitter.emit({"type": "test"})

            # Wait for emitter to be disabled (replaces time.sleep(0.1))
            wait_until(
                lambda: emitter._disabled_event.is_set(),
                timeout=2.0,
                message="Emitter should be disabled after connection failure",
            )
        finally:
            emitter.close()


class TestWorkerThreadRobustness:
    """Test worker thread robustness."""

    def test_close_with_pending_events(self) -> None:
        """Close with pending events should complete gracefully."""
        emitter = ACPEmitter("localhost", 59999)

        try:
            # Queue many events
            for i in range(100):
                emitter.emit({"type": "test", "index": i})

            # Close immediately - should not hang
            emitter.close()
        except Exception as e:
            pytest.fail(f"Close with pending events failed: {e}")

    def test_double_close(self) -> None:
        """Calling close() twice should be safe."""
        emitter = ACPEmitter("localhost", 59999)

        emitter.close()
        emitter.close()  # Should not raise

    def test_emit_after_close(self) -> None:
        """Emit after close should be handled gracefully."""
        emitter = ACPEmitter("localhost", 59999)
        emitter.close()

        # Should not crash (might be no-op or raise)
        with contextlib.suppress(Exception):
            emitter.emit({"type": "test"})


class TestQueueBehavior:
    """Test queue behavior under load."""

    def test_rapid_emit(self) -> None:
        """Rapid emit calls should not cause issues."""
        emitter = ACPEmitter("localhost", 59999)

        try:
            for _ in range(1000):
                emitter.emit({"type": "test", "data": "x" * 100})

            # Should complete without hanging
        finally:
            emitter.close()

    def test_emit_from_multiple_threads(self) -> None:
        """Concurrent emit from multiple threads should be safe."""
        emitter = ACPEmitter("localhost", 59999)

        errors: list[str] = []
        errors_lock = threading.Lock()

        def emitter_thread(thread_id: int) -> None:
            try:
                for i in range(100):
                    emitter.emit({"thread": thread_id, "event": i})
            except Exception as e:
                with errors_lock:
                    errors.append(f"Thread {thread_id}: {e}")

        try:
            threads = [threading.Thread(target=emitter_thread, args=(i,)) for i in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert len(errors) == 0, f"Errors: {errors}"
        finally:
            emitter.close()


class TestACPWithMockServer:
    """Test ACP with a mock server to verify actual behavior."""

    def test_successful_emit_reaches_server(self) -> None:
        """Events should reach the server when connection succeeds."""
        received: list[bytes] = []

        # Create mock server
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("localhost", 0))
        port = server.getsockname()[1]
        server.listen(1)
        server.settimeout(2.0)

        def accept_connection() -> None:
            try:
                conn, _ = server.accept()
                conn.settimeout(1.0)
                while True:
                    data = conn.recv(4096)
                    if not data:
                        break
                    received.append(data)
            except TimeoutError:
                pass
            finally:
                server.close()

        accept_thread = threading.Thread(target=accept_connection)
        accept_thread.start()

        try:
            emitter = ACPEmitter("localhost", port)
            emitter.emit({"type": "test", "value": 123})
            # Wait for message to be received (replaces time.sleep(0.2))
            wait_until(
                lambda: len(received) > 0,
                timeout=2.0,
                message="Event should be received by server",
            )
            emitter.close()

            accept_thread.join(timeout=2.0)

            # Verify event was received
            assert len(received) > 0, "No data received by server"
            combined = b"".join(received)
            assert b"test" in combined
        finally:
            if accept_thread.is_alive():
                server.close()
                accept_thread.join(timeout=1.0)
