from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import threading
import time
from typing import Any, Callable, Mapping

from .workerbee_mcp import (
    WorkerBeeBackendProvenance,
    inspect_workerbee_mcp,
    inspect_workerbee_switcher,
)


WORKERBEE_SWITCH_API_VERSION = "agent-pbx.workerbee-switch/v1"
WORKERBEE_SWITCH_BIN_ENV = "AGENT_PBX_WORKERBEE_SWITCHER_BIN"
WORKERBEE_SWITCH_STATUS_BIN_ENV = "AGENT_PBX_WORKERBEE_SWITCHER_STATUS_BIN"
WORKERBEE_SWITCH_TARGETS = ("oss", "platform_debug")
TRANSACTION_ID_PATTERN = re.compile(r"^[a-f0-9]{24}$")
DEFAULT_PREVIEW_TTL_SECONDS = 15 * 60


def _canonical_backend(value: object) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    if normalized in {"oss", "oss_local", "local", "workerbee_oss"}:
        return "oss"
    if normalized in {"debug", "platform", "platform_debug", "platform_admin"}:
        return "platform_debug"
    return normalized


def _provenance_dict(value: WorkerBeeBackendProvenance) -> dict[str, Any]:
    result = value.public_dict()
    result["backend"] = _canonical_backend(result.get("backend"))
    return result


