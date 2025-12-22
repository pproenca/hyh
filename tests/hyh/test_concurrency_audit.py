"""
Red Team Security Audit: Concurrency and Race Condition Vulnerabilities.

These tests target race conditions, lock issues, and threading bugs.

Tests focus on:
- Socket message fragmentation
- Concurrent trajectory logging races
- Daemon startup race conditions
- Lock contention and starvation
- DateTime timezone confusion
"""

import json
import os
import socketserver
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest


class TestSocketMessageFragmentation:
    """HIGH: Socket message fragmentation (client.py:248-254).

    Vulnerability: Large JSON responses may be truncated when reading
    4096 byte chunks and stopping at first newline.
    """

    def test_large_response_handling(self) -> None:
        """Large responses exceeding buffer size should be fully received."""
        from hyh.client import send_rpc

        large_data = {"key": "x" * 10000}  # >4KB response

        class LargeResponseHandler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                self.rfile.readline()
                response = json.dumps({"status": "ok", "data": large_data}) + "\n"
                self.wfile.write(response.encode())
                self.wfile.flush()

        with tempfile.TemporaryDirectory() as tmpdir:
            sock_path = f"{tmpdir}/test.sock"

            server = socketserver.UnixStreamServer(sock_path, LargeResponseHandler)
            server_thread = threading.Thread(target=server.handle_request)
            server_thread.start()
            time.sleep(0.1)

            try:
                response = send_rpc(sock_path, {"command": "test"})

                # If fragmentation bug exists, data would be truncated
                assert response["status"] == "ok"
                assert len(response["data"]["key"]) == 10000, (
                    f"Response truncated: got {len(response['data']['key'])} chars, expected 10000"
                )
            finally:
                server.server_close()
                server_thread.join(timeout=1)

    def test_response_with_embedded_newlines(self) -> None:
        """Responses with newlines in JSON values should be handled."""
        from hyh.client import send_rpc

        data_with_newlines = {"message": "line1\nline2\nline3"}

        class NewlineResponseHandler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                self.rfile.readline()
                # JSON will escape the newlines as \n
                response = json.dumps({"status": "ok", "data": data_with_newlines}) + "\n"
                self.wfile.write(response.encode())
                self.wfile.flush()

        with tempfile.TemporaryDirectory() as tmpdir:
            sock_path = f"{tmpdir}/test.sock"

            server = socketserver.UnixStreamServer(sock_path, NewlineResponseHandler)
            server_thread = threading.Thread(target=server.handle_request)
            server_thread.start()
            time.sleep(0.1)

            try:
                response = send_rpc(sock_path, {"command": "test"})
                assert response["data"]["message"] == "line1\nline2\nline3"
            finally:
                server.server_close()
                server_thread.join(timeout=1)


