"""Controller-owned durable WorkerBee cutover and cold-start reconciliation."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
import hashlib
import json
import re
import secrets
from typing import Any

from .workerbee_backend_adapter import WorkerBeeBackendAdapter
from .workerbee_cutover import WorkerBeeCutover, WorkerBeeCutoverError
from .workerbee_runtime_identity import (
    WORKERBEE_BACKENDS,
    WorkerBeeRuntimeSelection,
    canonical_workerbee_backend,
    decode_runtime_selection,
    make_runtime_selection,
    runtime_fingerprint,
)

SCHEMA = "agent-pbx.workerbee-qualified-cutover/v1"
_SHA = re.compile(r"[0-9a-f]{64}")


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class WorkerBeeCutoverQualification:
    """Fixed host adapter evidence. The control client never supplies this record."""

    schema: str
    backend: str
    artifact_sha256: str
    effective_config_sha256: str
    route_sha256: str
    runtime_fingerprint: str
    transport_identity_sha256: str

    def __post_init__(self) -> None:
        if (
            self.schema != SCHEMA or self.backend not in WORKERBEE_BACKENDS
            or any(
                type(value) is not str or not _SHA.fullmatch(value)
                for key, value in asdict(self).items() if key not in {"schema", "backend"}
            )
        ):
            raise ValueError("Invalid WorkerBee artifact, route, or transport qualification")


class WorkerBeeDurableSwitch:
    def __init__(
        self, cutover: WorkerBeeCutover, adapter: WorkerBeeBackendAdapter, *,
        impact: Callable[[], Mapping[str, Any]], invalidate: Callable[[], None],
        clock: Callable[[], float], preview_ttl: float, drain_timeout: float = 30,
    ) -> None:
        self.cutover = cutover
        self.adapter = adapter
        self.impact = impact
        self.invalidate = invalidate
        self.clock = clock
        self.preview_ttl = preview_ttl
        self.drain_timeout = drain_timeout

    def _qualify(self, backend: str) -> dict[str, str]:
        descriptors = [item for item in self.adapter.descriptors() if item.backend == backend]
        qualify = getattr(self.adapter, "cutover_qualification", None)
        if len(descriptors) != 1 or descriptors[0].qualification != "verified" or not callable(qualify):
            raise WorkerBeeCutoverError(
                "WORKERBEE_CUTOVER_ADAPTER_UNQUALIFIED",
                f"Backend {backend} lacks immutable artifact, route, and transport evidence",
            )
        result = qualify(backend)
        if not isinstance(result, WorkerBeeCutoverQualification) or result.backend != backend:
            raise ValueError("Backend qualification does not match the requested backend")
        return asdict(result)

    def _verify(self, expected: dict[str, str], result=None) -> dict[str, Any]:
        backend = expected["backend"]
        if result is None:
            result = self.adapter.verify(
                backend, expected_platform_debug_posture=(
                    self.adapter.platform_debug_posture if backend == "platform_debug" else None
                ),
            )
        after = result.get("after")
        capability = result.get("capability")
        if (
            result.get("verified") is not True or not isinstance(after, Mapping)
            or after.get("ready") is not True or after.get("backend") != backend
            or (
                backend == "platform_debug"
                and after.get("platform_debug_posture") != self.adapter.platform_debug_posture
            )
            or not isinstance(capability, Mapping) or capability.get("ready") is not True
            or capability.get("workerbee_transport_identity_sha256")
                != expected["transport_identity_sha256"]
            or result.get("cutover_qualification") != expected
            or self._qualify(backend) != expected
            or runtime_fingerprint(after) != expected["runtime_fingerprint"]
        ):
            raise WorkerBeeCutoverError("WORKERBEE_SWITCH_UNVERIFIED", "Exact backend readback failed")
        # Normalize and bound stored data to the selection schema.
        return make_runtime_selection(after, reason="verified", clock=self.clock).public_dict()

    @staticmethod
    def _inventory(impact: Mapping[str, Any]) -> str:
        return _digest({
            key: impact.get(key, [])
            for key in ("active_leases", "workspace_bindings", "workspace_policy", "route_policy")
        })

    def preview(self, target: str, *, controller: str) -> dict[str, Any]:
        target = canonical_workerbee_backend(target)
        if target not in WORKERBEE_BACKENDS:
            raise ValueError("Unknown WorkerBee switch target")
        with self.cutover.operation("controller_preview") as ticket:
            current = ticket.runtime
            assert current is not None
            source = self._qualify(current.backend)
            destination = self._qualify(target)
            self._verify(source)
            if source["runtime_fingerprint"] != current.fingerprint or (
                ticket.transport_identity
                and source["transport_identity_sha256"] != ticket.transport_identity
            ):
                raise ValueError("Qualified source differs from the committed selection")
            now = self.clock()
            transaction_id = secrets.token_hex(12)
            token = secrets.token_urlsafe(24)
            impact = self.impact()
            record = {
                "api_version": "agent-pbx.workerbee-switch/v1",
                "transaction_id": transaction_id, "target": target,
                "source": current.public_dict(), "before": current.public_dict(),
                "source_qualification": source, "target_qualification": destination,
                "inventory_sha256": self._inventory(impact),
                "plan_digest": _digest(self.adapter.preflight(target)),
                "plan_summary": ["Verified artifact, route, runtime, and workspace compatibility required"],
                "preview_token_sha256": _digest(token), "created_at": now,
                "expires_at": now + self.preview_ttl,
                "requires_privileged_confirmation": target == "platform_debug",
                "existing_sessions_pinned": False, "future_sessions_only": True,
                "generation_guard_required": True, "impact": impact,
            }
            self.cutover.preview(transaction_id, controller, record)
        return {**record, "phase": "previewed", "preview_token": token}

    def apply(
        self, transaction_id: str, *, controller: str, preview_token: str,
        confirm_target: str, confirm_privileged: bool,
    ) -> dict[str, Any]:
        record = self.cutover.journal(transaction_id, controller=controller)
        if (
            not secrets.compare_digest(_digest(preview_token), record["preview_token_sha256"])
            or canonical_workerbee_backend(confirm_target) != record["target"]
            or (record["requires_privileged_confirmation"] and not confirm_privileged)
        ):
            raise ValueError("Switch confirmation differs from the preview")
        if record["phase"] != "previewed":
            return self._terminal(record, {"verified", "verified_noop", "plan_drift", "drain_timeout"})
        if self.clock() >= record["expires_at"]:
            raise ValueError("WorkerBee switch preview expired")
        current = decode_runtime_selection(record["source"])
        return self._execute(record, current=current, controller=controller, rollback=False)

    def rollback(self, transaction_id: str, *, controller: str, confirm: bool) -> dict[str, Any]:
        if confirm is not True:
            raise ValueError("Rollback requires explicit confirmation")
        record = self.cutover.journal(transaction_id, controller=controller)
        if record["phase"] != "verified":
            return self._terminal(record, {"rolled_back", "verified_noop"})
        current = decode_runtime_selection(record["after"])
        return self._execute(record, current=current, controller=controller, rollback=True)

    @staticmethod
    def _terminal(record: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
        if record["phase"] not in allowed:
            raise WorkerBeeCutoverError(
                "WORKERBEE_SWITCH_NOT_ACTIONABLE", f"Switch phase is {record['phase']}",
            )
        return record

    def _execute(
        self, record: dict[str, Any], *, current: WorkerBeeRuntimeSelection,
        controller: str, rollback: bool,
    ) -> dict[str, Any]:
        transaction_id = record["transaction_id"]
        with self.cutover.controller():
            self.cutover.claim(transaction_id, controller, phase=record["phase"], selection=current)
            phase = "rollback_draining" if rollback else "draining"
            try:
                return self._apply_owned(record, current, phase=phase, rollback=rollback)
            except Exception as exc:
                live = self.cutover.journal(transaction_id)
                if live["phase"] not in {"verified", "verified_noop", "rolled_back"}:
                    # Keep the phase so restart can distinguish intent from a published CAS.
                    self.cutover.advance(
                        transaction_id, live["phase"], live["phase"], live,
                        hold="switch_verification_or_recovery_required",
                    )
                raise WorkerBeeCutoverError(
                    "WORKERBEE_SWITCH_RECOVERY_REQUIRED", "Switch requires exact readback before reopening",
                ) from exc

    def _apply_owned(
        self, record: dict[str, Any], current: WorkerBeeRuntimeSelection, *,
        phase: str, rollback: bool,
    ) -> dict[str, Any]:
        transaction_id = record["transaction_id"]
        source = record["target_qualification" if rollback else "source_qualification"]
        destination = record["source_qualification" if rollback else "target_qualification"]
        drained = self.cutover.drain(transaction_id, timeout=self.drain_timeout)
        source_readback = self._verify(source)
        if source_readback["fingerprint"] != current.fingerprint:
            raise ValueError("Source moved before cutover")
        if not drained:
            self.cutover.advance(transaction_id, phase, "drain_timeout", record, reopen=True)
            return self.cutover.journal(transaction_id)
        impact = self.impact()
        if (
            impact.get("active_controlled_runs") or impact.get("mutation_count")
            or self._inventory(impact) != record["inventory_sha256"]
            or (not rollback and (
                self.clock() >= record["expires_at"]
                or _digest(self.adapter.preflight(record["target"])) != record["plan_digest"]
            ))
            or self._qualify(destination["backend"]) != destination
        ):
            self.cutover.advance(transaction_id, phase, "plan_drift", record, reopen=True)
            return self.cutover.journal(transaction_id)
        if source == destination:
            self.cutover.advance(transaction_id, phase, "verified_noop", record, reopen=True)
            return self.cutover.journal(transaction_id)
        intent = "restore_intent" if rollback else "activate_intent"
        record["effect_generation"] = current.generation
        self.cutover.advance(transaction_id, phase, intent, record)
        if rollback:
            proved = self._restore(destination)
        else:
            self.adapter.activate(destination["backend"])
        verifying = "verify_restore" if rollback else "verify_target"
        self.cutover.advance(transaction_id, intent, verifying, record)
        if not rollback:
            proved = self._verify(destination)
        return self._publish(record, verifying, proved, destination, rollback=rollback)

    def _restore(self, destination: dict[str, str]) -> dict[str, Any]:
        backend = destination["backend"]
        result = self.adapter.restore(
            backend, expected_platform_debug_posture=(
                self.adapter.platform_debug_posture if backend == "platform_debug" else None
            ),
        )
        return self._verify(destination, result)

    def _publish(
        self, record: dict[str, Any], intent: str, proved: dict[str, Any],
        qualification: dict[str, str], *, rollback: bool,
    ) -> dict[str, Any]:
        transaction_id = record["transaction_id"]
        selection = make_runtime_selection(
            proved, reason="rollback" if rollback else "switch",
            generation=record["effect_generation"] + 1, clock=self.clock,
        )
        record["rollback_after" if rollback else "after"] = selection.public_dict()
        published = "restore_published" if rollback else "published"
        self.cutover.advance(
            transaction_id, intent, published, record, publish=selection,
            transport_identity=qualification["transport_identity_sha256"],
        )
        return self._finalize(record, published)

    def _finalize(self, record: dict[str, Any], published: str) -> dict[str, Any]:
        restored = published == "restore_published"
        expected = record["source_qualification" if restored else "target_qualification"]
        published_selection = record["rollback_after" if restored else "after"]
        selected = self.cutover.selected()
        observed = self._verify(expected)
        if (
            selected is None or selected.public_dict() != published_selection
            or selected.fingerprint != observed["fingerprint"]
        ):
            raise ValueError("Published WorkerBee selection differs from exact readback")
        self.invalidate()
        phase = "rolled_back" if restored else "verified"
        self.cutover.advance(record["transaction_id"], published, phase, record, reopen=True)
        return self.cutover.journal(record["transaction_id"])

    def recover_startup(self) -> None:
        posture = self.cutover.posture()
        transaction_id = posture.get("transaction_id")
        if posture["admitted"]:
            return
        if transaction_id is None:
            if posture["admission"] == "open":
                self._verify_last_publication()
            return
        record = self.cutover.journal(transaction_id)
        phase = record["phase"]
        if posture["admission"] == "recovery_required" and not (
            phase in {"published", "restore_published", "verify_target", "verify_restore"}
            and posture["reason"] == "switch_verification_or_recovery_required"
        ):
            return
        try:
            if phase in {"draining", "rollback_draining"}:
                rollback = phase == "rollback_draining"
                verify = self._verify(record["target_qualification" if rollback else "source_qualification"])
                expected = record["after" if rollback else "source"]
                if self.cutover.selected() != decode_runtime_selection(expected) or (
                    verify["fingerprint"] != expected["fingerprint"]
                ):
                    raise ValueError("Source drift interrupted a pre-effect switch")
                self.cutover.advance(transaction_id, phase, "interrupted_before_effect", record, reopen=True)
            elif phase in {"published", "restore_published"}:
                self._finalize(record, phase)
            elif phase in {"verify_target", "verify_restore"}:
                rollback = phase == "verify_restore"
                expected = record["source_qualification" if rollback else "target_qualification"]
                current = decode_runtime_selection(record["after" if rollback else "source"])
                if self.cutover.selected() != current:
                    raise ValueError("Pre-publication selection changed")
                self._publish(record, phase, self._verify(expected), expected, rollback=rollback)
            else:
                # A crash can occur immediately before or after the adapter call.
                # Never replay an effect from an intent without an exact outcome.
                self.cutover.advance(transaction_id, phase, phase, record, hold="ambiguous_adapter_effect")
        except Exception:
            live = self.cutover.journal(transaction_id)
            self.cutover.advance(
                transaction_id, live["phase"], live["phase"], live, hold="startup_verification_required",
            )

    def _verify_last_publication(self) -> None:
        marker = self.cutover.publication()
        if marker is None:
            return
        try:
            selected = self.cutover.selected()
            record = self.cutover.journal(marker["transaction_id"])
            restored = record["phase"] == "rolled_back"
            expected = record["rollback_after" if restored else "after"]
            qualification = record["source_qualification" if restored else "target_qualification"]
            if (
                selected is None or selected.public_dict() != expected
                or marker["generation"] != selected.generation
                or marker["transport_identity_sha256"] != qualification["transport_identity_sha256"]
                or self._verify(qualification)["fingerprint"] != selected.fingerprint
            ):
                raise ValueError("Committed WorkerBee generation no longer matches active transport")
        except Exception:
            self.cutover.hold("committed_runtime_readback_failed")
