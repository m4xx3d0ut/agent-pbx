from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import mimetypes
import os
import struct
from typing import Any


IGNORED_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    "artifacts",
    "build",
    "dist",
    "node_modules",
    "runs",
}
TEXT_EXTENSIONS = {
    ".cfg",
    ".css",
    ".csv",
    ".env",
    ".gitignore",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".jsx",
    ".lock",
    ".log",
    ".md",
    ".py",
    ".rs",
    ".sh",
    ".toml",
    ".tsx",
    ".txt",
    ".ts",
    ".xml",
    ".yaml",
    ".yml",
}
IMAGE_EXTENSIONS = {
    ".apng",
    ".avif",
    ".bmp",
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".svg",
    ".webp",
}
DEFAULT_LIST_LIMIT = 500
DEFAULT_PREVIEW_BYTES = 256 * 1024


@dataclass(frozen=True)
class FileBrowserError:
    code: str
    message: str
    remediation: str | None = None

    def as_dict(self) -> dict[str, str]:
        data = {"code": self.code, "message": self.message}
        if self.remediation:
            data["remediation"] = self.remediation
        return data


class AgentFileService:
    def __init__(
        self,
        *,
        list_limit: int = DEFAULT_LIST_LIMIT,
        preview_bytes: int = DEFAULT_PREVIEW_BYTES,
        ignored_dirs: set[str] | None = None,
    ) -> None:
        self.list_limit = max(1, list_limit)
        self.preview_bytes = max(1, preview_bytes)
        self.ignored_dirs = ignored_dirs or IGNORED_DIRS

    def list_for_agent(
        self, agent: dict[str, Any], *, path: str = "."
    ) -> dict[str, Any]:
        base = self._base_response(agent, path=path)
        root_result = self._agent_root(agent)
        if isinstance(root_result, FileBrowserError):
            return {**base, "parent": None, "entries": [], "error": root_result.as_dict()}
        root = root_result
        target_result = self._resolve_agent_path(root, path)
        if isinstance(target_result, FileBrowserError):
            return {**base, "cwd": str(root), "parent": None, "entries": [], "error": target_result.as_dict()}
        target = target_result
        if not target.exists():
            error = FileBrowserError(
                "FILE_NOT_FOUND",
                f"path does not exist: {self._display_path(root, target)}",
            )
            return {**base, "cwd": str(root), "parent": None, "entries": [], "error": error.as_dict()}
        if not target.is_dir():
            error = FileBrowserError(
                "NOT_A_DIRECTORY",
                f"path is not a directory: {self._display_path(root, target)}",
            )
            return {**base, "cwd": str(root), "parent": None, "entries": [], "error": error.as_dict()}

        entries: list[dict[str, Any]] = []
        try:
            children = list(target.iterdir())
        except OSError as exc:
            error = FileBrowserError("READ_FAILED", str(exc))
            return {**base, "cwd": str(root), "parent": None, "entries": [], "error": error.as_dict()}

        for child in children:
            if len(entries) >= self.list_limit:
                break
            if child.name in self.ignored_dirs and child.is_dir():
                continue
            try:
                resolved = child.resolve(strict=True)
            except OSError:
                continue
            if not self._is_inside(root, resolved):
                continue
            entries.append(self._entry(root, child, resolved))

        entries.sort(key=lambda item: (item["kind"] != "directory", item["name"].lower()))
        parent = self._parent_path(root, target)
        return {
            **base,
            "cwd": str(root),
            "path": self._display_path(root, target),
            "parent": parent,
            "entries": entries,
            "error": None,
        }

    def preview_for_agent(
        self, agent: dict[str, Any], *, path: str
    ) -> dict[str, Any]:
        base = self._base_response(agent, path=path)
        root_result = self._agent_root(agent)
        if isinstance(root_result, FileBrowserError):
            return {**base, "kind": "error", "size": None, "mime_type": None, "text": None, "truncated": False, "error": root_result.as_dict()}
        root = root_result
        target_result = self._resolve_agent_path(root, path)
        if isinstance(target_result, FileBrowserError):
            return {**base, "cwd": str(root), "kind": "error", "size": None, "mime_type": None, "text": None, "truncated": False, "error": target_result.as_dict()}
        target = target_result
        if not target.exists():
            error = FileBrowserError(
                "FILE_NOT_FOUND",
                f"path does not exist: {self._display_path(root, target)}",
            )
            return {**base, "cwd": str(root), "kind": "error", "size": None, "mime_type": None, "text": None, "truncated": False, "error": error.as_dict()}
        if target.is_dir():
            error = FileBrowserError(
                "IS_DIRECTORY",
                f"path is a directory: {self._display_path(root, target)}",
            )
            return {**base, "cwd": str(root), "kind": "directory", "size": None, "mime_type": None, "text": None, "truncated": False, "error": error.as_dict()}

        try:
            stat = target.stat()
            with target.open("rb") as handle:
                sample = handle.read(self.preview_bytes + 1)
        except OSError as exc:
            error = FileBrowserError("READ_FAILED", str(exc))
            return {**base, "cwd": str(root), "kind": "error", "size": None, "mime_type": None, "text": None, "truncated": False, "error": error.as_dict()}

        mime_type = guess_mime_type(target)
        dimensions = image_dimensions_from_bytes(sample, target.suffix.lower())
        is_image = is_image_path(target, mime_type)
        is_gif = target.suffix.lower() == ".gif" or sample.startswith(b"GIF8")
        text = decode_text_preview(sample[: self.preview_bytes], target, mime_type)
        truncated = stat.st_size > self.preview_bytes
        return {
            **base,
            "cwd": str(root),
            "path": self._display_path(root, target),
            "kind": "file",
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "extension": target.suffix.lower(),
            "mime_type": mime_type,
            "is_text": text is not None,
            "is_image": is_image,
            "is_gif": is_gif,
            "image_width": dimensions[0] if dimensions else None,
            "image_height": dimensions[1] if dimensions else None,
            "text": text,
            "truncated": truncated,
            "error": None,
        }

    def _base_response(self, agent: dict[str, Any], *, path: str) -> dict[str, Any]:
        return {
            "agent_id": str(agent.get("agent_id") or ""),
            "cwd": None,
            "path": path or ".",
        }

    def _agent_root(self, agent: dict[str, Any]) -> Path | FileBrowserError:
        metadata = agent.get("metadata")
        cwd = metadata.get("cwd") if isinstance(metadata, dict) else None
        if not isinstance(cwd, str) or not cwd.strip():
            return FileBrowserError(
                "AGENT_CWD_MISSING",
                "agent metadata does not include an absolute cwd",
                "Register the agent with metadata.cwd set to its project directory.",
            )
        cwd_path = Path(cwd).expanduser()
        if not cwd_path.is_absolute():
            return FileBrowserError(
                "AGENT_CWD_NOT_ABSOLUTE",
                f"agent cwd is not absolute: {cwd}",
                "Register the agent with an absolute metadata.cwd.",
            )
        try:
            root = cwd_path.resolve(strict=True)
        except OSError:
            return FileBrowserError(
                "AGENT_CWD_UNAVAILABLE",
                f"agent cwd does not exist or is not accessible: {cwd}",
            )
        if not root.is_dir():
            return FileBrowserError(
                "AGENT_CWD_NOT_DIRECTORY",
                f"agent cwd is not a directory: {cwd}",
            )
        return root

    def _resolve_agent_path(
        self, root: Path, requested: str
    ) -> Path | FileBrowserError:
        clean = (requested or ".").strip() or "."
        requested_path = Path(clean)
        if requested_path.is_absolute():
            return FileBrowserError("PATH_OUTSIDE_CWD", "absolute paths are not allowed")
        if any(part == ".." for part in requested_path.parts):
            return FileBrowserError("PATH_OUTSIDE_CWD", "parent path traversal is not allowed")
        try:
            resolved = (root / requested_path).resolve(strict=False)
        except OSError as exc:
            return FileBrowserError("PATH_INVALID", str(exc))
        if not self._is_inside(root, resolved):
            return FileBrowserError("PATH_OUTSIDE_CWD", "path resolves outside the agent cwd")
        return resolved

    def _entry(self, root: Path, path: Path, resolved: Path) -> dict[str, Any]:
        try:
            stat = path.stat()
        except OSError:
            stat = os.stat_result((0,) * 10)
        if resolved.is_dir():
            kind = "directory"
        elif resolved.is_file():
            kind = "file"
        else:
            kind = "other"
        mime_type = guess_mime_type(path)
        return {
            "name": path.name,
            "path": self._display_path(root, resolved),
            "kind": kind,
            "size": stat.st_size if kind == "file" else None,
            "mtime": stat.st_mtime,
            "extension": path.suffix.lower(),
            "mime_type": mime_type,
            "is_text": kind == "file" and is_text_path(path, mime_type),
            "is_image": kind == "file" and is_image_path(path, mime_type),
            "is_gif": kind == "file" and path.suffix.lower() == ".gif",
        }

    def _display_path(self, root: Path, path: Path) -> str:
        try:
            relative = path.relative_to(root)
        except ValueError:
            return "."
        text = relative.as_posix()
        return text if text else "."

    def _parent_path(self, root: Path, target: Path) -> str | None:
        if target == root:
            return None
        return self._display_path(root, target.parent)

    def _is_inside(self, root: Path, path: Path) -> bool:
        try:
            path.relative_to(root)
        except ValueError:
            return False
        return True


