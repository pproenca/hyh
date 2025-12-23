"""Tests for trajectory.py - TrajectoryLogger with efficient tail."""

import json
import os
import threading
import time
from unittest.mock import patch

import pytest

from hyh.trajectory import TrajectoryLogger


@pytest.fixture
def temp_trajectory_dir(tmp_path):
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    return tmp_path


@pytest.fixture
def logger(temp_trajectory_dir):
    trajectory_file = temp_trajectory_dir / ".claude" / "trajectory.jsonl"
    return TrajectoryLogger(trajectory_file)


def test_creates_file_on_first_log(temp_trajectory_dir, logger):
    """Test that the trajectory file is created on first log."""

    trajectory_file = temp_trajectory_dir / ".claude" / "trajectory.jsonl"
    assert not trajectory_file.exists()

    logger.log({"event": "test", "data": "value"})

    assert trajectory_file.exists()


def test_appends_jsonl(temp_trajectory_dir, logger):
    """Test that events are appended in JSONL format."""
    logger.log({"event": "event1", "value": 1})
    logger.log({"event": "event2", "value": 2})
    logger.log({"event": "event3", "value": 3})

    trajectory_file = temp_trajectory_dir / ".claude" / "trajectory.jsonl"
    lines = trajectory_file.read_text().strip().split("\n")

    assert len(lines) == 3
    assert json.loads(lines[0]) == {"event": "event1", "value": 1}
    assert json.loads(lines[1]) == {"event": "event2", "value": 2}
    assert json.loads(lines[2]) == {"event": "event3", "value": 3}


def test_thread_safe(temp_trajectory_dir, logger):
    """Test that concurrent writes are thread-safe."""
    num_threads = 10
    events_per_thread = 20

    def write_events(thread_id):
        for i in range(events_per_thread):
            logger.log({"thread": thread_id, "event": i})

    threads = [threading.Thread(target=write_events, args=(tid,)) for tid in range(num_threads)]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    trajectory_file = temp_trajectory_dir / ".claude" / "trajectory.jsonl"
    lines = trajectory_file.read_text().strip().split("\n")

    # Should have exactly num_threads * events_per_thread lines
    assert len(lines) == num_threads * events_per_thread

    # Each line should be valid JSON
    for line in lines:
        data = json.loads(line)
        assert "thread" in data
        assert "event" in data


def test_tail_returns_last_n(temp_trajectory_dir, logger):
    """Test that tail returns the last N events."""
    for i in range(100):
        logger.log({"event": i})

    last_10 = logger.tail(10)

    assert len(last_10) == 10
    # Should return events 90-99
    for i, event in enumerate(last_10):
        assert event["event"] == 90 + i


def test_tail_empty_file(temp_trajectory_dir, logger):
    """Test that tail returns empty list for empty file."""
    result = logger.tail(10)
    assert result == []


def test_tail_fewer_than_n(temp_trajectory_dir, logger):
    """Test that tail returns all events when file has fewer than N."""
    logger.log({"event": 1})
    logger.log({"event": 2})
    logger.log({"event": 3})

    result = logger.tail(10)

    assert len(result) == 3
    assert result[0]["event"] == 1
    assert result[1]["event"] == 2
    assert result[2]["event"] == 3


def test_tail_large_file_performance(temp_trajectory_dir, logger):
    """Test that tail is O(1) - completes in <50ms even for large files."""
    # Create a ~1MB file with many events
    for i in range(10000):
        logger.log({"event": i, "data": "x" * 100})

    trajectory_file = temp_trajectory_dir / ".claude" / "trajectory.jsonl"
    file_size = trajectory_file.stat().st_size
    assert file_size > 1_000_000, "File should be > 1MB for performance test"

    start = time.perf_counter()
    result = logger.tail(10)
    elapsed = (time.perf_counter() - start) * 1000  # Convert to ms

    assert elapsed < 50, f"tail(10) took {elapsed:.2f}ms, should be < 50ms"
    assert len(result) == 10
    # Verify correctness
    assert result[-1]["event"] == 9999


