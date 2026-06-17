from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def default_codex_home() -> Path:
    configured = os.getenv("CODEX_HOME", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".codex"


def enrich_codex_session_metadata(
    metadata: dict[str, Any],
    *,
    codex_home: str | Path | None = None,
) -> dict[str, Any]:
    enriched = dict(metadata)
    if enriched.get("codex_session_id") and enriched.get("codex_thread_id"):
        return enriched
    cwd = str(enriched.get("cwd") or "").strip()
    if not cwd:
        return enriched
    inferred = infer_latest_codex_session_metadata(cwd, codex_home=codex_home)
    if not inferred:
        return enriched
    if not enriched.get("codex_session_id"):
        enriched["codex_session_id"] = inferred["codex_session_id"]
    if not enriched.get("codex_thread_id"):
        enriched["codex_thread_id"] = inferred["codex_thread_id"]
    return enriched


def infer_latest_codex_session_metadata(
    cwd: str,
    *,
    codex_home: str | Path | None = None,
    max_files: int = 1000,
) -> dict[str, str]:
    target = _normalized_path(cwd)
    if not target:
        return {}
    root = Path(codex_home).expanduser() if codex_home else default_codex_home()
    sessions_dir = root / "sessions"
    if not sessions_dir.is_dir():
        return {}
    candidates: list[tuple[float, Path]] = []
    for path in sessions_dir.rglob("*.jsonl"):
        try:
            candidates.append((path.stat().st_mtime, path))
        except OSError:
            continue
    for _, path in sorted(candidates, key=lambda item: item[0], reverse=True)[:max_files]:
        session = _read_session_meta(path)
        if not session:
            continue
        if _normalized_path(str(session.get("cwd") or "")) != target:
            continue
        session_id = str(session.get("id") or "").strip()
        if not session_id:
            continue
        return {
            "codex_session_id": session_id,
            "codex_thread_id": session_id,
        }
    return {}


def _read_session_meta(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            first_line = handle.readline()
    except OSError:
        return {}
    if not first_line:
        return {}
    try:
        payload = json.loads(first_line)
    except json.JSONDecodeError:
        return {}
    if payload.get("type") != "session_meta":
        return {}
    session = payload.get("payload")
    return session if isinstance(session, dict) else {}


def _normalized_path(value: str) -> str:
    if not value.strip():
        return ""
    return str(Path(value).expanduser().resolve(strict=False))