class TestConcurrentTrajectoryRace:
    """HIGH: Concurrent trajectory logging race (trajectory.py).

    Vulnerability: Race between log() and tail() could cause partial reads.
    """

    def test_log_and_tail_concurrent(self) -> None:
        """Concurrent log and tail should not cause partial reads."""
        from hyh.trajectory import TrajectoryLogger

        with tempfile.TemporaryDirectory() as tmpdir:
            traj_file = Path(tmpdir) / "trajectory.jsonl"
            logger = TrajectoryLogger(traj_file)

            errors: list[tuple[str, object, Exception]] = []
            results: list[int] = []

            def writer() -> None:
                for i in range(100):
                    try:
                        logger.log({"event": i, "data": "x" * 100})
                    except Exception as e:
                        errors.append(("write", i, e))

            def reader() -> None:
                for _ in range(50):
                    try:
                        events = logger.tail(10)
                        # Each event should be valid JSON (not partial)
                        for e in events:
                            if "event" not in e:
                                errors.append(("read", "missing key", ValueError(str(e))))
                        results.append(len(events))
                    except json.JSONDecodeError as e:
                        errors.append(("read", "decode", e))
                    except Exception as e:
                        errors.append(("read", "other", e))
                    time.sleep(0.001)

            threads = [
                threading.Thread(target=writer),
                threading.Thread(target=reader),
            ]

            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert len(errors) == 0, f"Race condition errors: {errors}"

    def test_concurrent_log_atomicity(self) -> None:
        """Concurrent writes should not interleave JSONL lines."""
        from hyh.trajectory import TrajectoryLogger

        with tempfile.TemporaryDirectory() as tmpdir:
            traj_file = Path(tmpdir) / "trajectory.jsonl"
            logger = TrajectoryLogger(traj_file)

            num_threads = 10
            events_per_thread = 50

            def writer(thread_id: int) -> None:
                for i in range(events_per_thread):
                    # Use a marker to verify no interleaving
                    logger.log({"thread": thread_id, "event": i, "marker": f"T{thread_id}E{i}"})

            threads = [threading.Thread(target=writer, args=(i,)) for i in range(num_threads)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            lines = traj_file.read_text().strip().split("\n")
            assert len(lines) == num_threads * events_per_thread

            for i, line in enumerate(lines):
                try:
                    data = json.loads(line)

                    marker = data["marker"]
                    assert marker.startswith("T") and "E" in marker, (
                        f"Line {i} has corrupted marker: {marker}"
                    )
                except json.JSONDecodeError:
                    pytest.fail(f"Line {i} is not valid JSON (interleaved?): {line[:100]}...")


class TestDatetimeTimezoneConfusion:
    """HIGH: Timezone datetime confusion (state.py:102-104).

    Vulnerability: Mixing naive and timezone-aware datetimes causes TypeError.
    """

    def test_is_timed_out_with_naive_datetime(self) -> None:
        """is_timed_out() must handle naive datetimes."""
        from hyh.state import Task, TaskStatus

        # Scenario: Naive datetime from datetime.now()
        task = Task(
            id="1",
            description="Test",
            status=TaskStatus.RUNNING,
            dependencies=[],
            started_at=datetime.now() - timedelta(hours=1),  # naive
            timeout_seconds=60,
        )

        # Should not raise TypeError
        result = task.is_timed_out()
        assert result is True  # 1 hour > 60 seconds

    def test_is_timed_out_with_aware_datetime(self) -> None:
        """is_timed_out() must handle aware datetimes."""
        from hyh.state import Task, TaskStatus

        # Scenario: Aware datetime (e.g., from JSON deserialization)
        task = Task(
            id="2",
            description="Test",
            status=TaskStatus.RUNNING,
            dependencies=[],
            started_at=datetime.now(UTC) - timedelta(hours=1),  # aware
            timeout_seconds=60,
        )

        # Should not raise TypeError
        result = task.is_timed_out()
        assert result is True

    def test_datetime_round_trip_through_json(self) -> None:
        """Datetimes should survive JSON serialization round-trip."""
        from hyh.state import Task, TaskStatus, WorkflowState, WorkflowStateStore

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = WorkflowStateStore(Path(tmpdir))

            task = Task(
                id="task-1",
                description="Test",
                status=TaskStatus.RUNNING,
                dependencies=[],
                started_at=datetime.now(UTC),
                timeout_seconds=600,
            )
            state = WorkflowState(tasks={"task-1": task})
            manager.save(state)

            loaded = manager.load()
            assert loaded is not None
            assert loaded.tasks["task-1"].started_at is not None

            result = loaded.tasks["task-1"].is_timed_out()
            assert isinstance(result, bool)


class TestWorkerIdRaceCondition:
    """MEDIUM: Worker ID TOCTOU race (client.py:25-69).

    Vulnerability: Race condition between checking if worker ID file exists
    and reading/writing it.
    """

    def test_concurrent_worker_id_generation(self) -> None:
        """Concurrent worker ID generation should not cause conflicts."""
        from hyh.client import get_worker_id

        with tempfile.TemporaryDirectory() as tmpdir:
            worker_id_file = Path(tmpdir) / "worker.id"

            worker_ids: list[str] = []
            errors: list[Exception] = []

            def get_id() -> None:
                try:
                    wid = get_worker_id()
                    worker_ids.append(wid)
                except Exception as e:
                    errors.append(e)

            # Spawn many threads to trigger race
            # Use patch.dict globally for all threads as it modifies global os.environ
            with patch.dict(os.environ, {"HYH_WORKER_ID_FILE": str(worker_id_file)}):
                threads = [threading.Thread(target=get_id) for _ in range(20)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()

            assert len(errors) == 0, f"Errors during concurrent get_worker_id: {errors}"

            if worker_id_file.exists():
                content = worker_id_file.read_text().strip()
                assert content.startswith("worker-")
                assert len(content) == 19  # "worker-" + 12 hex chars


class TestLockContention:
    """MEDIUM: Lock contention/starvation (git.py).

    Tests for lock behavior under contention.
    """

    def test_global_exec_lock_contention(self) -> None:
        """Document lock behavior under contention."""
        from hyh.runtime import GLOBAL_EXEC_LOCK

        lock_acquisition_times: list[float] = []

        def timed_lock_acquire() -> None:
            start = time.monotonic()
            with GLOBAL_EXEC_LOCK:
                elapsed = time.monotonic() - start
                lock_acquisition_times.append(elapsed)
                time.sleep(0.01)  # Hold lock briefly

        threads = [threading.Thread(target=timed_lock_acquire) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(lock_acquisition_times) == 5

        # Later threads should wait longer
        # This documents the expected serialization behavior


class TestDaemonStartupRace:
    """HIGH: Daemon startup race condition (client.py:183-218).

    Vulnerability: Daemon may die between process checks.
    """

    def test_daemon_spawn_crash_detection(self) -> None:
        """Daemon crash during startup should be detected."""
        # This is a documentation test - actual implementation requires
        # complex process mocking

        # The vulnerability:
        # 1. Client forks daemon process
        # 2. Client checks if process exists (os.kill(pid, 0))
        # 3. Daemon crashes
        # 4. Client checks if socket exists
        # 5. Socket doesn't exist, but client thinks daemon is running

        # The fix would be to check both in a single atomic operation
        # or to use a more robust health check
        pass


class TestStateManagerLocking:
    """Tests for StateManager lock behavior."""

    def test_concurrent_claim_and_complete(self) -> None:
        """Concurrent claims and completes should not corrupt state."""
        from hyh.state import Task, TaskStatus, WorkflowState, WorkflowStateStore

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = WorkflowStateStore(Path(tmpdir))

            tasks = {}
            for i in range(10):
                tasks[f"task-{i}"] = Task(
                    id=f"task-{i}",
                    description=f"Task {i}",
                    status=TaskStatus.PENDING,
                    dependencies=[],
                )
            state = WorkflowState(tasks=tasks)
            manager.save(state)

            claimed_tasks: list[str] = []
            completed_tasks: list[str] = []
            claim_lock = threading.Lock()
            errors: list[Exception] = []

            def claim_and_complete(worker_id: str) -> None:
                try:
                    result = manager.claim_task(worker_id)
                    if result.task:
                        with claim_lock:
                            claimed_tasks.append(result.task.id)
                        task_id = result.task.id
                        time.sleep(0.01)  # Simulate work
                        manager.complete_task(task_id, worker_id)
                        with claim_lock:
                            completed_tasks.append(task_id)
                except Exception as e:
                    errors.append(e)

            threads = [
                threading.Thread(target=claim_and_complete, args=(f"worker-{i}",))
                for i in range(10)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert len(errors) == 0, f"Errors during concurrent operations: {errors}"

            # Verify state consistency - completed count should match what we tracked
            final_state = manager.load()
            assert final_state is not None
            completed_count = sum(
                1 for t in final_state.tasks.values() if t.status == TaskStatus.COMPLETED
            )
            # All claimed tasks should be completed
            assert completed_count == len(completed_tasks), (
                f"State inconsistency: {completed_count} completed in state vs "
                f"{len(completed_tasks)} we completed"
            )