def guess_mime_type(path: Path) -> str | None:
    mime_type, _encoding = mimetypes.guess_type(path.name)
    if mime_type:
        return mime_type
    if path.suffix.lower() in TEXT_EXTENSIONS:
        return "text/plain"
    if path.suffix.lower() in IMAGE_EXTENSIONS:
        return f"image/{path.suffix.lower().lstrip('.')}"
    return None


def is_text_path(path: Path, mime_type: str | None) -> bool:
    suffix = path.suffix.lower()
    return suffix in TEXT_EXTENSIONS or bool(mime_type and mime_type.startswith("text/"))


def is_image_path(path: Path, mime_type: str | None) -> bool:
    suffix = path.suffix.lower()
    return suffix in IMAGE_EXTENSIONS or bool(mime_type and mime_type.startswith("image/"))


def decode_text_preview(data: bytes, path: Path, mime_type: str | None) -> str | None:
    if not data:
        return "" if is_text_path(path, mime_type) else None
    if b"\x00" in data[:4096]:
        return None
    encodings = ("utf-8", "utf-16")
    if not is_text_path(path, mime_type):
        encodings = ("utf-8",)
    for encoding in encodings:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def image_dimensions_from_bytes(
    data: bytes, suffix: str
) -> tuple[int, int] | None:
    if data.startswith(b"GIF8") and len(data) >= 10:
        width, height = struct.unpack("<HH", data[6:10])
        return int(width), int(height)
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return int(width), int(height)
    if suffix in {".jpg", ".jpeg"}:
        return jpeg_dimensions(data)
    return None


def jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    if not data.startswith(b"\xff\xd8"):
        return None
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        index += 2
        if marker in {0xD8, 0xD9}:
            continue
        if index + 2 > len(data):
            return None
        length = int.from_bytes(data[index : index + 2], "big")
        if length < 2 or index + length > len(data):
            return None
        if 0xC0 <= marker <= 0xC3 and length >= 7:
            height = int.from_bytes(data[index + 3 : index + 5], "big")
            width = int.from_bytes(data[index + 5 : index + 7], "big")
            return width, height
        index += length
    return None
