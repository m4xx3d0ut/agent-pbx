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
from .workerbee_runtime import canonical_workerbee_backend


WORKERBEE_SWITCH_API_VERSION = "agent-pbx.workerbee-switch/v1"
WORKERBEE_SWITCH_BIN_ENV = "AGENT_PBX_WORKERBEE_SWITCHER_BIN"
WORKERBEE_SWITCH_STATUS_BIN_ENV = "AGENT_PBX_WORKERBEE_SWITCHER_STATUS_BIN"
WORKERBEE_PLATFORM_DEBUG_POSTURE_ENV = (
    "AGENT_PBX_WORKERBEE_PLATFORM_DEBUG_POSTURE"
)
WORKERBEE_SWITCH_TARGETS = ("oss", "platform_debug")
WORKERBEE_PLATFORM_DEBUG_POSTURES = ("loopback", "native_lan")
DEFAULT_PLATFORM_DEBUG_POSTURE = "loopback"
TRANSACTION_ID_PATTERN = re.compile(r"^[a-f0-9]{24}$")
DEFAULT_PREVIEW_TTL_SECONDS = 15 * 60


def _canonical_backend(value: object) -> str:
    return canonical_workerbee_backend(value)


def _canonical_platform_debug_posture(value: object) -> str:
    resolved = str(value or DEFAULT_PLATFORM_DEBUG_POSTURE).strip().lower()
    resolved = resolved.replace("-", "_")
    aliases = {
        "lan": "native_lan",
        "native": "native_lan",
        "native_caddy": "native_lan",
    }
    resolved = aliases.get(resolved, resolved)
    if resolved not in WORKERBEE_PLATFORM_DEBUG_POSTURES:
        choices = ", ".join(WORKERBEE_PLATFORM_DEBUG_POSTURES)
        raise ValueError(
            f"unsupported WorkerBee platform-debug posture {value!r}; choose {choices}"
        )
    return resolved


def _provenance_dict(value: WorkerBeeBackendProvenance) -> dict[str, Any]:
    result = value.public_dict()
    result["backend"] = _canonical_backend(result.get("backend"))
    return result


