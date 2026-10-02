from __future__ import annotations

from dataclasses import dataclass
import hashlib
from importlib import resources
import json
import os
from pathlib import Path
import shutil
import time
from typing import Any, Iterable


MANAGED_START = "<!-- agent-pbx-managed-skill:v2 start -->"
MANAGED_END = "<!-- agent-pbx-managed-skill:v2 end -->"
PACK_NAMES = (
    "agent-pbx-base",
    "roses-architect",
    "agent-role",
    "operator-role",
    "review-role",
)


@dataclass(frozen=True)
class SkillPackPreview:
    name: str
    target: str
    action: str
    source_hash: str
    installed_hash: str | None
    managed: bool
    estimated_tokens: int
    error: str | None = None


@dataclass(frozen=True)
class PersonalityOverlay:
    active: bool
    name: str
    path: str
    content_hash: str
    size_bytes: int

    @classmethod
    def load(cls, path: Path) -> "PersonalityOverlay":
        try:
            body = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return cls(False, "", str(path), "", 0)
        digest = hashlib.sha256(body.encode()).hexdigest()
        name = "Rosie-0, known simply as Rosie" if body.strip() else ""
        return cls(bool(body.strip()), name, str(path), digest, len(body.encode()))

    def public_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "name": self.name,
            "path": self.path,
            "content_hash": self.content_hash,
            "size_bytes": self.size_bytes,
        }


class ManagedSkillPackService:
    def __init__(self, project_root: Path, *, pack_names: Iterable[str] = PACK_NAMES) -> None:
        self.project_root = project_root.resolve()
        self.pack_names = tuple(dict.fromkeys(str(name) for name in pack_names))
        unknown = tuple(name for name in self.pack_names if name not in PACK_NAMES)
        if unknown:
            raise ValueError(f"Unknown managed skill pack(s): {', '.join(unknown)}")
        self.codex_root = self.project_root / ".codex"
        self.skills_root = self.codex_root / "skills"
        self.manifest_path = self.codex_root / "agent-pbx-managed-skills.json"
        self.backup_root = self.codex_root / "agent-pbx-skill-backups"

    def preview(self) -> tuple[SkillPackPreview, ...]:
        previews: list[SkillPackPreview] = []
        for name in self.pack_names:
            body = self._source(name)
            source_hash = self._digest(body)
            target = self.skills_root / name / "SKILL.md"
            installed: str | None = None
            managed = False
            error: str | None = None
            try:
                installed = target.read_text(encoding="utf-8")
                managed = self._managed(installed)
            except FileNotFoundError:
                pass
            except OSError as exc:
                error = str(exc)
            installed_hash = self._digest(installed) if installed is not None else None
            if error:
                action = "error"
            elif installed is None:
                action = "create"
            elif not managed:
                action = "refuse_unmanaged"
            elif installed_hash == source_hash:
                action = "unchanged"
            else:
                action = "update"
            previews.append(
                SkillPackPreview(
                    name=name,
                    target=str(target),
                    action=action,
                    source_hash=source_hash,
                    installed_hash=installed_hash,
                    managed=managed,
                    estimated_tokens=max(1, len(body) // 4),
                    error=error,
                )
            )
        return tuple(previews)

    def apply(self, *, max_estimated_tokens: int = 12_000) -> dict[str, Any]:
        preview = self.preview()
        refused = [item for item in preview if item.action in {"refuse_unmanaged", "error"}]
        if refused:
            raise ValueError(
                "Managed skill apply refused: "
                + ", ".join(f"{item.name}={item.action}" for item in refused)
            )
        token_total = sum(item.estimated_tokens for item in preview)
        if token_total > max_estimated_tokens:
            raise ValueError(
                f"Managed skills estimate {token_total} tokens, above the "
                f"reviewed limit {max_estimated_tokens}."
            )
        timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        backup_dir = self.backup_root / timestamp
        changed: list[str] = []
        for item in preview:
            if item.action == "unchanged":
                continue
            target = Path(item.target)
            if target.exists():
                backup = backup_dir / item.name / "SKILL.md"
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup)
            target.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_write(target, self._source(item.name))
            changed.append(item.name)
        manifest = {
            "api_version": "agent-pbx.managed-skills/v2",
            "project_root": str(self.project_root),
            "installed_at": time.time(),
            "backup_dir": str(backup_dir) if backup_dir.exists() else None,
            "estimated_tokens": token_total,
            "packs": [item.__dict__ for item in self.preview()],
        }
        self._atomic_write(self.manifest_path, json.dumps(manifest, indent=2) + "\n")
        return {"changed": changed, "manifest": manifest}

    def drift(self) -> tuple[SkillPackPreview, ...]:
        return tuple(item for item in self.preview() if item.action not in {"unchanged"})

    def rollback(self) -> dict[str, Any]:
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError("No managed skill manifest is installed.") from exc
        backup_value = manifest.get("backup_dir")
        restored: list[str] = []
        removed: list[str] = []
        for name in self.pack_names:
            target = self.skills_root / name / "SKILL.md"
            backup = Path(str(backup_value)) / name / "SKILL.md" if backup_value else None
            if backup is not None and backup.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup, target)
                restored.append(name)
            elif target.exists() and self._managed(target.read_text(encoding="utf-8")):
                target.unlink()
                removed.append(name)
        self.manifest_path.unlink(missing_ok=True)
        return {"restored": restored, "removed": removed}

    @staticmethod
    def _managed(body: str) -> bool:
        return MANAGED_START in body and MANAGED_END in body

    @staticmethod
    def _digest(body: str) -> str:
        return hashlib.sha256(body.encode()).hexdigest()

    @staticmethod
    def _atomic_write(path: Path, body: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(body, encoding="utf-8")
        os.replace(temp, path)

    @staticmethod
    def _source(name: str) -> str:
        if name not in PACK_NAMES:
            raise ValueError(f"Unknown managed skill pack {name!r}.")
        source = resources.files("agent_pbx").joinpath("skill_packs", name, "SKILL.md")
        return source.read_text(encoding="utf-8")
