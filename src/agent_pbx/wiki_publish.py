from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
from typing import Any, Mapping, Sequence
from urllib.parse import quote


WIKI_STAGE_API_VERSION = "agent-pbx.wiki-stage/v1"
WIKI_MANIFEST_NAME = "manifest.json"
WIKI_PAGES_DIR = "pages"
WIKI_ARCHIVE_NAME = "wiki-pages.tar"
WIKI_DEFAULT_REPOSITORY_URL = "https://github.com/m4xx3d0ut/agent-pbx"
WIKI_PAGE_ORDER = (
    "Home",
    "Getting-Started",
    "Concepts-and-Architecture",
    "TUI-and-Keymap",
    "Agents-Operators-and-Runtimes",
    "Runtime-and-Tmux-Operations",
    "Models-Profiles-and-Codex",
    "WorkerBee-Integration",
    "Joplin-Notes",
    "Security-and-Remote-Access",
    "Troubleshooting-and-Recovery",
    "Packaging-Releases-and-Upgrades",
)
MARKDOWN_LINK_PATTERN = re.compile(r"(!?\[[^\]]*\])\(([^)]+)\)")
PUBLIC_SOURCE_PATTERNS = (
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key material"),
    (re.compile(r"(?i)\b(?:password|token|secret)\s*=\s*[^<\s][^\s]*"), "credential assignment"),
    (re.compile(r"/home/[A-Za-z0-9_.-]+/"), "absolute home path"),
    (re.compile(r"(?i)\.agent-pbx-joplin-e2ee-password"), "private password-file path"),
)


@dataclass(frozen=True, slots=True)
class WikiSourceCheck:
    ready: bool
    source: str
    pages: tuple[str, ...]
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


def check_wiki_source(source: Path) -> WikiSourceCheck:
    root = source.expanduser().resolve()
    errors: list[str] = []
    warnings: list[str] = []
    if not root.is_dir():
        return WikiSourceCheck(False, str(root), (), ("wiki source is not a directory",), ())
    pages = tuple(
        sorted(
            path.name
            for path in root.glob("*.md")
            if path.name != "README.md" and not path.name.startswith("_")
        )
    )
    if "Home.md" not in pages:
        errors.append("Home.md is required")
    if not pages:
        errors.append("wiki source has no publishable Markdown pages")
    repository_root = root.parent.parent
    for name in (*pages, "README.md"):
        path = root / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if not text.endswith("\n"):
            warnings.append(f"{name}: missing final newline")
        for pattern, label in PUBLIC_SOURCE_PATTERNS:
            if pattern.search(text):
                errors.append(f"{name}: contains {label}")
        for _label, raw_target in MARKDOWN_LINK_PATTERN.findall(text):
            target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
            if not target or target.startswith(("#", "http://", "https://", "mailto:")):
                continue
            path_part = target.split("#", 1)[0].split("?", 1)[0]
            resolved = (path.parent / path_part).resolve(strict=False)
            try:
                resolved.relative_to(repository_root)
            except ValueError:
                errors.append(f"{name}: link escapes repository: {raw_target}")
                continue
            if not resolved.exists():
                errors.append(f"{name}: missing local link target: {raw_target}")
    return WikiSourceCheck(
        not errors,
        str(root),
        pages,
        tuple(sorted(set(errors))),
        tuple(sorted(set(warnings))),
    )


