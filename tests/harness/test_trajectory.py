"""Tests for trajectory.py - TrajectoryLogger with efficient tail."""

import json
import threading
import time

import pytest

from harness.trajectory import TrajectoryLogger


@pytest.fixture
def temp_trajectory_dir(tmp_path):
    """Create a temporary .claude directory."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    return tmp_path


@pytest.fixture
def logger(temp_trajectory_dir):
    """Create a TrajectoryLogger instance."""
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

    # Measure tail performance
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
    """Test that TrajectoryLogger has its own lock, separate from StateManager."""
    # Verify logger has its own _lock attribute
    assert hasattr(logger, "_lock")
    assert isinstance(logger._lock, threading.Lock)

    # Verify it's a different instance than what StateManager would use
    # (This test just verifies the lock exists; integration will test separation)
    lock_id_1 = id(logger._lock)

    # Create another logger
    another_logger = TrajectoryLogger(temp_trajectory_dir / ".claude" / "trajectory2.jsonl")
    lock_id_2 = id(another_logger._lock)

    # Each logger should have its own lock instance
    assert lock_id_1 != lock_id_2
