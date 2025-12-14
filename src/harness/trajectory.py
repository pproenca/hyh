"""Trajectory logger with efficient tail for Harness daemon.

This module provides a thread-safe trajectory logger that appends events to a JSONL
file and supports efficient O(1) tail operations using reverse-seek.
"""

import json
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

        Thread-safe operation that appends a JSON line to the file.

        Args:
            event: Dictionary to log as a JSON line
        """
        with self._lock:
            # Create parent directory if it doesn't exist
            self.trajectory_file.parent.mkdir(parents=True, exist_ok=True)

            # Append the event as a JSON line
            with self.trajectory_file.open("a") as f:
                f.write(json.dumps(event) + "\n")

    def tail(self, n: int) -> list[dict[str, Any]]:
        """Get the last N events from the trajectory log.

        Uses O(1) reverse-seek algorithm to efficiently read from the end of
        large files without loading the entire file into memory.

        Args:
            n: Number of events to retrieve

        Returns:
            List of the last N events (or fewer if file has fewer than N events)
        """
        if not self.trajectory_file.exists():
            return []

        with self._lock:
            return self._tail_reverse_seek(n)

    def _tail_reverse_seek(self, n: int) -> list[dict[str, Any]]:
        """Efficiently read last N lines using reverse-seek.

        Reads the file from the end in 4KB blocks until we have enough lines.

        Args:
            n: Number of lines to retrieve

        Returns:
            List of the last N events
        """
        block_size = 4096  # 4KB blocks

        with self.trajectory_file.open("rb") as f:
            # Get file size
            f.seek(0, 2)  # Seek to end
            file_size = f.tell()

            if file_size == 0:
                return []

            # Read from end in blocks until we have enough lines
            buffer = b""
            position = file_size

            while True:
                # Determine how much to read
                read_size = min(block_size, position)
                position -= read_size

                # Seek to position and read
                f.seek(position)
                chunk = f.read(read_size)
                buffer = chunk + buffer

                # Try to split into lines
                lines = buffer.split(b"\n")

                # If we have enough lines (accounting for potential empty line at end)
                # We need n+1 because split on "line1\nline2\n" gives ["line1", "line2", ""]
                if len(lines) > n or position == 0:
                    break

            # Parse JSON lines, skipping corrupt ones
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

            # Return last n events
            return events[-n:] if len(events) > n else events
