from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any, Sequence


CAPTURE_MANIFEST_API_VERSION = "agent-pbx.demo-capture/v1"

_OSC_PATTERN = re.compile(r"\x1b\].*?(?:\x07|\x1b\\)", re.DOTALL)
_CSI_PATTERN = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def visible_terminal_text(value: str) -> str:
    """Return a reviewable text representation of one terminal frame."""

    without_osc = _OSC_PATTERN.sub("", value)
    without_csi = _CSI_PATTERN.sub("", without_osc)
    normalized = without_csi.replace("\r\n", "\n").replace("\r", "\n")
    return _CONTROL_PATTERN.sub("", normalized)


def asciicast_snapshot(
    value: str,
    *,
    cols: int,
    rows: int,
    title: str,
) -> str:
    """Build a one-frame asciicast v2 document from a styled tmux capture."""

    if cols <= 0 or rows <= 0:
        raise ValueError("capture geometry must be positive")
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    terminal_frame = (
        "\x1b[?25l\x1b[2J\x1b[H"
        + normalized.replace("\n", "\r\n")
        + "\x1b[0m"
    )
    header = {
        "version": 2,
        "width": int(cols),
        "height": int(rows),
        "timestamp": int(time.time()),
        "title": title,
        "env": {"SHELL": "/bin/sh", "TERM": "xterm-256color"},
    }
    return "\n".join(
        (
            json.dumps(header, sort_keys=True),
            json.dumps([0.0, "o", terminal_frame]),
            "",
        )
    )


def prepare_snapshot(
    *,
    raw_path: Path,
    cast_path: Path,
    plain_path: Path,
    cols: int,
    rows: int,
    title: str,
) -> dict[str, Any]:
    raw = raw_path.read_text(encoding="utf-8", errors="replace")
    plain = visible_terminal_text(raw)
    cast_path.parent.mkdir(parents=True, exist_ok=True)
    plain_path.parent.mkdir(parents=True, exist_ok=True)
    cast_path.write_text(
        asciicast_snapshot(raw, cols=cols, rows=rows, title=title),
        encoding="utf-8",
    )
    plain_path.write_text(plain, encoding="utf-8")
    return {
        "cols": int(cols),
        "rows": int(rows),
        "plain_sha256": hashlib.sha256(plain.encode()).hexdigest(),
    }


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"api_version": CAPTURE_MANIFEST_API_VERSION, "scenes": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("api_version") != CAPTURE_MANIFEST_API_VERSION:
        raise ValueError("capture manifest API version does not match")
    if not isinstance(payload.get("scenes"), list):
        raise ValueError("capture manifest scenes must be a list")
    return payload


def finalize_snapshot(
    *,
    manifest_path: Path,
    scene: str,
    image_path: Path,
    plain_path: Path,
    source_commit: str,
    cols: int,
    rows: int,
    width: int,
    height: int,
    expected: Sequence[str] = (),
    forbidden: Sequence[str] = (),
) -> dict[str, Any]:
    if not image_path.is_file() or image_path.stat().st_size <= 0:
        raise ValueError(f"capture image is missing or empty: {image_path}")
    plain = plain_path.read_text(encoding="utf-8", errors="replace")
    plain_casefold = plain.casefold()
    missing = [item for item in expected if item.casefold() not in plain_casefold]
    if missing:
        raise ValueError(
            f"capture scene {scene} is missing expected markers: {', '.join(missing)}"
        )
    exposed = [item for item in forbidden if item and item.casefold() in plain_casefold]
    if exposed:
        raise ValueError(
            f"capture scene {scene} contains forbidden text: {', '.join(exposed)}"
        )

    manifest = _load_manifest(manifest_path)
    image_bytes = image_path.read_bytes()
    scene_record = {
        "scene": scene,
        "image": str(image_path),
        "source_commit": source_commit,
        "terminal": {"cols": int(cols), "rows": int(rows)},
        "image_size": {"width": int(width), "height": int(height)},
        "expected_markers": list(expected),
        "plain_sha256": hashlib.sha256(plain.encode()).hexdigest(),
        "image_sha256": hashlib.sha256(image_bytes).hexdigest(),
        "captured_at": time.time(),
    }
    scenes = [
        item
        for item in manifest["scenes"]
        if isinstance(item, dict) and item.get("scene") != scene
    ]
    scenes.append(scene_record)
    manifest["scenes"] = scenes
    manifest["source_commit"] = source_commit
    manifest["capture_mode"] = "deterministic-fixtures"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return scene_record


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare deterministic TUI stills.")
    subcommands = parser.add_subparsers(dest="command", required=True)

    prepare = subcommands.add_parser("prepare")
    prepare.add_argument("--raw", type=Path, required=True)
    prepare.add_argument("--cast", type=Path, required=True)
    prepare.add_argument("--plain", type=Path, required=True)
    prepare.add_argument("--cols", type=int, required=True)
    prepare.add_argument("--rows", type=int, required=True)
    prepare.add_argument("--title", required=True)

    finalize = subcommands.add_parser("finalize")
    finalize.add_argument("--manifest", type=Path, required=True)
    finalize.add_argument("--scene", required=True)
    finalize.add_argument("--image", type=Path, required=True)
    finalize.add_argument("--plain", type=Path, required=True)
    finalize.add_argument("--source-commit", required=True)
    finalize.add_argument("--cols", type=int, required=True)
    finalize.add_argument("--rows", type=int, required=True)
    finalize.add_argument("--width", type=int, required=True)
    finalize.add_argument("--height", type=int, required=True)
    finalize.add_argument("--expect", action="append", default=[])
    finalize.add_argument("--forbid", action="append", default=[])
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare_snapshot(
                raw_path=args.raw,
                cast_path=args.cast,
                plain_path=args.plain,
                cols=args.cols,
                rows=args.rows,
                title=args.title,
            )
        else:
            result = finalize_snapshot(
                manifest_path=args.manifest,
                scene=args.scene,
                image_path=args.image,
                plain_path=args.plain,
                source_commit=args.source_commit,
                cols=args.cols,
                rows=args.rows,
                width=args.width,
                height=args.height,
                expected=args.expect,
                forbidden=args.forbid,
            )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