def stage_wiki(
    source: Path,
    output: Path,
    *,
    repository_url: str = WIKI_DEFAULT_REPOSITORY_URL,
    revision: str = "main",
) -> dict[str, Any]:
    check = check_wiki_source(source)
    if not check.ready:
        raise ValueError("wiki source check failed: " + "; ".join(check.errors))
    source_root = Path(check.source)
    repository_root = source_root.parent.parent
    destination = output.expanduser().resolve()
    if destination == source_root or source_root in destination.parents:
        raise ValueError("wiki staging output must be outside the source directory")
    _prepare_stage_destination(destination)
    pages_root = destination / WIKI_PAGES_DIR
    pages_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    page_names: list[str] = []
    for name in check.pages:
        source_path = source_root / name
        rendered = _render_page_for_wiki(
            source_path.read_text(encoding="utf-8"),
            page=source_path,
            source_root=source_root,
            repository_root=repository_root,
            repository_url=repository_url.rstrip("/"),
            revision=revision,
        )
        destination_path = pages_root / name
        destination_path.write_text(rendered, encoding="utf-8")
        destination_path.chmod(0o600)
        page_names.append(name)
    sidebar = _sidebar(page_names)
    (pages_root / "_Sidebar.md").write_text(sidebar, encoding="utf-8")
    (pages_root / "_Sidebar.md").chmod(0o600)
    published = tuple(sorted((*page_names, "_Sidebar.md")))
    checksums = {
        name: hashlib.sha256((pages_root / name).read_bytes()).hexdigest()
        for name in published
    }
    digest_payload = json.dumps(checksums, sort_keys=True, separators=(",", ":"))
    stage_digest = hashlib.sha256(digest_payload.encode()).hexdigest()
    manifest: dict[str, Any] = {
        "api_version": WIKI_STAGE_API_VERSION,
        "source": str(source_root),
        "repository_url": repository_url.rstrip("/"),
        "revision": revision,
        "pages": list(published),
        "checksums": checksums,
        "stage_digest": stage_digest,
        "unknown_remote_pages_preserved": True,
    }
    _write_private_json(destination / WIKI_MANIFEST_NAME, manifest)
    _write_deterministic_tar(destination / WIKI_ARCHIVE_NAME, pages_root, published)
    return {**manifest, "output": str(destination), "archive": str(destination / WIKI_ARCHIVE_NAME)}


