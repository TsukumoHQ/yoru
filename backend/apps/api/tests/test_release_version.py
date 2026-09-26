"""Release hygiene: the server version in pyproject.toml must match the newest
CHANGELOG section, because release-sync extracts the release body from it."""
import re
import tomllib
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[4]


def test_pyproject_version_matches_top_changelog_section():
    version = tomllib.loads((_ROOT / "backend" / "pyproject.toml").read_text())["project"]["version"]
    top = re.search(r"^## \[(\d+\.\d+\.\d+)\]", (_ROOT / "CHANGELOG.md").read_text(), re.M)
    assert top is not None
    assert top.group(1) == version