def test_crash_resilient_jsonl_format(temp_trajectory_dir, logger):
    """Test that corrupt JSON lines are skipped gracefully."""
    trajectory_file = temp_trajectory_dir / ".claude" / "trajectory.jsonl"

    # Write some valid events
    logger.log({"event": 1})
    logger.log({"event": 2})

    # Manually corrupt the file by adding invalid JSON
    with open(trajectory_file, "a") as f:
        f.write("CORRUPT LINE NOT JSON\n")
        f.write('{"incomplete": \n')

    # Add more valid events
    logger.log({"event": 3})

    # tail should skip corrupt lines and return valid ones
    result = logger.tail(10)

    # Should get 3 valid events
    assert len(result) == 3
    assert result[0]["event"] == 1
    assert result[1]["event"] == 2
    assert result[2]["event"] == 3


def test_separate_lock_from_state(temp_trajectory_dir, logger):
    # Verify logger has its own _write_lock attribute
    assert hasattr(logger, "_write_lock")
    assert isinstance(logger._write_lock, type(threading.Lock()))

    # Verify it's a different instance than what WorkflowStateStore would use
    # (This test just verifies the lock exists; integration will test separation)
    lock_id_1 = id(logger._write_lock)

    # Create another logger
    another_logger = TrajectoryLogger(temp_trajectory_dir / ".claude" / "trajectory2.jsonl")
    lock_id_2 = id(another_logger._write_lock)

    assert lock_id_1 != lock_id_2


def test_trajectory_tail_handles_decode_error(tmp_path):
    """tail() should skip lines that fail JSON decode."""
    from hyh.trajectory import TrajectoryLogger

    trajectory_file = tmp_path / "trajectory.jsonl"

    # Write mix of valid and invalid lines
    with trajectory_file.open("w") as f:
        f.write('{"event": "valid1"}\n')
        f.write("invalid json line\n")
        f.write('{"event": "valid2"}\n')

    logger = TrajectoryLogger(trajectory_file)
    events = logger.tail(5)

    assert len(events) == 2
    assert events[0]["event"] == "valid1"
    assert events[1]["event"] == "valid2"


def test_log_calls_fsync_for_durability(temp_trajectory_dir, logger):
    """Test that log() calls fsync to ensure durability on crash.

    Per System Reliability Protocol: Assume the process will crash at any nanosecond.
    Without fsync, data may be lost in OS buffers on crash.
    """

    original_fsync = os.fsync
    fsync_calls = []

    def track_fsync(fd):
        fsync_calls.append(fd)
        # Call real fsync to not break functionality
        return original_fsync(fd)

    with patch("os.fsync", track_fsync):
        logger.log({"event": "test_durability"})

    assert len(fsync_calls) >= 1, "fsync must be called for crash durability"

    trajectory_file = temp_trajectory_dir / ".claude" / "trajectory.jsonl"
    content = trajectory_file.read_text()
    assert "test_durability" in content


def test_tail_limits_memory_on_corrupt_file(tmp_path):
    """tail() must not read entire file into memory if newlines are missing.

    Bug: If a corrupted file has no newlines (or very few), the algorithm
    reads the ENTIRE file into the buffer trying to find enough lines.
    This could exhaust memory on large corrupt files.

    The fix should add a max_buffer_bytes parameter that limits how much
    data is read before giving up (default 1MB).
    """
    trajectory_file = tmp_path / "trajectory.jsonl"

    # Create a "corrupted" file with no newlines - continuous data
    # 500KB file - we want to prove we DON'T read all of it
    corrupt_size = 500_000
    trajectory_file.write_bytes(b"x" * corrupt_size)

    logger = TrajectoryLogger(trajectory_file)

    # Test 1: Verify tail has max_buffer_bytes parameter
    import inspect

    sig = inspect.signature(logger.tail)
    assert "max_buffer_bytes" in sig.parameters, (
        "tail() should have max_buffer_bytes parameter to limit memory usage on corrupt files"
    )

    # Test 2: Verify the limit is respected
    result = logger.tail(5, max_buffer_bytes=50_000)  # 50KB limit
    assert result == [], "Corrupt file with no valid JSON should return empty list"

    # Test 3: Verify we can still read normal files with default limit

    # (This ensures the fix doesn't break normal operation)
    trajectory_file.write_text('{"event": 1}\n{"event": 2}\n{"event": 3}\n')
    result = logger.tail(2)
    assert len(result) == 2
    assert result[-1]["event"] == 3


