# tests/hyh/test_demo.py
"""Tests for demo module."""

import os
from pathlib import Path

from hyh.demo import (
    NC,
    print_command,
    print_explanation,
    print_header,
    print_info,
    print_step,
    print_success,
)


def test_print_header_format(capsys: object) -> None:
    """Test header prints with magenta borders."""
    print_header("Test Title")
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "Test Title" in captured.out
    assert "━" in captured.out  # Border character
    assert "\033[0;35m" in captured.out  # MAGENTA


def test_print_step_format(capsys: object) -> None:
    """Test step prints with cyan arrow."""
    print_step("Do something")
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "▶" in captured.out
    assert "Do something" in captured.out
    assert "\033[0;36m" in captured.out  # CYAN


def test_print_info_format(capsys: object) -> None:
    """Test info prints dimmed and indented."""
    print_info("Some info")
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "Some info" in captured.out
    assert "\033[2m" in captured.out  # DIM


def test_print_success_format(capsys: object) -> None:
    """Test success prints green with checkmark."""
    print_success("Done")
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "✓" in captured.out
    assert "Done" in captured.out
    assert "\033[0;32m" in captured.out  # GREEN


def test_print_command_format(capsys: object) -> None:
    """Test command prints yellow with $ prefix."""
    print_command("hyh ping")
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "$" in captured.out
    assert "hyh ping" in captured.out
    assert "\033[1;33m" in captured.out  # YELLOW


def test_print_explanation_format(capsys: object) -> None:
    """Test explanation prints blue with info icon."""
    print_explanation("Why this works")
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "\N{INFORMATION SOURCE}" in captured.out
    assert "Why this works" in captured.out
    assert "\033[0;34m" in captured.out  # BLUE


def test_all_outputs_end_with_reset(capsys: object) -> None:
    """Test all outputs reset color at end."""
    print_header("H")
    print_step("S")
    print_info("I")
    print_success("OK")
    print_command("cmd")
    print_explanation("E")
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    # Each line should end with NC (reset)
    assert captured.out.count(NC) >= 6


def test_cleanup_removes_temp_directory(tmp_path: Path) -> None:
    """Test cleanup removes the demo directory."""
    from hyh.demo import cleanup

    demo_dir = tmp_path / "demo"
    demo_dir.mkdir()
    (demo_dir / "file.txt").write_text("test")

    cleanup(demo_dir)

    assert not demo_dir.exists()


def test_run_restores_cwd(monkeypatch: object) -> None:
    """Test run() restores original cwd on success."""
    from hyh.demo import run

    original = os.getcwd()
    # Patch to skip actual demo steps
    monkeypatch.setattr("hyh.demo._run_all_steps", lambda d: None)  # type: ignore[attr-defined]

    run()

    assert os.getcwd() == original


def test_run_restores_cwd_on_error(monkeypatch: object) -> None:
    """Test run() restores original cwd when _run_all_steps raises."""
    import pytest

    from hyh.demo import run

    original = os.getcwd()

    def raise_error(d: Path) -> None:
        raise RuntimeError("Demo failed")

    monkeypatch.setattr("hyh.demo._run_all_steps", raise_error)  # type: ignore[attr-defined]

    with pytest.raises(RuntimeError):
        run()

    assert os.getcwd() == original
