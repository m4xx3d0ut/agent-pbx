from __future__ import annotations

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

from .workerbee_backend_adapter import (
    LegacyWorkerBeeBackendAdapter,
    WorkerBeeBackendAdapter,
    WorkerBeeSwitchBackend,
    canonical_platform_debug_posture,
)
from .workerbee_runtime import canonical_workerbee_backend


WORKERBEE_SWITCH_API_VERSION = "agent-pbx.workerbee-switch/v1"
TRANSACTION_ID_PATTERN = re.compile(r"^[a-f0-9]{24}$")
DEFAULT_PREVIEW_TTL_SECONDS = 15 * 60


def _canonical_backend(value: object) -> str:
    return canonical_workerbee_backend(value)


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
        adapter: WorkerBeeBackendAdapter | None = None,
    ) -> None:
        self.state_root = state_root.expanduser().resolve()
        self.clock = clock
        self.preview_ttl_seconds = max(30, int(preview_ttl_seconds))
        self.cache_invalidator = cache_invalidator or (lambda: None)
        self.impact_provider = impact_provider or (lambda: {})
        self.selection_recorder = selection_recorder
        self.adapter = adapter if adapter is not None else LegacyWorkerBeeBackendAdapter(
            switch_bin=switch_bin,
            status_bin=status_bin,
            platform_debug_posture=platform_debug_posture,
            runner=runner,
            capability_probe=capability_probe,
            which=which,
        )
        self._lock = threading.Lock()

    @property
    def platform_debug_posture(self) -> str:
        return self.adapter.platform_debug_posture

    def status(self) -> dict[str, Any]:
        active = self.adapter.discover()
        descriptors = self._descriptors()
        targets = [item.backend for item in descriptors.values() if item.available]
        impact = dict(self.impact_provider() or {})
        active_backend = _canonical_backend(active.get("backend"))
        observed_posture = str(active.get("platform_debug_posture") or "")
        posture_ready = bool(
            active_backend != "platform_debug"
            or observed_posture == self.platform_debug_posture
        )
        return {
            "api_version": WORKERBEE_SWITCH_API_VERSION,
            "available": bool(targets),
            "targets": targets,
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
                item.public_dict(active) for item in descriptors.values()
            ],
        }

    def preview(self, target: str) -> dict[str, Any]:
        with self._lock:
            descriptor = self._require_target(target)
            resolved_target = descriptor.backend
            before = self.adapter.discover()
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
            self._require_target(before.get("backend"))
            impact = dict(self.impact_provider() or {})
            if int(impact.get("mutation_count") or 0) > 0:
                raise ValueError(
                    "WorkerBee backend switch is blocked by active mutating tool calls"
                )
            plan = self.adapter.preflight(resolved_target)
            now = self.clock()
            transaction_id = secrets.token_hex(12)
            preview_token = secrets.token_urlsafe(24)
            record: dict[str, Any] = {
                "api_version": WORKERBEE_SWITCH_API_VERSION,
                "transaction_id": transaction_id,
                "preview_token": preview_token,
                "phase": "previewed",
                "target": resolved_target,
                "target_descriptor": descriptor.public_dict(before),
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
            before_now = self.adapter.discover()
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
                self.adapter.activate(target)
            except Exception as exc:  # noqa: BLE001 - preserve recovery evidence
                current = self.adapter.discover()
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
            try:
                result = self.adapter.verify(
                    target,
                    expected_platform_debug_posture=target_posture or None,
                )
            except Exception as exc:  # noqa: BLE001 - use the existing recovery path
                result = {
                    "verified": False,
                    "message": f"backend verification failed ({type(exc).__name__})",
                }
            result = self._checked_verification(result, target, target_posture)
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
        try:
            self._require_target(backend)
        except ValueError:
            return {
                "verified": False,
                "message": f"prior backend {backend or '-'} is not switchable",
            }
        effective_posture = (
            canonical_platform_debug_posture(
                platform_debug_posture or self.platform_debug_posture
            )
            if backend == "platform_debug"
            else None
        )
        result = self.adapter.restore(
            backend,
            expected_platform_debug_posture=effective_posture,
        )
        return self._checked_verification(result, backend, effective_posture or "")

    @staticmethod
    def _checked_verification(
        result: dict[str, Any], target: str, target_posture: str
    ) -> dict[str, Any]:
        if result.get("verified") is not True:
            return {**result, "verified": False}
        after = result.get("after")
        capability = result.get("capability")
        if (
            not isinstance(after, Mapping)
            or after.get("ready") is not True
            or _canonical_backend(after.get("backend")) != target
            or not isinstance(after.get("endpoint"), str)
            or not after["endpoint"].strip()
            or not isinstance(capability, Mapping)
            or capability.get("ready") is not True
            or (
                target == "platform_debug"
                and after.get("platform_debug_posture") != target_posture
            )
        ):
            return {
                **result,
                "verified": False,
                "message": "WorkerBee adapter returned mismatched verification evidence.",
            }
        return result

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

    def _descriptors(self) -> dict[str, WorkerBeeSwitchBackend]:
        result: dict[str, WorkerBeeSwitchBackend] = {}
        for item in self.adapter.descriptors():
            if item.backend in result:
                raise ValueError(f"duplicate WorkerBee backend descriptor: {item.backend}")
            result[item.backend] = item
        return result

    def _require_target(self, target: object) -> WorkerBeeSwitchBackend:
        resolved = _canonical_backend(target)
        descriptor = self._descriptors().get(resolved)
        if descriptor is None:
            raise ValueError(f"unsupported WorkerBee backend {target!r}")
        if not descriptor.available:
            raise ValueError(descriptor.unavailable_reason)
        return descriptor

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
        if _canonical_backend(confirm_target) != target:
            raise ValueError("confirmed backend does not match the preview")
        descriptor = self._require_target(target)
        approved = record.get("target_descriptor")
        if "target_descriptor" not in record:
            # Receipts predating descriptors retain the legacy command contract
            # for OSS/debug only.
            if not isinstance(self.adapter, LegacyWorkerBeeBackendAdapter) or (
                descriptor.qualification != "legacy_switcher"
            ):
                raise ValueError("switch preview lacks a qualified backend descriptor")
        elif not isinstance(approved, dict) or any(
            approved.get(key) != value
            for key, value in descriptor.public_dict({}).items()
            if key != "instance_id"
        ):
            raise ValueError("WorkerBee target configuration changed after preview")
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
