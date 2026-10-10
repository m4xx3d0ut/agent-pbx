from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"


def test_package_readme_uses_pypi_safe_image_sources() -> None:
    body = README.read_text(encoding="utf-8")

    html_sources = re.findall(r"<img\s+[^>]*src=[\"']([^\"']+)[\"']", body, re.I)
    markdown_sources = re.findall(r"!\[[^\]]*\]\(([^)\s]+)", body)
    sources = [*html_sources, *markdown_sources]

    assert sources, "README should contain its public product visuals"
    assert all(source.startswith("https://") for source in sources)
    assert "```mermaid" not in body


def test_package_readme_architecture_source_and_asset_are_tracked() -> None:
    body = README.read_text(encoding="utf-8")
    source = ROOT / "docs" / "diagrams" / "agent-pbx-control-plane.mmd"
    asset = ROOT / "docs" / "assets" / "agent-pbx-control-plane.png"

    assert source.is_file()
    assert asset.is_file()
    assert asset.stat().st_size > 10_000
    assert (
        "https://github.com/m4xx3d0ut/agent-pbx/blob/dev/"
        "docs/diagrams/agent-pbx-control-plane.mmd"
    ) in body
