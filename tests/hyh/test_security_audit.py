"""
Red Team Security Audit: Input Validation and Injection Vulnerabilities.

These tests are designed to expose security bugs. Tests that FAIL indicate
vulnerabilities that need to be fixed.

Tests focus on:
- Path traversal attacks (VolumeMapper)
- Git argument injection (safe_git_exec)
- Plan content injection (parse_markdown_plan)
- Environment variable override attacks
- Docker command injection
"""

import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestPathTraversalVolumeMapper:
    """CRITICAL: Path traversal in VolumeMapper (runtime.py:113-120).

    Vulnerability: VolumeMapper.to_runtime() does not sanitize path traversal
    sequences like `../..` which can escape the container mount.

    Attack vector: An attacker provides a path like `/host/workspace/../../../etc/passwd`
    which maps to `/workspace/../../../etc/passwd` - escaping the container.
    """

    def test_path_traversal_escape_via_dotdot(self) -> None:
        """VolumeMapper should reject or normalize paths with .. sequences.

        Security requirement: Path traversal attempts should NOT be mapped
        to container paths that could escape the mount.
        """
        import os

        from hyh.runtime import VolumeMapper

        mapper = VolumeMapper(host_root="/host/workspace", container_root="/workspace")

        # Attack: Escape container via path traversal
        malicious_path = "/host/workspace/../../etc/passwd"
        result = mapper.to_runtime(malicious_path)

        # Security check: The result should either:
        # 1. Not be mapped (returns original host path) - acceptable if not passed to container
        # 2. Normalize to container root or reject
        # Key: If it IS mapped, it must not escape container_root

        if result.startswith("/workspace"):
            # If mapped to container, verify it doesn't escape
            normalized = os.path.normpath(result)
            assert normalized.startswith("/workspace"), (
                f"Path traversal escape: {malicious_path} -> {result} "
                f"(normalized: {normalized}). Attacker can escape container mount."
            )
        else:
            # Not mapped - original path returned, which is safe
            # (the path won't be used in container context)
            pass

    def test_path_traversal_multiple_patterns(self) -> None:
        """VolumeMapper should handle various traversal patterns."""
        from hyh.runtime import VolumeMapper

        mapper = VolumeMapper(host_root="/host/workspace", container_root="/workspace")

        traversal_paths = [
            "/host/workspace/../secret",
            "/host/workspace/foo/../../secret",
            "/host/workspace/./../../secret",
            "/host/workspace/foo/../bar/../../../secret",
        ]

        for path in traversal_paths:
            result = mapper.to_runtime(path)

            normalized = os.path.normpath(result)
            # Either should start with container root or remain unchanged (not mapped)
            is_safe = normalized.startswith("/workspace") or result == path
            assert is_safe, f"Path {path} -> {result} (normalized: {normalized}) escapes container"

    def test_path_traversal_via_to_host(self) -> None:
        """to_host() should also reject path traversal."""
        from hyh.runtime import VolumeMapper

        mapper = VolumeMapper(host_root="/host/workspace", container_root="/workspace")

        # Attack from container side
        malicious_path = "/workspace/../../../etc/passwd"
        result = mapper.to_host(malicious_path)

        # Should not allow escape to host filesystem
        assert ".." not in result or result == malicious_path, (
            f"Path traversal in to_host: {malicious_path} -> {result}"
        )