def publish_wiki_stage(
    stage_root: Path,
    *,
    remote: str,
    confirm: str,
    branch: str = "master",
    work_root: Path | None = None,
) -> dict[str, Any]:
    stage = stage_root.expanduser().resolve()
    manifest = _load_manifest(stage)
    expected = str(manifest["stage_digest"])
    if confirm != expected:
        raise ValueError("--confirm must exactly match the staged wiki digest")
    _verify_stage(stage, manifest)
    remote_value = str(remote or "").strip()
    if not remote_value:
        raise ValueError("wiki remote is required")
    if "://" in remote_value or remote_value.startswith("git@"):
        if not remote_value.rstrip("/").endswith(".wiki.git"):
            raise ValueError("external wiki remote must end with .wiki.git")
    parent = work_root.expanduser().resolve() if work_root else None
    if parent is not None:
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="agent-pbx-wiki-", dir=parent) as temporary:
        checkout = Path(temporary) / "wiki"
        cloned = subprocess.run(
            ["git", "clone", "--quiet", remote_value, str(checkout)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if cloned.returncode != 0:
            raise RuntimeError((cloned.stderr or cloned.stdout or "wiki clone failed").strip())
        pages_root = stage / WIKI_PAGES_DIR
        for name in manifest["pages"]:
            shutil.copyfile(pages_root / name, checkout / name)
        subprocess.run(["git", "-C", str(checkout), "add", "--", *manifest["pages"]], check=True)
        status = subprocess.run(
            ["git", "-C", str(checkout), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        if not status:
            return {
                "api_version": WIKI_STAGE_API_VERSION,
                "published": False,
                "unchanged": True,
                "stage_digest": expected,
            }
        subprocess.run(
            ["git", "-C", str(checkout), "config", "user.name", "Agent PBX Wiki Publisher"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(checkout), "config", "user.email", "agent-pbx@localhost"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(checkout), "commit", "--quiet", "-m", f"Publish Agent PBX wiki {expected[:12]}"],
            check=True,
        )
        pushed = subprocess.run(
            ["git", "-C", str(checkout), "push", "--quiet", "origin", f"HEAD:{branch}"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if pushed.returncode != 0:
            raise RuntimeError((pushed.stderr or pushed.stdout or "wiki push failed").strip())
    return {
        "api_version": WIKI_STAGE_API_VERSION,
        "published": True,
        "unchanged": False,
        "stage_digest": expected,
        "remote": remote_value,
        "branch": branch,
        "published_pages": list(manifest["pages"]),
        "unknown_remote_pages_preserved": True,
    }


def _prepare_stage_destination(destination: Path) -> None:
    if destination.exists():
        manifest_path = destination / WIKI_MANIFEST_NAME
        if not manifest_path.is_file():
            raise ValueError("refusing to replace a staging directory Agent PBX does not own")
        manifest = _load_manifest(destination)
        if manifest.get("api_version") != WIKI_STAGE_API_VERSION:
            raise ValueError("refusing to replace an unsupported wiki staging directory")
        shutil.rmtree(destination / WIKI_PAGES_DIR, ignore_errors=True)
        (destination / WIKI_ARCHIVE_NAME).unlink(missing_ok=True)
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.chmod(0o700)


def _render_page_for_wiki(
    text: str,
    *,
    page: Path,
    source_root: Path,
    repository_root: Path,
    repository_url: str,
    revision: str,
) -> str:
    def replace(match: re.Match[str]) -> str:
        label, raw_target = match.groups()
        target = raw_target.strip()
        if not target or target.startswith(("#", "http://", "https://", "mailto:")):
            return match.group(0)
        path_target, separator, anchor = target.partition("#")
        resolved = (page.parent / path_target).resolve(strict=False)
        suffix = f"#{anchor}" if separator else ""
        try:
            relative_wiki = resolved.relative_to(source_root)
        except ValueError:
            relative_wiki = None
        if relative_wiki is not None and resolved.suffix.lower() == ".md":
            return f"{label}({relative_wiki.with_suffix('').as_posix()}{suffix})"
        relative_repo = resolved.relative_to(repository_root)
        route = "raw" if label.startswith("!") else "blob"
        encoded = "/".join(quote(part) for part in relative_repo.parts)
        return f"{label}({repository_url}/{route}/{quote(revision)}/{encoded}{suffix})"

    rendered = MARKDOWN_LINK_PATTERN.sub(replace, text)
    return rendered if rendered.endswith("\n") else rendered + "\n"


def _sidebar(page_names: Sequence[str]) -> str:
    available = {Path(name).stem for name in page_names}
    ordered = [name for name in WIKI_PAGE_ORDER if name in available]
    ordered.extend(sorted(available - set(ordered)))
    labels = {name: name.replace("-", " ") for name in ordered}
    labels["Home"] = "Agent PBX Home"
    return "# Agent PBX\n\n" + "\n".join(
        f"- [{labels[name]}]({name})" for name in ordered
    ) + "\n"


def _write_private_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def _write_deterministic_tar(
    archive: Path,
    pages_root: Path,
    pages: Sequence[str],
) -> None:
    with tarfile.open(archive, "w", format=tarfile.USTAR_FORMAT) as handle:
        for name in sorted(pages):
            data = (pages_root / name).read_bytes()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = 0
            handle.addfile(info, io.BytesIO(data))
    archive.chmod(0o600)


def _load_manifest(stage: Path) -> dict[str, Any]:
    try:
        payload = json.loads((stage / WIKI_MANIFEST_NAME).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        raise ValueError("wiki stage manifest is missing or invalid") from exc
    if not isinstance(payload, dict) or payload.get("api_version") != WIKI_STAGE_API_VERSION:
        raise ValueError("wiki stage manifest contract is unsupported")
    return payload


def _verify_stage(stage: Path, manifest: Mapping[str, Any]) -> None:
    pages = manifest.get("pages")
    checksums = manifest.get("checksums")
    if not isinstance(pages, list) or not isinstance(checksums, dict):
        raise ValueError("wiki stage manifest is incomplete")
    for name in pages:
        if not isinstance(name, str) or Path(name).name != name or not name.endswith(".md"):
            raise ValueError("wiki stage contains an unsafe page name")
        path = stage / WIKI_PAGES_DIR / name
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != checksums.get(name):
            raise ValueError(f"wiki stage checksum changed: {name}")
    digest_payload = json.dumps(checksums, sort_keys=True, separators=(",", ":"))
    if hashlib.sha256(digest_payload.encode()).hexdigest() != manifest.get("stage_digest"):
        raise ValueError("wiki stage digest is invalid")
