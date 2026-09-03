"""Keep repository documentation and genuine screenshot assets shippable."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_markdown_local_links_exist():
    documents = list(ROOT.glob("*.md")) + list((ROOT / "docs").glob("*.md"))
    for document in documents:
        for destination in re.findall(r"\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
            if "://" in destination or destination.startswith("#"):
                continue
            target = destination.split("#", 1)[0]
            assert (document.parent / target).is_file(), (document.name, target)


@pytest.mark.parametrize("filename", ["dashboard.jpg", "protocol-log.jpg", "mobile.jpg"])
def test_readme_screenshots_match_native_jpeg_format(filename):
    data = (ROOT / "docs" / "images" / filename).read_bytes()
    assert data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9")
    assert len(data) > 10000
