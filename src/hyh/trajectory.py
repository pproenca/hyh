import json
import os
import threading
from pathlib import Path
from typing import Any, Final


class TrajectoryLogger:
    __slots__ = ("_parent_str", "_path_str", "_write_lock", "trajectory_file")

    def __init__(self, trajectory_file: Path) -> None:
        self.trajectory_file: Final[Path] = Path(trajectory_file)

        self._path_str: Final[str] = str(self.trajectory_file)
        self._parent_str: Final[str] = str(self.trajectory_file.parent)
        self._write_lock: Final[threading.Lock] = threading.Lock()

    def log(self, event: dict[str, Any]) -> None:
        line = (json.dumps(event) + "\n").encode("utf-8")

        self.trajectory_file.parent.mkdir(parents=True, exist_ok=True)

        fd = os.open(self._path_str, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, line)
            os.fsync(fd)
        finally:
            os.close(fd)

    def tail(self, n: int, max_buffer_bytes: int = 1_048_576) -> list[dict[str, Any]]:
        if n <= 0:
            return []

        if not self.trajectory_file.exists():
            return []

        with self._write_lock:
            return self._tail_reverse_seek(n, max_buffer_bytes)

    def _tail_reverse_seek(self, n: int, max_buffer_bytes: int) -> list[dict[str, Any]]:
        block_size = 4096

        with self.trajectory_file.open("rb") as f:
            f.seek(0, 2)
            file_size = f.tell()

            if file_size == 0:
                return []

            chunks: list[bytes] = []
            position = file_size
            bytes_read = 0
            newline_count = 0

            while True:
                if bytes_read >= max_buffer_bytes:
                    break

                read_size = min(block_size, position)
                position -= read_size

                f.seek(position)
                chunk = f.read(read_size)
                chunks.append(chunk)
                bytes_read += read_size

                newline_count += chunk.count(b"\n")

                if newline_count > n or position == 0:
                    break

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
                    continue

            return events[-n:] if len(events) > n else events
