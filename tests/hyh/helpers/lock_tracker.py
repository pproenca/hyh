"""
Lock Hierarchy Tracking for Deadlock Prevention.

<approach>
The documented lock hierarchy from CLAUDE.md:
1. WorkflowStateStore._state_lock (highest priority)
2. TrajectoryLogger._write_lock
3. GLOBAL_EXEC_LOCK (lowest, only for git operations)

Locks must ALWAYS be acquired in this order. Never acquire a higher-priority
lock while holding a lower-priority one.

This module provides instrumentation to:
1. Track which locks each thread currently holds
2. Detect hierarchy violations at runtime
3. Raise exceptions on violation for test failure
</approach>
"""

import threading
from collections.abc import Generator
from contextlib import contextmanager
from typing import ClassVar

from msgspec import Struct


class LockHierarchyError(Exception):
    """Raised when lock hierarchy is violated."""

    def __init__(self, message: str, held_locks: list[str], attempted_lock: str) -> None:
        super().__init__(message)
        self.held_locks = held_locks
        self.attempted_lock = attempted_lock


class LockInfo(Struct, frozen=True, forbid_unknown_fields=True):
    """Information about a tracked lock."""

    name: str
    priority: int  # Lower = higher priority (acquire first)
    lock: threading.Lock


class LockTracker:
    """Track lock acquisitions and detect hierarchy violations.

    Usage:
        tracker = LockTracker()
        tracker.register("WorkflowStateStore._state_lock", 1, state_store._state_lock)
        tracker.register("GLOBAL_EXEC_LOCK", 3, GLOBAL_EXEC_LOCK)

        # In code:
        with tracker.track("WorkflowStateStore._state_lock"):
            # ... critical section
            with tracker.track("GLOBAL_EXEC_LOCK"):  # OK: 1 < 3
                pass

        with tracker.track("GLOBAL_EXEC_LOCK"):
            with tracker.track("WorkflowStateStore._state_lock"):  # VIOLATION: 3 > 1
                pass  # Raises LockHierarchyError
    """

    # Standard hierarchy from CLAUDE.md
    STANDARD_HIERARCHY: ClassVar[dict[str, int]] = {
        "WorkflowStateStore._state_lock": 1,
        "TrajectoryLogger._write_lock": 2,
        "GLOBAL_EXEC_LOCK": 3,
    }

    def __init__(self) -> None:
        self._local = threading.local()
        self._locks: dict[str, LockInfo] = {}
        self._violations: list[LockHierarchyError] = []
        self._violations_lock = threading.Lock()

    def register(self, name: str, priority: int, lock: threading.Lock) -> None:
        """Register a lock to be tracked."""
        self._locks[name] = LockInfo(name=name, priority=priority, lock=lock)

    def register_standard_hierarchy(
        self,
        state_lock: threading.Lock,
        trajectory_lock: threading.Lock,
        exec_lock: threading.Lock,
    ) -> None:
        """Register all locks with the standard hierarchy."""
        self.register("WorkflowStateStore._state_lock", 1, state_lock)
        self.register("TrajectoryLogger._write_lock", 2, trajectory_lock)
        self.register("GLOBAL_EXEC_LOCK", 3, exec_lock)

    def _get_held_stack(self) -> list[str]:
        """Get the stack of held locks for current thread."""
        if not hasattr(self._local, "stack"):
            self._local.stack = []
        return self._local.stack

    @contextmanager
    def track(self, lock_name: str) -> Generator[None]:
        """Context manager to track lock acquisition."""
        stack = self._get_held_stack()
        lock_info = self._locks.get(lock_name)

        if lock_info is None:
            # Unknown lock, just track the name
            priority = self.STANDARD_HIERARCHY.get(lock_name, 999)
        else:
            priority = lock_info.priority

        # Check for hierarchy violations
        for held_name in stack:
            held_info = self._locks.get(held_name)
            held_priority = (
                held_info.priority if held_info else self.STANDARD_HIERARCHY.get(held_name, 999)
            )

            if priority < held_priority:
                # Trying to acquire higher-priority lock while holding lower-priority
                violation = LockHierarchyError(
                    f"Lock hierarchy violation: attempting to acquire '{lock_name}' "
                    f"(priority {priority}) while holding '{held_name}' (priority {held_priority})",
                    held_locks=list(stack),
                    attempted_lock=lock_name,
                )
                with self._violations_lock:
                    self._violations.append(violation)
                raise violation

        stack.append(lock_name)
        try:
            if lock_info:
                with lock_info.lock:
                    yield
            else:
                yield
        finally:
            stack.pop()

    def get_violations(self) -> list[LockHierarchyError]:
        """Get all recorded violations."""
        with self._violations_lock:
            return list(self._violations)

    def clear_violations(self) -> None:
        """Clear recorded violations."""
        with self._violations_lock:
            self._violations.clear()

    def get_held_locks(self) -> list[str]:
        """Get list of locks held by current thread."""
        return list(self._get_held_stack())


# Global tracker instance for tests
_global_tracker = LockTracker()


def get_global_tracker() -> LockTracker:
    """Get the global lock tracker instance."""
    return _global_tracker


def reset_global_tracker() -> None:
    """Reset the global tracker (for test isolation)."""
    global _global_tracker
    _global_tracker = LockTracker()