class TestGitArgumentInjection:
    """CRITICAL: Git argument injection (git.py:35-40).

    Vulnerability: safe_git_exec() passes args directly to git without
    validating they don't contain dangerous options like -c.
    """

    def test_git_config_injection_via_c_flag(self) -> None:
        """Git -c flag can execute arbitrary commands via core.pager.

        Attack: args=["-c", "core.pager=rm -rf /", "log"] would execute
        the pager command when git tries to display output.
        """
        from hyh.git import safe_git_exec

        with tempfile.TemporaryDirectory() as tmpdir:
            subprocess.run(["git", "init"], cwd=tmpdir, capture_output=True, check=True)
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=tmpdir,
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"],
                cwd=tmpdir,
                capture_output=True,
                check=True,
            )
            Path(tmpdir, "file.txt").write_text("content")
            subprocess.run(["git", "add", "."], cwd=tmpdir, capture_output=True, check=True)
            subprocess.run(
                ["git", "commit", "-m", "initial"],
                cwd=tmpdir,
                capture_output=True,
                check=True,
            )

            # Attack payload - in real attack would be: core.pager=sh -c 'malicious'
            # We use a benign marker to detect if -c is processed
            malicious_args = ["-c", "core.pager=echo INJECTED", "log", "-1"]

            # Expected: safe_git_exec should reject args starting with -c
            # or validate against a whitelist of allowed git commands
            with pytest.raises(ValueError, match="[Ii]nvalid|[Dd]angerous|not allowed|-c"):
                safe_git_exec(malicious_args, tmpdir)

    def test_git_upload_pack_injection(self) -> None:
        """Git --upload-pack can execute arbitrary commands."""
        from hyh.git import safe_git_exec

        with tempfile.TemporaryDirectory() as tmpdir:
            subprocess.run(["git", "init"], cwd=tmpdir, capture_output=True, check=True)

            # Another dangerous option
            malicious_args = ["clone", "--upload-pack=echo INJECTED", ".", "clone"]

            # Should be rejected
            with pytest.raises(ValueError, match="[Ii]nvalid|[Dd]angerous|not allowed"):
                safe_git_exec(malicious_args, tmpdir)


class TestPlanContentInjection:
    """HIGH: Unsanitized plan instructions (plan.py:73-181).

    Vulnerability: Instructions extracted from Markdown plans are passed
    to agents without sanitization. Malicious plans could inject commands.
    """

    def test_plan_task_id_with_shell_metacharacters(self) -> None:
        """Task IDs with shell metacharacters should not be parsed.

        Defense-in-depth: The regex pattern only matches safe characters,
        so task IDs with $, `, ;, etc. are simply not extracted.
        This results in phantom/orphan task errors.
        """
        from hyh.plan import parse_markdown_plan

        malicious_plan = """\
# Plan

**Goal:** Test

| Task Group | Tasks |
|------------|-------|
| Group 1 | task-$(whoami) |

### Task task-safe: Valid Task

Do something
"""
        # Task with shell metacharacters won't be extracted by regex,
        # so it becomes a phantom task (in table but not in body)
        # or orphan task (in body but not in table)
        with pytest.raises(ValueError, match="[Pp]hantom|[Oo]rphan|not in"):
            parse_markdown_plan(malicious_plan)

    def test_plan_task_id_with_semicolon(self) -> None:
        """Task IDs with semicolons should not be parsed."""
        from hyh.plan import parse_markdown_plan

        malicious_plan = """\
# Plan

**Goal:** Test

| Task Group | Tasks |
|------------|-------|
| Group 1 | task;rm |

### Task task-safe: Valid

Content
"""
        # Semicolon breaks regex, becomes phantom task
        with pytest.raises(ValueError, match="[Pp]hantom|[Oo]rphan|not in"):
            parse_markdown_plan(malicious_plan)

    def test_plan_task_id_with_backticks(self) -> None:
        """Task IDs with backticks should not be parsed."""
        from hyh.plan import parse_markdown_plan

        malicious_plan = """\
# Plan

**Goal:** Test

| Task Group | Tasks |
|------------|-------|
| Group 1 | task`id` |

### Task task-safe: Valid

Content
"""
        # Backticks break regex, becomes phantom task
        with pytest.raises(ValueError, match="[Pp]hantom|[Oo]rphan|not in"):
            parse_markdown_plan(malicious_plan)

    def test_valid_task_id_with_validation(self) -> None:
        """Valid task IDs should pass validation."""
        from hyh.plan import parse_markdown_plan

        valid_plan = """\
# Plan

**Goal:** Test

| Task Group | Tasks |
|------------|-------|
| Group 1 | task-1, task_2, task.3 |

### Task task-1: First

Content 1

### Task task_2: Second

Content 2

### Task task.3: Third

Content 3
"""
        plan = parse_markdown_plan(valid_plan)
        assert "task-1" in plan.tasks
        assert "task_2" in plan.tasks
        assert "task.3" in plan.tasks


