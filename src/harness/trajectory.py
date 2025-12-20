"""Trajectory logger with efficient tail for Harness daemon.

This module provides a thread-safe trajectory logger that appends events to a JSONL
file and supports efficient O(1) tail operations using reverse-seek.
"""

import json
import os
import threading
from pathlib import Path
from typing import Any


class TrajectoryLogger:
    """Thread-safe trajectory logger with efficient tail.

    Features:
    - Thread-safe append operations
    - JSONL format for crash resilience
    - O(1) reverse-seek tail (reads from end of file in blocks)
    - Separate lock from StateManager (prevents lock convoy)
    """

    def __init__(self, trajectory_file: Path) -> None:
        """Initialize the trajectory logger.

        Args:
            trajectory_file: Path to the trajectory.jsonl file
        """
        self.trajectory_file = Path(trajectory_file)
        self._lock = threading.Lock()

    def log(self, event: dict[str, Any]) -> None:
        """Append an event to the trajectory log.

        Thread-safe via O_APPEND (POSIX atomic append guarantee).
        Uses fsync for crash durability (System Reliability Protocol).

        Note: O_APPEND atomicity is guaranteed for writes up to PIPE_BUF
        (typically 4KB-64KB depending on platform). Typical trajectory
        events are well under this limit (<1KB).

        Args:
            event: Dictionary to log as a JSON line
        """
        line = (json.dumps(event) + "\n").encode("utf-8")

        self.trajectory_file.parent.mkdir(parents=True, exist_ok=True)

        # O_APPEND: kernel guarantees atomic append (no interleaving)
        # This eliminates the need for self._lock during writes
        fd = os.open(self.trajectory_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, line)
            os.fsync(fd)
        finally:
            os.close(fd)

    def tail(self, n: int, max_buffer_bytes: int = 1_048_576) -> list[dict[str, Any]]:
        """Get the last N events from the trajectory log.

        Uses O(1) reverse-seek algorithm to efficiently read from the end of
        large files without loading the entire file into memory.

        Args:
            n: Number of events to retrieve (must be > 0 to get results)
            max_buffer_bytes: Maximum bytes to read before giving up (default 1MB).
                Prevents memory exhaustion on corrupt files with missing newlines.

        Returns:
            List of the last N events (or fewer if file has fewer than N events).
            Empty list if n <= 0.
        """
        if n <= 0:
            return []

        if not self.trajectory_file.exists():
            return []

        with self._lock:
            return self._tail_reverse_seek(n, max_buffer_bytes)

    def _tail_reverse_seek(self, n: int, max_buffer_bytes: int) -> list[dict[str, Any]]:
        """Efficiently read last N lines using reverse-seek.


        Complexity: O(k) where k = number of blocks read (NOT O(k²)).

        Args:
            n: Number of lines to retrieve
            max_buffer_bytes: Maximum bytes to read before stopping

        Returns:
            List of the last N events
        """
        block_size = 4096

        with self.trajectory_file.open("rb") as f:
            # Get file size
            f.seek(0, 2)
            file_size = f.tell()

            if file_size == 0:
                return []

            # Read from end in blocks until we have enough lines
            chunks: list[bytes] = []
            position = file_size
            bytes_read = 0
            newline_count = 0

            while True:
                # Check buffer limit to prevent memory exhaustion on corrupt files
                if bytes_read >= max_buffer_bytes:
                    break

                read_size = min(block_size, position)
                position -= read_size

                f.seek(position)
                chunk = f.read(read_size)
                chunks.append(chunk)  # O(1) append
                bytes_read += read_size

                # Count newlines in this chunk only (O(chunk_size), not O(total_bytes))
                newline_count += chunk.count(b"\n")

                # We need n+1 newlines because split on "line1\nline2\n"
                # gives ["line1", "line2", ""]
                if newline_count > n or position == 0:
                    break

            # Join ONCE after loop exits - O(total_bytes) but only once
            buffer = b"".join(reversed(chunks))
            lines = buffer.split(b"\n")

            events: list[dict[str, Any]] = []
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    event: dict[str, Any] = json.loads(line.decode("utf-8"))
                    events.append(event)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    # Skip corrupt lines (crash resilience)
                    continue

            return events[-n:] if len(events) > n else events
