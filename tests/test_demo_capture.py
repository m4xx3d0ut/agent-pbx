from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_pbx.demo.capture import (
    CAPTURE_MANIFEST_API_VERSION,
    asciicast_snapshot,
    finalize_snapshot,
    prepare_snapshot,
    visible_terminal_text,
)


def test_visible_terminal_text_removes_ansi_and_normalizes_lines() -> None:
    raw = "\x1b[31mAgent PBX\x1b[0m\r\n\x1b]0;private title\x07Ready\r"

    assert visible_terminal_text(raw) == "Agent PBX\nReady\n"


def test_asciicast_snapshot_preserves_styled_frame() -> None:
    payload = asciicast_snapshot(
        "\x1b[36mLatest\x1b[0m\nReady",
        cols=207,
        rows=46,
        title="Agent PBX capture",
    )
    header, event = [json.loads(line) for line in payload.splitlines()]

    assert header["version"] == 2
    assert header["width"] == 207
    assert header["height"] == 46
    assert event[:2] == [0.0, "o"]
    assert "\x1b[36mLatest" in event[2]
    assert "Latest\x1b[0m\r\nReady" in event[2]


def test_prepare_and_finalize_snapshot_records_review_evidence(tmp_path: Path) -> None:
    raw = tmp_path / "scene.ansi"
    cast = tmp_path / "scene.cast"
    plain = tmp_path / "scene.txt"
    image = tmp_path / "scene.png"
    manifest = tmp_path / "manifest.json"
    raw.write_text("\x1b[35mEditor: nvim\x1b[0m\nruntime.py\n", encoding="utf-8")
    image.write_bytes(b"png fixture")

    prepared = prepare_snapshot(
        raw_path=raw,
        cast_path=cast,
        plain_path=plain,
        cols=207,
        rows=46,
        title="Managed Neovim",
    )
    record = finalize_snapshot(
        manifest_path=manifest,
        scene="managed-neovim",
        image_path=image,
        plain_path=plain,
        source_commit="abc123",
        cols=207,
        rows=46,
        width=1920,
        height=800,
        expected=("Editor: nvim", "runtime.py"),
        forbidden=("/home/private", "operator-0"),
    )

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert prepared["cols"] == 207
    assert payload["api_version"] == CAPTURE_MANIFEST_API_VERSION
    assert payload["capture_mode"] == "deterministic-fixtures"
    assert payload["scenes"] == [record]
    assert record["image_size"] == {"width": 1920, "height": 800}
    assert record["image_sha256"]


def test_finalize_snapshot_rejects_missing_and_forbidden_markers(
    tmp_path: Path,
) -> None:
    plain = tmp_path / "scene.txt"
    image = tmp_path / "scene.png"
    plain.write_text("Agent PBX\n/home/private/project\n", encoding="utf-8")
    image.write_bytes(b"png fixture")

    with pytest.raises(ValueError, match="missing expected markers"):
        finalize_snapshot(
            manifest_path=tmp_path / "manifest.json",
            scene="native-latest",
            image_path=image,
            plain_path=plain,
            source_commit="abc123",
            cols=207,
            rows=46,
            width=1920,
            height=800,
            expected=("DEMO CODEX RUNTIME",),
        )

    with pytest.raises(ValueError, match="contains forbidden text"):
        finalize_snapshot(
            manifest_path=tmp_path / "manifest.json",
            scene="native-latest",
            image_path=image,
            plain_path=plain,
            source_commit="abc123",
            cols=207,
            rows=46,
            width=1920,
            height=800,
            forbidden=("/home/private",),
        )
