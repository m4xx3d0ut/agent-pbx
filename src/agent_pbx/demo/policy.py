from __future__ import annotations

from contextlib import contextmanager
import ipaddress
import os
from pathlib import Path
import stat
from typing import Iterator, Mapping
from urllib.parse import urlparse


class PublicDemoPolicyError(ValueError):
    """Raised when the browser demo would cross its public isolation boundary."""


SENSITIVE_ENV_NAMES = frozenset(
    {
        "AGENT_PBX_TOKEN",
        "AGENT_PBX_SERVER",
        "AGENT_PBX_SERVER_URL",
        "AGENT_PBX_JOPLIN_TOKEN",
        "AGENT_PBX_JOPLIN_E2EE_PASSWORD",
        "CODEX_API_KEY",
        "DOCKER_HOST",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "KUBECONFIG",
        "OPENAI_API_KEY",
        "SSH_AGENT_PID",
        "SSH_AUTH_SOCK",
        "TMUX",
        "TMUX_PANE",
    }
)
SENSITIVE_ENV_PREFIXES = (
    "AWS_",
    "AZURE_",
    "CLOUDFLARE_",
    "DIGITALOCEAN_",
    "GCP_",
    "GOOGLE_",
    "JOPLIN_",
    "K1S_",
    "KUBE_",
    "NATS_",
    "OPENAI_",
    "TF_VAR_",
    "VAULT_",
    "WORKERBEE_",
)
SAFE_HOST_ENV_NAMES = frozenset(
    {
        "COLORTERM",
        "LANG",
        "LANGUAGE",
        "LC_ALL",
        "LC_CTYPE",
        "PATH",
        "TERM",
        "TZ",
    }
)


def sensitive_environment_names(environ: Mapping[str, str]) -> tuple[str, ...]:
    """List credential/runtime variables that may never enter a demo child."""

    names = {
        name
        for name, value in environ.items()
        if value
        and (
            name in SENSITIVE_ENV_NAMES
            or any(name.startswith(prefix) for prefix in SENSITIVE_ENV_PREFIXES)
            or name.endswith("_TOKEN")
            or name.endswith("_PASSWORD")
            or name.endswith("_SECRET")
            or name.endswith("_API_KEY")
        )
    }
    return tuple(sorted(names))


def build_public_demo_environment(
    environ: Mapping[str, str],
    *,
    home: Path,
    runtime_dir: Path,
    path_prefix: Path | None = None,
) -> dict[str, str]:
    """Build an allowlisted child environment rooted in disposable paths."""

    result = {
        name: str(environ[name])
        for name in SAFE_HOST_ENV_NAMES
        if str(environ.get(name) or "").strip()
    }
    path = str(result.get("PATH") or os.defpath)
    if path_prefix is not None:
        path = f"{path_prefix}{os.pathsep}{path}"
    result.update(
        {
            "AGENT_PBX_PUBLIC_DEMO": "1",
            "HOME": str(home),
            "PATH": path,
            "TERM": str(result.get("TERM") or "xterm-256color"),
            "COLORTERM": str(result.get("COLORTERM") or "truecolor"),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local" / "share"),
            "XDG_RUNTIME_DIR": str(runtime_dir),
            "XDG_STATE_HOME": str(home / ".local" / "state"),
        }
    )
    validate_public_demo_environment(result, home=home, runtime_dir=runtime_dir)
    return result


def validate_public_demo_environment(
    environ: Mapping[str, str],
    *,
    home: Path,
    runtime_dir: Path,
) -> None:
    """Fail if a child environment can reach host credentials or tmux state."""

    leaked = sensitive_environment_names(environ)
    if leaked:
        raise PublicDemoPolicyError(
            "public demo environment contains forbidden values: " + ", ".join(leaked)
        )
    resolved_home = Path(str(environ.get("HOME") or "")).resolve(strict=False)
    resolved_runtime = Path(str(environ.get("XDG_RUNTIME_DIR") or "")).resolve(
        strict=False
    )
    if resolved_home != home.resolve(strict=False):
        raise PublicDemoPolicyError("public demo HOME is outside its session root")
    if resolved_runtime != runtime_dir.resolve(strict=False):
        raise PublicDemoPolicyError(
            "public demo XDG_RUNTIME_DIR is outside its session root"
        )


def ensure_private_directory(path: Path) -> Path:
    """Create a caller-owned, mode-0700 directory without following a symlink."""

    if path.exists() and (path.is_symlink() or not path.is_dir()):
        raise PublicDemoPolicyError(f"demo path is not a private directory: {path}")
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.chmod(0o700)
    details = path.stat()
    if hasattr(os, "getuid") and details.st_uid != os.getuid():
        raise PublicDemoPolicyError(f"demo path has a different owner: {path}")
    if details.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise PublicDemoPolicyError(f"demo path is accessible by another user: {path}")
    return path


def is_loopback_host(host: str) -> bool:
    normalized = host.strip().casefold()
    if normalized in {"localhost", "ip6-localhost"}:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def validate_web_binding(
    *,
    host: str,
    public_url: str | None,
    allowed_origins: tuple[str, ...],
    allow_public_bind: bool,
) -> tuple[str, tuple[str, ...]]:
    """Validate local development or explicit TLS reverse-proxy publication."""

    loopback = is_loopback_host(host)
    if not loopback and not allow_public_bind:
        raise PublicDemoPolicyError(
            "non-loopback demo binding requires --allow-public-bind"
        )
    if not public_url:
        if not loopback:
            raise PublicDemoPolicyError(
                "public demo binding requires an explicit HTTPS --public-url"
            )
        public_url = f"http://{host}"
    parsed = urlparse(public_url)
    if parsed.scheme not in ({"http", "https"} if loopback else {"https"}):
        raise PublicDemoPolicyError(
            "public demo URL must use HTTPS outside loopback"
        )
    if not parsed.netloc or parsed.username or parsed.password:
        raise PublicDemoPolicyError("public demo URL must be an absolute URL without credentials")
    normalized_url = public_url.rstrip("/")
    origins = tuple(origin.rstrip("/") for origin in allowed_origins if origin.strip())
    if not origins:
        origins = (normalized_url,)
    for origin in origins:
        candidate = urlparse(origin)
        if candidate.scheme not in {"http", "https"} or not candidate.netloc:
            raise PublicDemoPolicyError(f"invalid allowed origin: {origin}")
        if candidate.path not in {"", "/"} or candidate.query or candidate.fragment:
            raise PublicDemoPolicyError(
                f"allowed origin must not contain a path, query, or fragment: {origin}"
            )
    return normalized_url, origins


@contextmanager
def replaced_environment(environ: Mapping[str, str]) -> Iterator[None]:
    """Temporarily replace the process environment for an inherited child API."""

    original = dict(os.environ)
    os.environ.clear()
    os.environ.update({str(key): str(value) for key, value in environ.items()})
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(original)
