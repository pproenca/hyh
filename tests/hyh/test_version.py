"""Tests for version management."""

import re
import subprocess
import sys


def test_version_importable():
    """Verify __version__ is accessible from package."""
    from hyh import __version__

    assert __version__ is not None
    assert isinstance(__version__, str)


def test_version_format_pep440():
    """Verify version follows PEP 440 format."""
    from hyh import __version__

    # PEP 440: N[.N]+[{a|b|rc}N][.postN][.devN]
    pep440_pattern = r"^\d+\.\d+\.\d+(a|b|rc)?\d*(\.post\d+)?(\.dev\d+)?(\+.+)?$"
    assert re.match(pep440_pattern, __version__), (
        f"Version '{__version__}' is not PEP 440 compliant"
    )


def test_version_matches_metadata():
    """Verify __version__ matches installed package metadata."""
    from importlib.metadata import version

    from hyh import __version__

    installed_version = version("hyh")
    assert __version__ == installed_version


def test_version_cli_accessible():
    """Verify version can be accessed via CLI import."""
    result = subprocess.run(
        [sys.executable, "-c", "from hyh import __version__; print(__version__)"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() != ""


def test_version_is_alpha():
    """Verify current version is alpha release."""
    from hyh import __version__

    assert "a" in __version__ or "+dev" in __version__, (
        f"Expected alpha version, got '{__version__}'"
    )
