"""Tests for project registry."""

from pathlib import Path

from hyh.registry import ProjectRegistry


def test_registry_load_empty(tmp_path: Path) -> None:
    """Registry returns empty projects dict when file doesn't exist."""
    registry_file = tmp_path / "registry.json"
    registry = ProjectRegistry(registry_file)

    assert registry.list_projects() == {}


def test_registry_register_project(tmp_path: Path) -> None:
    """Registry persists project on register."""
    registry_file = tmp_path / "registry.json"
    registry = ProjectRegistry(registry_file)

    worktree = Path("/Users/test/project")
    registry.register(worktree)
    registry2 = ProjectRegistry(registry_file)
    projects = registry2.list_projects()

    assert len(projects) == 1
    assert str(worktree) in [p["path"] for p in projects.values()]


def test_registry_concurrent_registration(tmp_path: Path) -> None:
    """Concurrent registrations don't lose data (race condition safety)."""
    import concurrent.futures

    registry_file = tmp_path / "registry.json"
    projects = [tmp_path / f"project_{i}" for i in range(10)]
    for p in projects:
        p.mkdir()

    def register_project(proj: Path) -> str:
        registry = ProjectRegistry(registry_file)
        return registry.register(proj)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        list(executor.map(register_project, projects))

    registry = ProjectRegistry(registry_file)
    registered = registry.list_projects()
    assert len(registered) == 10
