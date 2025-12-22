import fcntl
import hashlib
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final


def _get_default_registry_path() -> Path:
    env_path = os.getenv("HYH_REGISTRY_FILE")
    if env_path:
        return Path(env_path)
    return Path.home() / ".hyh" / "registry.json"


class ProjectRegistry:
    __slots__ = ("_lock_file", "registry_file")

    def __init__(self, registry_file: Path | None = None) -> None:
        resolved_path = Path(registry_file) if registry_file else _get_default_registry_path()
        self.registry_file: Final[Path] = resolved_path
        self._ensure_parent_dir()
        self._lock_file: Final[Path] = self.registry_file.with_suffix(".lock")

    def _ensure_parent_dir(self) -> None:
        self.registry_file.parent.mkdir(parents=True, exist_ok=True)

    def _with_lock[T](self, fn: Callable[[], T]) -> T:
        with self._lock_file.open("w") as lock_fd:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            try:
                return fn()
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)

    def _load_unlocked(self) -> dict[str, Any]:
        if not self.registry_file.exists():
            return {"projects": {}}
        try:
            data: dict[str, Any] = json.loads(self.registry_file.read_text())
            return data
        except (json.JSONDecodeError, OSError):
            return {"projects": {}}

    def _save_unlocked(self, data: dict[str, Any]) -> None:
        tmp = self.registry_file.with_suffix(".tmp")
        content = json.dumps(data, indent=2)
        with tmp.open("w") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        tmp.rename(self.registry_file)

    def register(self, worktree: Path) -> str:
        worktree = worktree.resolve()
        path_hash = hashlib.sha256(str(worktree).encode()).hexdigest()[:16]

        def _do_register() -> str:
            data = self._load_unlocked()
            data["projects"][path_hash] = {
                "path": str(worktree),
                "last_active": datetime.now(UTC).isoformat(),
            }
            self._save_unlocked(data)
            return path_hash

        return self._with_lock(_do_register)

    def list_projects(self) -> dict[str, dict[str, Any]]:
        return self._with_lock(lambda: self._load_unlocked().get("projects", {}))

    def get_hash_for_path(self, worktree: Path) -> str:
        worktree = worktree.resolve()
        return hashlib.sha256(str(worktree).encode()).hexdigest()[:16]