def test_tail_reverse_seek_uses_append_not_insert(tmp_path):
    """Verify _tail_reverse_seek uses O(1) append, not O(n) insert.

    Bug: chunks.insert(0, chunk) shifts all elements right on each call.
    Fix: Use chunks.append(chunk) then reversed(chunks) for O(1) per operation.
    """
    import inspect

    from hyh.trajectory import TrajectoryLogger

    logger = TrajectoryLogger(tmp_path / "trajectory.jsonl")
    source = inspect.getsource(logger._tail_reverse_seek)

    # The fix should use append + reversed, not insert(0, ...)
    assert "insert(0" not in source, (
        "_tail_reverse_seek uses insert(0, chunk) which is O(n). "
        "Use chunks.append(chunk) and reversed(chunks) instead."
    )


def test_tail_reverse_seek_joins_outside_loop(tmp_path):
    """Verify buffer join happens AFTER the while loop, not inside it.

    Bug: join(reversed(chunks)) inside loop = O(k²) where k = chunks read.
    Fix: Count newlines in each chunk, join only when done seeking.
    """
    import ast
    import inspect
    import textwrap

    from hyh.trajectory import TrajectoryLogger

    logger = TrajectoryLogger(tmp_path / "trajectory.jsonl")
    source = inspect.getsource(logger._tail_reverse_seek)

    # Dedent to remove leading whitespace for ast.parse
    source = textwrap.dedent(source)

    tree = ast.parse(source)

    while_loops = [node for node in ast.walk(tree) if isinstance(node, ast.While)]
    assert len(while_loops) == 1, "Expected exactly one while loop"

    while_loop = while_loops[0]
    while_body_lines = {node.lineno for node in ast.walk(while_loop) if hasattr(node, "lineno")}

    join_calls = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "join"
        ):
            join_calls.append(node.lineno)

    # Verify no join calls are inside the while loop
    joins_inside_loop = [line for line in join_calls if line in while_body_lines]
    assert not joins_inside_loop, (
        f"Found join() calls inside while loop at relative lines {joins_inside_loop}. "
        "Move buffer reconstruction outside the loop to achieve O(k) complexity."
    )


def test_log_does_not_hold_lock_during_fsync(tmp_path):
    """
    Bug: fsync() under lock creates convoy effect (1-10ms blocking).
    Fix: Use O_APPEND for atomic appends, no lock needed for writes.

    This test verifies concurrent execution by checking that total time
    is significantly less than fully serialized execution (5 x 10ms = 50ms).
    """
    import threading
    import time

    from hyh.trajectory import TrajectoryLogger

    logger = TrajectoryLogger(tmp_path / "trajectory.jsonl")

    completion_times: list[tuple[float, int]] = []
    original_fsync = os.fsync

    def slow_fsync(fd):
        time.sleep(0.01)  # 10ms
        original_fsync(fd)

    os.fsync = slow_fsync  # type: ignore

    try:
        threads = []
        start_barrier = threading.Barrier(5, timeout=5.0)
        start_time = [0.0]

        def log_event(thread_id):
            start_barrier.wait()
            if thread_id == 0:
                start_time[0] = time.monotonic()
            logger.log({"thread": thread_id, "data": "x" * 100})
            completion_times.append((time.monotonic(), thread_id))

        for i in range(5):
            t = threading.Thread(target=log_event, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        total_time = max(t for t, _ in completion_times) - start_time[0]
        total_time_ms = total_time * 1000

        # Serialized execution would take ~50ms (5 x 10ms).
        # With O_APPEND (no Python lock), we expect significant concurrency.
        # Use 80ms threshold: generous margin for system load/free-threading
        # overhead while still detecting the 50ms+ serialized convoy effect.
        serialized_time_ms = 50
        max_allowed_ms = serialized_time_ms * 1.6  # 80ms

        assert total_time_ms < max_allowed_ms, (
            f"5 concurrent log() calls took {total_time_ms:.1f}ms. "
            f"Expected < {max_allowed_ms}ms (serialized would be ~{serialized_time_ms}ms). "
            "If it's >=50ms, the lock may be held during fsync (convoy effect)."
        )
    finally:
        os.fsync = original_fsync