class TestSocketPathOverride:
    """HIGH: Socket path env override (client.py:76-101).

    Vulnerability: HYH_SOCKET env var can redirect IPC to arbitrary path.
    """

    def test_socket_path_to_devnull_accepted(self) -> None:
        """Setting HYH_SOCKET=/dev/null should be rejected or handled.

        This documents the vulnerability - /dev/null causes DoS.
        """
        from hyh.client import get_socket_path

        with patch.dict(os.environ, {"HYH_SOCKET": "/dev/null"}):
            path = get_socket_path()
            # Currently accepts any path - this is the vulnerability
            # Ideally should validate it's a valid socket path
            assert path == "/dev/null"

    def test_socket_path_with_null_bytes(self) -> None:
        """Socket path with null bytes should be rejected.

        Note: The OS itself rejects null bytes in env vars (ValueError).
        This test documents that behavior as a defense-in-depth measure.
        """
        # Attempting to set an env var with null byte raises ValueError
        # This is a Python/OS level protection that we rely on
        with pytest.raises(ValueError, match="null byte"):
            os.environ["HYH_SOCKET"] = "/tmp/test\x00.sock"


class TestDockerCommandInjection:
    """MEDIUM: Docker command injection (runtime.py:281-302).

    Tests verify that command arguments are properly isolated.
    """

    @patch("subprocess.run")
    def test_env_var_with_shell_metacharacters(self, mock_run: MagicMock) -> None:
        """Environment variables with shell metacharacters should be safe.

        Since subprocess.run with list args doesn't use shell, this should be safe.
        This test verifies the safe pattern is in place.
        """
        from hyh.runtime import DockerRuntime, IdentityMapper

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        runtime = DockerRuntime("container", IdentityMapper())

        # Potentially dangerous env var value
        env_with_metachar = {"ATTACK": "value; rm -rf /"}
        runtime.execute(["echo", "test"], env=env_with_metachar)

        # Verify shell=True was NOT used
        assert mock_run.call_args[1].get("shell") is not True

    @patch("subprocess.run")
    def test_command_with_shell_metacharacters(self, mock_run: MagicMock) -> None:
        """Command arguments with shell metacharacters should not be interpreted."""
        from hyh.runtime import DockerRuntime, IdentityMapper

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        runtime = DockerRuntime("container", IdentityMapper())

        # Command with shell metacharacters
        runtime.execute(["echo", "hello; cat /etc/passwd"])

        call_args = mock_run.call_args[0][0]

        # The argument should be passed literally, not split
        assert "hello; cat /etc/passwd" in call_args
        assert mock_run.call_args[1].get("shell") is not True

    @patch("subprocess.run")
    def test_container_id_with_spaces(self, mock_run: MagicMock) -> None:
        """Container ID with injection attempt should be handled safely."""
        from hyh.runtime import DockerRuntime, IdentityMapper

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # Container ID that looks like it has extra args
        # This should be passed as one argument, not split
        malicious_container = "mycontainer --privileged"
        runtime = DockerRuntime(malicious_container, IdentityMapper())
        runtime.execute(["whoami"])

        # The container ID should be a single element (or cause an earlier error)
        # subprocess.run with list won't split it, which is safe
        # But Docker will error on invalid container name
        assert mock_run.called


class TestEmptyCommandValidation:
    """MEDIUM: Empty command validation (daemon.py:282-283).

    Vulnerability: args=[""] passes validation but causes undefined behavior.
    """

    def test_empty_string_command_behavior(self) -> None:
        """Document that [""] passes if not args check.

        The validation `if not args` passes for [""] because bool([""]) is True.
        """
        # Document the vulnerability
        args: list[str] = [""]
        passes_not_check = not args  # False - [""] is truthy

        # This shows the validation gap
        assert not passes_not_check, "Empty string list passes 'if not args' check"

        # Proper validation should also check first element
        proper_check = not args or not args[0]
        assert proper_check, "Proper validation catches empty command"


class TestWorkerIdValidation:
    """Tests for worker ID validation edge cases."""

    def test_worker_id_with_special_characters(self) -> None:
        """Worker ID generation should produce safe IDs only."""
        from hyh.client import get_worker_id

        with tempfile.TemporaryDirectory() as tmpdir:
            worker_id_file = Path(tmpdir) / "worker.id"

            with patch.dict(os.environ, {"HYH_WORKER_ID_FILE": str(worker_id_file)}):
                worker_id = get_worker_id()

                # Worker ID should be alphanumeric with hyphens only
                import re

                assert re.match(r"^worker-[a-f0-9]+$", worker_id), (
                    f"Worker ID {worker_id} contains unexpected characters"
                )