def _fingerprint(value: Mapping[str, Any]) -> str:
    selected = {
        key: value.get(key)
        for key in (
            "backend",
            "endpoint",
            "server_name",
            "server_version",
            "config_path",
            "platform_debug_posture",
        )
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
        platform_debug_posture: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        capability_probe: Callable[[str], Mapping[str, Any]] | None = None,
        cache_invalidator: Callable[[], None] | None = None,
        impact_provider: Callable[[], Mapping[str, Any]] | None = None,
        selection_recorder: Callable[..., Mapping[str, Any] | None] | None = None,
        preview_ttl_seconds: int = DEFAULT_PREVIEW_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self.state_root = state_root.expanduser().resolve()
        self.runner = runner
        self.clock = clock
        self.preview_ttl_seconds = max(30, int(preview_ttl_seconds))
        self.cache_invalidator = cache_invalidator or (lambda: None)
        self.impact_provider = impact_provider or (lambda: {})
        self.selection_recorder = selection_recorder
        self.capability_probe = capability_probe or self._default_capability_probe
        self.platform_debug_posture = _canonical_platform_debug_posture(
            platform_debug_posture
            or os.getenv(WORKERBEE_PLATFORM_DEBUG_POSTURE_ENV)
            or DEFAULT_PLATFORM_DEBUG_POSTURE
        )
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
        active = self._current_state()
        impact = dict(self.impact_provider() or {})
        active_backend = _canonical_backend(active.get("backend"))
        observed_posture = str(active.get("platform_debug_posture") or "")
        posture_ready = bool(
            active_backend != "platform_debug"
            or observed_posture == self.platform_debug_posture
        )
        return {
            "api_version": WORKERBEE_SWITCH_API_VERSION,
            "available": bool(self.switch_bin and self.status_bin),
            "targets": list(WORKERBEE_SWITCH_TARGETS),
            "active": active,
            "platform_debug_posture": {
                "expected": self.platform_debug_posture,
                "observed": observed_posture,
                "ready": posture_ready,
            },
            "last_transaction": self._last_transaction_summary(),
            "existing_sessions_pinned": False,
            "generation_guard_required": True,
            "impact": impact,
            "backend_catalog": [
                {
                    "backend": "oss",
                    "available": True,
                    "workspace_strategy": "direct_host",
                },
                {
                    "backend": "user_appliance",
                    "available": False,
                    "workspace_strategy": "managed_appliance_volume",
                    "unavailable_reason": "Live user-appliance image qualification is deferred.",
                },
                {
                    "backend": "platform_debug",
                    "available": True,
                    "workspace_strategy": "content_addressed_import",
                    "posture": self.platform_debug_posture,
                },
            ],
        }

    def preview(self, target: str) -> dict[str, Any]:
        resolved_target = self._target(target)
        with self._lock:
            self._require_tools()
            before = self._current_state()
            repairable_posture_drift = bool(
                resolved_target == "platform_debug"
                and _canonical_backend(before.get("backend")) == "platform_debug"
                and before.get("endpoint")
                and before.get("platform_debug_posture")
                and before.get("platform_debug_posture")
                != self.platform_debug_posture
            )
            if not before.get("ready") and not repairable_posture_drift:
                raise ValueError(
                    "WorkerBee status is not ready; repair the active backend before switching"
                )
            impact = dict(self.impact_provider() or {})
            if int(impact.get("mutation_count") or 0) > 0:
                raise ValueError(
                    "WorkerBee backend switch is blocked by active mutating tool calls"
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
                "target_platform_debug_posture": (
                    self.platform_debug_posture
                    if resolved_target == "platform_debug"
                    else ""
                ),
                "created_at": now,
                "expires_at": now + self.preview_ttl_seconds,
                "before": before,
                "before_fingerprint": _fingerprint(before),
                "plan_digest": hashlib.sha256(plan.encode()).hexdigest(),
                "plan_summary": self._plan_summary(plan),
                "requires_privileged_confirmation": resolved_target == "platform_debug",
                "existing_sessions_pinned": False,
                "future_sessions_only": True,
                "generation_guard_required": True,
                "impact": impact,
                "repairs_posture_drift": repairable_posture_drift,
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
            before_now = self._current_state()
            impact = dict(self.impact_provider() or {})
            if int(impact.get("mutation_count") or 0) > 0:
                raise ValueError(
                    "WorkerBee backend switch is blocked by active mutating tool calls"
                )
            if _fingerprint(before_now) != record["before_fingerprint"]:
                raise ValueError(
                    "WorkerBee backend changed after preview; create a fresh switch preview"
                )
            target = str(record["target"])
            target_posture = str(
                record.get("target_platform_debug_posture") or ""
            )
            backend_unchanged = _canonical_backend(before_now.get("backend")) == target
            posture_unchanged = bool(
                target != "platform_debug"
                or before_now.get("platform_debug_posture") == target_posture
            )
            if backend_unchanged and posture_unchanged:
                record.update(
                    phase="verified_noop",
                    applied_at=self.clock(),
                    after=before_now,
                    capability={"ready": True, "code": "UNCHANGED_BACKEND"},
                )
                self._save(record)
                self._record_selection(before_now, reason="verified_noop", force_generation=False)
                return self._public_record(record)
            try:
                self._run_switch(target, dry_run=False)
            except Exception as exc:  # noqa: BLE001 - preserve recovery evidence
                current = self._current_state()
                unchanged = _fingerprint(current) == record["before_fingerprint"]
                rollback = (
                    {"verified": True, "unchanged": True, "after": current}
                    if unchanged
                    else self._safe_restore_backend(
                        _canonical_backend(record["before"].get("backend")),
                        platform_debug_posture=self.platform_debug_posture,
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
            result = self._verify_target(
                target,
                expected_platform_debug_posture=target_posture or None,
            )
            if not result["verified"]:
                rollback = self._safe_restore_backend(
                    _canonical_backend(record["before"].get("backend")),
                    platform_debug_posture=self.platform_debug_posture,
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
                platform_debug_posture=result["platform_debug_posture"],
                rollback_backend=_canonical_backend(record["before"].get("backend")),
                rollback_platform_debug_posture=(
                    self.platform_debug_posture
                    if _canonical_backend(record["before"].get("backend"))
                    == "platform_debug"
                    else ""
                ),
            )
            self._save(record)
            self.cache_invalidator()
            self._record_selection(result["after"], reason="switch", force_generation=True)
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
            result = self._restore_backend(
                backend,
                platform_debug_posture=str(
                    record.get("rollback_platform_debug_posture")
                    or (record.get("before") or {}).get("platform_debug_posture")
                    or ""
                ) or None,
            )
            record.update(
                phase="rolled_back" if result.get("verified") else "rollback_failed",
                rolled_back_at=self.clock(),
                rollback=result,
            )
            self._save(record)
            if result.get("verified"):
                self.cache_invalidator()
                self._record_selection(
                    result.get("after") or {},
                    reason="rollback",
                    force_generation=True,
                )
            return self._public_record(record)

    def _record_selection(
        self,
        provenance: Mapping[str, Any],
        *,
        reason: str,
        force_generation: bool,
    ) -> None:
        if self.selection_recorder is None:
            return
        self.selection_recorder(
            provenance,
            reason=reason,
            force_generation=force_generation,
        )

    def _restore_backend(
        self,
        backend: str,
        *,
        platform_debug_posture: str | None = None,
    ) -> dict[str, Any]:
        if backend not in WORKERBEE_SWITCH_TARGETS:
            return {
                "verified": False,
                "message": f"prior backend {backend or '-'} is not switchable",
            }
        effective_posture = (
            _canonical_platform_debug_posture(
                platform_debug_posture or self.platform_debug_posture
            )
            if backend == "platform_debug"
            else None
        )
        self._run_switch(backend, dry_run=False)
        return self._verify_target(
            backend,
            expected_platform_debug_posture=effective_posture,
        )

    def _safe_restore_backend(
        self,
        backend: str,
        *,
        platform_debug_posture: str | None = None,
    ) -> dict[str, Any]:
        try:
            return self._restore_backend(
                backend,
                platform_debug_posture=platform_debug_posture,
            )
        except Exception as exc:  # noqa: BLE001 - persisted rollback diagnostic
            return {
                "verified": False,
                "message": f"rollback failed ({type(exc).__name__})",
            }

    def _verify_target(
        self,
        target: str,
        *,
        expected_platform_debug_posture: str | None = None,
    ) -> dict[str, Any]:
        after = self._current_state()
        expected_posture = (
            _canonical_platform_debug_posture(expected_platform_debug_posture)
            if target == "platform_debug"
            else ""
        )
        posture_ready = bool(
            target != "platform_debug"
            or after.get("platform_debug_posture") == expected_posture
        )
        backend_ready = bool(
            after.get("ready")
            and _canonical_backend(after.get("backend")) == target
            and after.get("endpoint")
            and posture_ready
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
            "platform_debug_posture": {
                "expected": expected_posture,
                "observed": str(after.get("platform_debug_posture") or ""),
                "ready": posture_ready,
            },
            "message": (
                "WorkerBee backend and bootstrap capabilities verified."
                if verified
                else (
                    "WorkerBee platform-debug posture verification failed."
                    if target == "platform_debug" and not posture_ready
                    else "WorkerBee backend or bootstrap capability verification failed."
                )
            ),
        }

    def _run_switch(
        self,
        target: str,
        *,
        dry_run: bool,
    ) -> str:
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

    def _current_state(self) -> dict[str, Any]:
        provenance = _provenance_dict(self._inspect())
        payload = self._status_payload()
        status = payload.get("status") if isinstance(payload, dict) else {}
        config = payload.get("config") if isinstance(payload, dict) else {}
        if not isinstance(status, dict):
            status = {}
        if not isinstance(config, dict):
            config = {}
        if (
            status.get("api_version") == "workerbee.mcp_switch_status/v1"
            and config.get("api_version") == "workerbee.mcp_switch_config/v1"
            and status.get("backend")
            and status.get("mcp_endpoint")
        ):
            mcp = status.get("mcp") if isinstance(status.get("mcp"), dict) else {}
            server_info = (
                mcp.get("server_info")
                if isinstance(mcp.get("server_info"), dict)
                else {}
            )
            provenance.update(
                backend=_canonical_backend(status.get("backend")),
                endpoint=str(status.get("mcp_endpoint") or ""),
                server_name=str(server_info.get("name") or ""),
                server_version=str(server_info.get("version") or ""),
                config_path=str(config.get("path") or ""),
            )
        provenance["platform_debug_posture"] = self._observed_platform_debug_posture(
            provenance,
            status=status,
        )
        return provenance

    def _status_payload(self) -> dict[str, Any]:
        if not self.status_bin:
            return {}
        try:
            result = self.runner(
                [self.status_bin],
                capture_output=True,
                text=True,
                timeout=5,
            )
            payload = json.loads(result.stdout)
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError, TypeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _observed_platform_debug_posture(
        self,
        provenance: Mapping[str, Any],
        *,
        status: Mapping[str, Any] | None = None,
    ) -> str:
        if _canonical_backend(provenance.get("backend")) != "platform_debug":
            return ""
        status = status if isinstance(status, Mapping) else {}
        candidates: list[object] = [
            status.get("platform_debug_observed_posture"),
            status.get("platform_debug_posture"),
            status.get("debug_posture"),
        ]
        observed_detail = status.get("observed_posture")
        if isinstance(observed_detail, Mapping):
            candidates.insert(0, observed_detail.get("posture"))
        else:
            candidates.insert(0, observed_detail)
        posture_detail = status.get("platform_debug_posture")
        if isinstance(posture_detail, dict):
            candidates = [
                posture_detail.get("observed"),
                posture_detail.get("runtime"),
                *candidates,
            ]
        for field in ("debug_lan", "debug_lan_bridge"):
            value = status.get(field)
            if isinstance(value, dict):
                candidates.extend(
                    (
                        value.get("platform_debug_posture"),
                        value.get("posture"),
                        value.get("mode"),
                    )
                )
                if str(value.get("status") or "").strip().lower() in {
                    "active",
                    "ready",
                }:
                    candidates.append("native_lan")
        for candidate in candidates:
            if not str(candidate or "").strip():
                continue
            try:
                return _canonical_platform_debug_posture(candidate)
            except ValueError:
                continue
        # Older switchers expose no posture field. Their platform-debug command
        # is the committed loopback-only baseline, so retain that compatibility
        # while requiring explicit evidence for native-LAN verification.
        return DEFAULT_PLATFORM_DEBUG_POSTURE

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
                "target_platform_debug_posture": record.get(
                    "target_platform_debug_posture"
                ),
                "rollback_platform_debug_posture": record.get(
                    "rollback_platform_debug_posture"
                ),
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
