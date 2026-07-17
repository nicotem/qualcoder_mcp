"""Qualcoder MCP Server - Model Context Protocol integration for Qualcoder."""

from importlib.metadata import PackageNotFoundError, version

try:
    # Single source of truth: the installed package metadata (pyproject.toml)
    __version__ = version("qualcoder-mcp")
except PackageNotFoundError:  # running from a source tree without install
    __version__ = "0.0.0+unknown"
