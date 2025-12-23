"""
Registry Stress Tests for Concurrent Registration.

Tests for project registry concurrency and path handling.
"""

import tempfile
import threading
from pathlib import Path

from hyh.registry import ProjectRegistry


class TestConcurrentRegistration:
    """Test concurrent project registration."""

    def test_simultaneous_register_on_empty_file(self) -> None:
        """10 threads registering simultaneously should all succeed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_file = Path(tmpdir) / "registry.json"
            registry = ProjectRegistry(registry_file)

            registered: list[str] = []
            lock = threading.Lock()
            errors: list[str] = []
            barrier = threading.Barrier(10, timeout=5.0)

            def register_project(project_id: int) -> None:
                project_path = Path(tmpdir) / f"project-{project_id}"
                project_path.mkdir()
                barrier.wait()
                try:
                    result = registry.register(project_path)
                    with lock:
                        registered.append(result)
                except Exception as e:
                    with lock:
                        errors.append(str(e))

            threads = [threading.Thread(target=register_project, args=(i,)) for i in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert len(errors) == 0, f"Registration errors: {errors}"

            # All 10 should be registered
            projects = registry.list_projects()
            assert len(projects) == 10, f"Only {len(projects)} projects registered"


class TestPathNormalization:
    """Test path normalization and symlink handling."""

    def test_symlink_path_normalization(self) -> None:
        """Symlinks to same directory should get same hash."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_file = Path(tmpdir) / "registry.json"
            registry = ProjectRegistry(registry_file)

            # Create real directory
            real_path = Path(tmpdir) / "real"
            real_path.mkdir()

            # Create symlink
            link_path = Path(tmpdir) / "link"
            link_path.symlink_to(real_path)

            # Register both
            hash1 = registry.register(real_path)
            hash2 = registry.register(link_path)

            # Should get same hash (both resolve to same real path)
            # Note: This tests expected behavior, may fail if not implemented
            assert hash1 == hash2, "Symlink should resolve to same hash as real path"


class TestRegistryLocking:
    """Test registry file locking behavior."""

    def test_high_contention_no_data_loss(self) -> None:
        """High contention should not lose registrations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_file = Path(tmpdir) / "registry.json"
            registry = ProjectRegistry(registry_file)

            num_threads = 20
            registered_count = [0]
            lock = threading.Lock()

            def register_many(thread_id: int) -> None:
                for i in range(5):
                    project_path = Path(tmpdir) / f"project-{thread_id}-{i}"
                    project_path.mkdir()
                    registry.register(project_path)
                    with lock:
                        registered_count[0] += 1

            threads = [
                threading.Thread(target=register_many, args=(i,)) for i in range(num_threads)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # All registrations should have succeeded
            projects = registry.list_projects()
            assert len(projects) == num_threads * 5, (
                f"Expected {num_threads * 5} projects, got {len(projects)}"
            )
