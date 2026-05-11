"""Tests for package metadata consistency."""

import re
from pathlib import Path

import svx


def test_package_version_matches_pyproject():
    """Runtime package version should match the published package metadata."""
    pyproject = Path(__file__).parent.parent / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)

    assert match is not None
    assert svx.__version__ == match.group(1)
