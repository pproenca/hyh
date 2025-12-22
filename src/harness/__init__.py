"""Harness - Autonomous Research Kernel with Thread-Safe Pull Engine."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("harness-cli")
except PackageNotFoundError:
    # Running from source without install
    __version__ = "0.0.0+dev"