def _fingerprint(value: Mapping[str, Any]) -> str:
    selected = {
        key: value.get(key)
        for key in ("backend", "endpoint", "server_name", "server_version", "config_path")
    }
    payload = json.dumps(selected, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _atomic_private_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
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


class WorkerBeeSwitchService:
    """Guard WorkerBee backend changes with preview-bound transactions."""

    def __init__(
        self,
        *,
        state_root: Path,
        switch_bin: str | Path | None = None,
        status_bin: str | Path | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        capability_probe: Callable[[str], Mapping[str, Any]] | None = None,
        cache_invalidator: Callable[[], None] | None = None,
        preview_ttl_seconds: int = DEFAULT_PREVIEW_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self.state_root = state_root.expanduser().resolve()
        self.runner = runner
        self.clock = clock
        self.preview_ttl_seconds = max(30, int(preview_ttl_seconds))
        self.cache_invalidator = cache_invalidator or (lambda: None)
        self.capability_probe = capability_probe or self._default_capability_probe
        configured_switch = str(
            switch_bin or os.getenv(WORKERBEE_SWITCH_BIN_ENV) or ""
        ).strip()
        configured_status = str(
            status_bin or os.getenv(WORKERBEE_SWITCH_STATUS_BIN_ENV) or ""
        ).strip()
        self.switch_bin = configured_switch or str(which("wb-mcp-switch") or "")
        self.status_bin = configured_status or str(which("wb-mcp-status") or "")
        self._lock = threading.Lock()

    def status(self) -> dict[str, Any]:
        provenance = self._inspect()
        return {
            "api_version": WORKERBEE_SWITCH_API_VERSION,
            "available": bool(self.switch_bin and self.status_bin),
            "targets": list(WORKERBEE_SWITCH_TARGETS),
            "active": _provenance_dict(provenance),
            "last_transaction": self._last_transaction_summary(),
            "existing_sessions_pinned": True,
        }

    def preview(self, target: str) -> dict[str, Any]:
        resolved_target = self._target(target)
        with self._lock:
            self._require_tools()
            before = _provenance_dict(self._inspect())
            if not before.get("ready"):
                raise ValueError(
                    "WorkerBee status is not ready; repair the active backend before switching"
                )
            plan = self._run_switch(resolved_target, dry_run=True)
            now = self.clock()
            transaction_id = secrets.token_hex(12)
            preview_token = secrets.token_urlsafe(24)
            record: dict[str, Any] = {
                "api_version": WORKERBEE_SWITCH_API_VERSION,
                "transaction_id": transaction_id,
                "preview_token": preview_token,
                "phase": "previewed",
                "target": resolved_target,
                "created_at": now,
                "expires_at": now + self.preview_ttl_seconds,
                "before": before,
                "before_fingerprint": _fingerprint(before),
                "plan_digest": hashlib.sha256(plan.encode()).hexdigest(),
                "plan_summary": self._plan_summary(plan),
                "requires_privileged_confirmation": resolved_target == "platform_debug",
                "existing_sessions_pinned": True,
                "future_sessions_only": True,
            }
            self._save(record)
            return dict(record)

    def apply(
        self,
        transaction_id: str,
        *,
        preview_token: str,
        confirm_target: str,
        confirm_privileged: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            record = self._load(transaction_id)
            self._validate_preview(
                record,
                preview_token=preview_token,
                confirm_target=confirm_target,
                confirm_privileged=confirm_privileged,
            )
            before_now = _provenance_dict(self._inspect())
            if _fingerprint(before_now) != record["before_fingerprint"]:
                raise ValueError(
                    "WorkerBee backend changed after preview; create a fresh switch preview"
                )
            target = str(record["target"])
            if _canonical_backend(before_now.get("backend")) == target:
                record.update(
                    phase="verified_noop",
                    applied_at=self.clock(),
                    after=before_now,
                    capability={"ready": True, "code": "UNCHANGED_BACKEND"},
                )
                self._save(record)
                return self._public_record(record)
            try:
                self._run_switch(target, dry_run=False)
            except Exception as exc:  # noqa: BLE001 - preserve recovery evidence
                current = _provenance_dict(self._inspect())
                unchanged = _fingerprint(current) == record["before_fingerprint"]
                rollback = (
                    {"verified": True, "unchanged": True, "after": current}
                    if unchanged
                    else self._safe_restore_backend(
                        _canonical_backend(record["before"].get("backend"))
                    )
                )
                record.update(
                    phase=(
                        "apply_failed_unchanged"
                        if unchanged
                        else (
                            "failed_rolled_back"
                            if rollback.get("verified")
                            else "failed_rollback_failed"
                        )
                    ),
                    failed_at=self.clock(),
                    failure=f"{type(exc).__name__}: {exc}",
                    automatic_rollback=rollback,
                )
                self._save(record)
                return self._public_record(record)
            result = self._verify_target(target)
            if not result["verified"]:
                rollback = self._safe_restore_backend(
                    _canonical_backend(record["before"].get("backend"))
                )
                record.update(
                    phase=(
                        "failed_rolled_back"
                        if rollback.get("verified")
                        else "failed_rollback_failed"
                    ),
                    failed_at=self.clock(),
                    after=result.get("after"),
                    capability=result.get("capability"),
                    failure=result.get("message"),
                    automatic_rollback=rollback,
                )
                self._save(record)
                return self._public_record(record)
            record.update(
                phase="verified",
                applied_at=self.clock(),
                after=result["after"],
                capability=result["capability"],
                rollback_backend=_canonical_backend(record["before"].get("backend")),
            )
            self._save(record)
            self.cache_invalidator()
            return self._public_record(record)

    def rollback(
        self,
        transaction_id: str,
        *,
        confirm: bool,
    ) -> dict[str, Any]:
        if not confirm:
            raise ValueError("rollback requires explicit confirmation")
        with self._lock:
            record = self._load(transaction_id)
            if record.get("phase") not in {"verified", "verified_noop"}:
                raise ValueError("only a verified switch transaction can be rolled back")
            if record.get("rolled_back_at"):
                raise ValueError("switch transaction was already rolled back")
            backend = _canonical_backend(record.get("rollback_backend"))
            if not backend:
                backend = _canonical_backend(
                    (record.get("before") or {}).get("backend")
                )
            result = self._restore_backend(backend)
            record.update(
                phase="rolled_back" if result.get("verified") else "rollback_failed",
                rolled_back_at=self.clock(),
                rollback=result,
            )
            self._save(record)
            if result.get("verified"):
                self.cache_invalidator()
            return self._public_record(record)

    def _restore_backend(self, backend: str) -> dict[str, Any]:
        if backend not in WORKERBEE_SWITCH_TARGETS:
            return {
                "verified": False,
                "message": f"prior backend {backend or '-'} is not switchable",
            }
        self._run_switch(backend, dry_run=False)
        return self._verify_target(backend)

    def _safe_restore_backend(self, backend: str) -> dict[str, Any]:
        try:
            return self._restore_backend(backend)
        except Exception as exc:  # noqa: BLE001 - persisted rollback diagnostic
            return {
                "verified": False,
                "message": f"rollback failed ({type(exc).__name__})",
            }

    def _verify_target(self, target: str) -> dict[str, Any]:
        after = _provenance_dict(self._inspect())
        backend_ready = bool(
            after.get("ready")
            and _canonical_backend(after.get("backend")) == target
            and after.get("endpoint")
        )
        capability: Mapping[str, Any] = {
            "ready": False,
            "code": "WORKERBEE_SWITCH_NOT_PROBED",
        }
        if backend_ready:
            try:
                capability = dict(self.capability_probe(str(after["endpoint"])))
            except Exception as exc:  # noqa: BLE001 - transaction records the failure
                capability = {
                    "ready": False,
                    "code": "WORKERBEE_SWITCH_CAPABILITY_FAILED",
                    "message": type(exc).__name__,
                }
        verified = backend_ready and bool(capability.get("ready"))
        return {
            "verified": verified,
            "after": after,
            "capability": dict(capability),
            "message": (
                "WorkerBee backend and bootstrap capabilities verified."
                if verified
                else "WorkerBee backend or bootstrap capability verification failed."
            ),
        }

    def _run_switch(self, target: str, *, dry_run: bool) -> str:
        command = [
            self.switch_bin,
            "switch-debug" if target == "platform_debug" else "switch-oss",
        ]
        if dry_run:
            command.append("--dry-run")
        command.append("--json")
        if target == "platform_debug":
            command.append("--confirm-platform-debug")
        try:
            result = self.runner(
                command,
                capture_output=True,
                text=True,
                timeout=180 if not dry_run else 45,
            )
        except Exception as exc:
            raise RuntimeError(
                f"WorkerBee switch command failed ({type(exc).__name__})"
            ) from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "switch command failed").strip()
            raise RuntimeError(f"WorkerBee switch command exited {result.returncode}: {detail}")
        return str(result.stdout or result.stderr or "").strip()

    def _inspect(self) -> WorkerBeeBackendProvenance:
        return inspect_workerbee_switcher(
            status_bin=self.status_bin or None,
            runner=self.runner,
        )

    @staticmethod
    def _default_capability_probe(endpoint: str) -> Mapping[str, Any]:
        result = asyncio.run(inspect_workerbee_mcp(endpoint))
        return result.public_dict()

    @staticmethod
    def _target(target: str) -> str:
        resolved = _canonical_backend(target)
        if resolved not in WORKERBEE_SWITCH_TARGETS:
            raise ValueError(
                f"unsupported WorkerBee backend {target!r}; choose oss or platform_debug"
            )
        return resolved

    def _require_tools(self) -> None:
        if not self.switch_bin:
            raise ValueError("wb-mcp-switch is not configured")
        if not self.status_bin:
            raise ValueError("wb-mcp-status is not configured")

    def _validate_preview(
        self,
        record: Mapping[str, Any],
        *,
        preview_token: str,
        confirm_target: str,
        confirm_privileged: bool,
    ) -> None:
        if record.get("phase") != "previewed":
            raise ValueError("switch preview is no longer applicable")
        if not secrets.compare_digest(
            str(record.get("preview_token") or ""), str(preview_token or "")
        ):
            raise ValueError("switch preview token does not match")
        if self.clock() > float(record.get("expires_at") or 0):
            raise ValueError("switch preview expired; create a fresh preview")
        target = str(record.get("target") or "")
        if self._target(confirm_target) != target:
            raise ValueError("confirmed backend does not match the preview")
        if target == "platform_debug" and not confirm_privileged:
            raise ValueError("platform_debug requires privileged-backend confirmation")

    def _receipt_path(self, transaction_id: str) -> Path:
        if not TRANSACTION_ID_PATTERN.fullmatch(transaction_id):
            raise ValueError("invalid WorkerBee switch transaction id")
        return self.state_root / "transactions" / f"{transaction_id}.json"

    def _save(self, record: Mapping[str, Any]) -> None:
        _atomic_private_json(
            self._receipt_path(str(record["transaction_id"])), record
        )
        _atomic_private_json(
            self.state_root / "last-transaction.json",
            {
                "api_version": WORKERBEE_SWITCH_API_VERSION,
                "transaction_id": record["transaction_id"],
                "phase": record.get("phase"),
                "target": record.get("target"),
                "before_backend": _canonical_backend(
                    (record.get("before") or {}).get("backend")
                ),
                "rollback_backend": record.get("rollback_backend"),
                "updated_at": self.clock(),
            },
        )

    def _load(self, transaction_id: str) -> dict[str, Any]:
        path = self._receipt_path(transaction_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError("WorkerBee switch transaction was not found") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("WorkerBee switch transaction receipt is unreadable") from exc
        if not isinstance(payload, dict) or payload.get("api_version") != WORKERBEE_SWITCH_API_VERSION:
            raise ValueError("WorkerBee switch transaction contract is unsupported")
        return payload

    def _last_transaction_summary(self) -> dict[str, Any] | None:
        path = self.state_root / "last-transaction.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _plan_summary(output: str) -> list[str]:
        lines = [" ".join(line.strip().split()) for line in output.splitlines()]
        return [line[:240] for line in lines if line][:12]

    @staticmethod
    def _public_record(record: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(record)
        result.pop("preview_token", None)
        return result
