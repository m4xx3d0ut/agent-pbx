from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re
from typing import Any, Callable, Mapping

from . import tmux as tmux_support
from .runtime_tmux import process_start_ticks, runtime_mapping_tmux_binary
from .schemas import CommandCreateRequest
from .security import now_ts
from .store import Store
from .terminal_delivery_repository import TerminalDeliveryRepository
from .tmux_binary import configured_tmux_binary


TERMINAL_DELIVERY_API_VERSION = "agent-pbx.idle-target-delivery/v1"
NOHUP_MODE = "nohup"
DIRECT_DELIVERY_MODES = frozenset({"auto", "tmux"})
RUNTIME_READY_STATES = frozenset({"ready", "complete", "waiting_user"})
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ANSI_CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
ANSI_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
CODEX_INPUT_PROMPT_RE = re.compile(r"^\s*›\s*(?P<body>.*?)\s*$")
CODEX_INPUT_PLACEHOLDERS = ("ask codex to do anything", "write a task")

SECURITY_PROMPT_MARKERS = (
    "approval required",
    "do you want to allow",
    "enter your password",
    "credential required",
    "confirm destructive",
    "press enter to confirm",
)

STARTUP_OVERLAY_MARKERS = (
    "starting codex",
    "initializing codex",
    "connecting to codex",
    "loading codex session",
)

MODEL_OVERLAY_MARKERS = (
    "select a model",
    "choose a model",
    "model selection",
    "model announcement",
)

UPDATE_OVERLAY_MARKERS = (
    "checking for updates",
    "update available",
    "installing update",
    "upgrade codex",
)

THINKING_OVERLAY_MARKERS = (
    "response taking extra thought",
    "retry with a faster model",
)


class IdleTargetDeliveryOutcome(str, Enum):
    DELIVERED = "delivered"
    QUEUED = "queued"
    TARGET_NOT_READY = "target_not_ready"
    OVERLAY_ACTIVE = "overlay_active"
    AMBIGUOUS = "ambiguous"
    BLOCKED = "blocked"
    STALE_MAPPING = "stale_mapping"


@dataclass(frozen=True, slots=True)
class TerminalReadiness:
    ready: bool
    outcome: IdleTargetDeliveryOutcome
    reason_code: str
    overlay: str | None = None


@dataclass(frozen=True, slots=True)
class IdleTargetDeliveryRequest:
    request_id: str
    entity_id: str
    message: str
    requested_delivery: str
    source: str
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ManagedTerminalIdentity:
    entity_id: str
    codex_session_id: str
    server_mode: str
    server_id: str
    socket_path: str
    session_name: str
    window_id: str
    pane_id: str
    pane_pid: int
    process_start_ticks: int
    workspace_path: str
    workspace_set_digest: str
    authority_profile: str

    def public_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "codex_session_id": self.codex_session_id,
            "server_mode": self.server_mode,
            "server_id": self.server_id,
            "socket_path": self.socket_path,
            "session_name": self.session_name,
            "window_id": self.window_id,
            "pane_id": self.pane_id,
            "pane_pid": self.pane_pid,
            "process_start_ticks": self.process_start_ticks,
            "workspace_path": self.workspace_path,
            "workspace_set_digest": self.workspace_set_digest,
            "authority_profile": self.authority_profile,
        }

    def digest(self) -> str:
        encoded = json.dumps(
            self.public_dict(),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class IdleTargetDeliveryResult:
    request_id: str
    command_id: str
    entity_id: str
    outcome: IdleTargetDeliveryOutcome
    reason_code: str
    reason: str
    target_identity_sha256: str | None
    target_identity: Mapping[str, Any] | None
    replayed: bool
    writer_client_id: str | None

    def public_dict(self) -> dict[str, Any]:
        return {
            "api_version": TERMINAL_DELIVERY_API_VERSION,
            "request_id": self.request_id,
            "command_id": self.command_id,
            "entity_id": self.entity_id,
            "outcome": self.outcome.value,
            "reason_code": self.reason_code,
            "reason": self.reason[:400],
            "target_identity_sha256": self.target_identity_sha256,
            "target_identity": dict(self.target_identity or {}),
            "replayed": self.replayed,
            "writer_client_id": self.writer_client_id,
        }


class IdleTargetDeliveryError(ValueError):
    def __init__(self, result: IdleTargetDeliveryResult) -> None:
        self.result = result
        super().__init__(f"{result.outcome.value}: {result.reason}")


class _ClassifiedDeliveryError(RuntimeError):
    def __init__(
        self,
        outcome: IdleTargetDeliveryOutcome,
        reason_code: str,
        reason: str,
    ) -> None:
        self.outcome = outcome
        self.reason_code = reason_code
        self.reason = reason
        super().__init__(reason)


def classify_codex_terminal_capture(captured: str) -> TerminalReadiness:
    """Classify a bounded terminal tail without retaining terminal content."""

    cleaned = ANSI_OSC_RE.sub("", ANSI_CSI_RE.sub("", str(captured or "")))
    cleaned = "".join(
        character
        for character in cleaned
        if character in {"\n", "\t"} or ord(character) >= 32
    )
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    tail = lines[-12:]
    normalized = "\n".join(tail).casefold()
    if any(marker in normalized for marker in SECURITY_PROMPT_MARKERS):
        return TerminalReadiness(
            ready=False,
            outcome=IdleTargetDeliveryOutcome.BLOCKED,
            reason_code="security_prompt_active",
        )
    for overlay, markers in (
        ("startup", STARTUP_OVERLAY_MARKERS),
        ("model", MODEL_OVERLAY_MARKERS),
        ("update", UPDATE_OVERLAY_MARKERS),
        ("thinking", THINKING_OVERLAY_MARKERS),
    ):
        if any(marker in normalized for marker in markers):
            return TerminalReadiness(
                ready=False,
                outcome=IdleTargetDeliveryOutcome.OVERLAY_ACTIVE,
                reason_code=f"{overlay}_overlay_active",
                overlay=overlay,
            )
    bottom_band = tail[-4:]
    for index, line in enumerate(bottom_band):
        prompt = CODEX_INPUT_PROMPT_RE.fullmatch(line)
        if prompt is None:
            continue
        prompt_body = prompt.group("body").strip().casefold()
        known_body = not prompt_body or prompt_body in CODEX_INPUT_PLACEHOLDERS
        if not known_body:
            continue
        footer = bottom_band[index + 1 :]
        footer_is_composer_status = all(
            " · " in item
            or item.casefold().startswith(("← for agents", "? for shortcuts"))
            for item in footer
        )
        if footer_is_composer_status:
            return TerminalReadiness(
                ready=True,
                outcome=IdleTargetDeliveryOutcome.DELIVERED,
                reason_code="input_prompt_ready",
            )
    return TerminalReadiness(
        ready=False,
        outcome=IdleTargetDeliveryOutcome.TARGET_NOT_READY,
        reason_code="input_prompt_not_ready",
    )


class IdleTargetDeliveryService:
    """Fail-closed exact-target delivery for managed Codex terminals."""

    def __init__(
        self,
        store: Store,
        *,
        runtime_snapshot: Callable[[str], Mapping[str, Any]] | None = None,
        tmux_bin: str = "tmux",
        writer_lease_seconds: float = 15.0,
        repository: TerminalDeliveryRepository | None = None,
    ) -> None:
        self.store = store
        self.repository = repository or TerminalDeliveryRepository(store)
        self.runtime_snapshot = runtime_snapshot
        self.tmux_bin = configured_tmux_binary(tmux_bin)
        self.writer_lease_seconds = max(5.0, min(60.0, writer_lease_seconds))

    def deliver(self, request: IdleTargetDeliveryRequest) -> IdleTargetDeliveryResult:
        request_id = str(request.request_id or "").strip()
        entity_id = str(request.entity_id or "").strip()
        message = str(request.message or "")
        requested = str(request.requested_delivery or "auto").strip().lower()
        source = str(request.source or "").strip()
        if not request_id or len(request_id) > 240:
            raise ValueError("idle delivery request_id must contain 1-240 characters")
        if not entity_id or len(entity_id) > 120:
            raise ValueError("idle delivery entity_id must contain 1-120 characters")
        if not message:
            raise ValueError("idle delivery message is required")
        if requested not in {"auto", "queue", "tmux"}:
            raise ValueError("idle delivery mode must be auto, queue, or tmux")
        if not source or len(source) > 120:
            raise ValueError("idle delivery source must contain 1-120 characters")

        agent = self.store.get_agent(entity_id)
        if agent is None:
            return self._record_without_agent(
                request,
                outcome=IdleTargetDeliveryOutcome.BLOCKED,
                reason_code="entity_not_registered",
                reason="target entity is not registered",
            )
        metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
        pbx_mode = str(metadata.get("pbx_mode") or "report").strip().lower()
        resolved = requested
        if requested == "auto":
            resolved = "queue" if pbx_mode == NOHUP_MODE else "tmux"
        message_sha256 = hashlib.sha256(message.encode("utf-8")).hexdigest()
        request_sha256 = self._request_sha256(
            request=request,
            resolved_delivery=resolved,
            message_sha256=message_sha256,
        )
        audit_payload = {
            **dict(request.payload),
            "source": source,
            "requested_delivery": requested,
            "resolved_delivery": resolved,
            "delivery_mode": resolved,
            "message_sha256": message_sha256,
            "message_bytes": len(message.encode("utf-8")),
        }

        if resolved == "queue":
            if pbx_mode != NOHUP_MODE:
                return self._record_direct_outcome(
                    request=request,
                    request_sha256=request_sha256,
                    audit_payload=audit_payload,
                    outcome=IdleTargetDeliveryOutcome.BLOCKED,
                    reason_code="queue_requires_explicit_nohup",
                    reason="queue delivery is permitted only for an explicit nohup target",
                )
            queued_payload = {**audit_payload, "message": message}
            command, created = self.repository.create_idempotent_command(
                CommandCreateRequest(
                    agent_id=entity_id,
                    type="send_input",
                    payload=queued_payload,
                ),
                delivery_request_id=request_id,
                delivery_request_sha256=request_sha256,
                status="queued",
            )
            result = IdleTargetDeliveryResult(
                request_id=request_id,
                command_id=str(command["command_id"]),
                entity_id=entity_id,
                outcome=IdleTargetDeliveryOutcome.QUEUED,
                reason_code="explicit_nohup_queue",
                reason="delivery was queued for the explicit nohup target",
                target_identity_sha256=None,
                target_identity=None,
                replayed=not created,
                writer_client_id=None,
            )
            if created:
                self._append_audit(result)
            return result

        return self._deliver_managed_terminal(
            request=request,
            request_sha256=request_sha256,
            audit_payload=audit_payload,
            agent=agent,
        )

    def command_for_result(self, result: IdleTargetDeliveryResult) -> dict[str, Any]:
        command = self.store.get_command(result.command_id)
        if command is None:
            raise RuntimeError("idle delivery command is unavailable")
        return command

    def _deliver_managed_terminal(
        self,
        *,
        request: IdleTargetDeliveryRequest,
        request_sha256: str,
        audit_payload: dict[str, Any],
        agent: Mapping[str, Any],
    ) -> IdleTargetDeliveryResult:
        command, created = self.repository.create_idempotent_command(
            CommandCreateRequest(
                agent_id=request.entity_id,
                type="send_input",
                payload=audit_payload,
            ),
            delivery_request_id=request.request_id,
            delivery_request_sha256=request_sha256,
            status="preparing",
        )
        if not created:
            return self._replay_result(request, command)
        command_id = str(command["command_id"])
        writer_client_id = self._writer_client_id(request.request_id)
        identity: ManagedTerminalIdentity | None = None
        lease_acquired = False
        send_attempted = False
        try:
            identity = self._resolve_identity(agent)
            self._validate_runtime_snapshot(identity)
            self._validate_terminal(identity)
            try:
                leased = self.store.acquire_tmux_writer_lease(
                    identity.entity_id,
                    client_id=writer_client_id,
                    ttl_seconds=self.writer_lease_seconds,
                )
            except ValueError as exc:
                raise _ClassifiedDeliveryError(
                    IdleTargetDeliveryOutcome.BLOCKED,
                    "writer_lease_unavailable",
                    str(exc),
                ) from exc
            lease_acquired = True
            if (
                not leased.get("writer_lease_active")
                or str(leased.get("writer_client_id") or "") != writer_client_id
            ):
                raise _ClassifiedDeliveryError(
                    IdleTargetDeliveryOutcome.BLOCKED,
                    "writer_lease_not_owned",
                    "delivery did not acquire the exact runtime writer lease",
                )
            refreshed_agent = self.store.get_agent(identity.entity_id)
            if refreshed_agent is None:
                raise _ClassifiedDeliveryError(
                    IdleTargetDeliveryOutcome.STALE_MAPPING,
                    "entity_changed_after_writer_lease",
                    "target entity disappeared after writer lease acquisition",
                )
            refreshed = self._resolve_identity(refreshed_agent)
            if refreshed != identity:
                raise _ClassifiedDeliveryError(
                    IdleTargetDeliveryOutcome.STALE_MAPPING,
                    "mapping_changed_after_writer_lease",
                    "managed runtime identity changed after writer lease acquisition",
                )
            self._validate_runtime_snapshot(refreshed)
            self._validate_terminal(refreshed)
            send_attempted = True
            tmux_support.send_text(
                refreshed.pane_id,
                request.message,
                tmux_bin=runtime_mapping_tmux_binary(
                    self.store.get_tmux_runtime_mapping(refreshed.entity_id) or {}
                ),
                socket_path=refreshed.socket_path,
            )
            result = self._result(
                request=request,
                command_id=command_id,
                outcome=IdleTargetDeliveryOutcome.DELIVERED,
                reason_code="input_prompt_delivered",
                reason="message was delivered to the exact idle managed terminal",
                identity=refreshed,
                writer_client_id=writer_client_id,
            )
        except _ClassifiedDeliveryError as exc:
            result = self._result(
                request=request,
                command_id=command_id,
                outcome=exc.outcome,
                reason_code=exc.reason_code,
                reason=exc.reason,
                identity=identity,
                writer_client_id=writer_client_id if lease_acquired else None,
            )
        except Exception as exc:  # noqa: BLE001 - partial tmux delivery is ambiguous.
            result = self._result(
                request=request,
                command_id=command_id,
                outcome=(
                    IdleTargetDeliveryOutcome.AMBIGUOUS
                    if send_attempted
                    else IdleTargetDeliveryOutcome.BLOCKED
                ),
                reason_code=(
                    "delivery_result_ambiguous"
                    if send_attempted
                    else "delivery_preflight_failed"
                ),
                reason=str(exc) or "managed terminal delivery failed",
                identity=identity,
                writer_client_id=writer_client_id if lease_acquired else None,
            )
        release_error: Exception | None = None
        if lease_acquired:
            try:
                self.store.release_tmux_writer_lease(
                    request.entity_id,
                    client_id=writer_client_id,
                )
            except Exception as exc:  # noqa: BLE001 - cleanup ambiguity is durable.
                release_error = exc
        if release_error is not None:
            result = self._result(
                request=request,
                command_id=command_id,
                outcome=IdleTargetDeliveryOutcome.AMBIGUOUS,
                reason_code="writer_lease_release_failed",
                reason=f"writer lease cleanup failed: {release_error}",
                identity=identity,
                writer_client_id=writer_client_id,
            )
        terminal_status = (
            "sent"
            if result.outcome == IdleTargetDeliveryOutcome.DELIVERED
            else result.outcome.value
        )
        command = self.repository.finish_command(
            command_id,
            expected_status="preparing",
            status=terminal_status,
            result=result.public_dict(),
        )
        final_result = IdleTargetDeliveryResult(
            request_id=result.request_id,
            command_id=str(command["command_id"]),
            entity_id=result.entity_id,
            outcome=result.outcome,
            reason_code=result.reason_code,
            reason=result.reason,
            target_identity_sha256=result.target_identity_sha256,
            target_identity=result.target_identity,
            replayed=False,
            writer_client_id=result.writer_client_id,
        )
        self._append_audit(final_result)
        return final_result

    def _resolve_identity(self, agent: Mapping[str, Any]) -> ManagedTerminalIdentity:
        entity_id = str(agent.get("agent_id") or "").strip()
        mapping = self.store.get_tmux_runtime_mapping(entity_id)
        if mapping is None:
            raise _ClassifiedDeliveryError(
                IdleTargetDeliveryOutcome.STALE_MAPPING,
                "runtime_mapping_missing",
                "exact managed runtime mapping is unavailable",
            )
        if str(mapping.get("state") or "").strip().lower() != "ready":
            raise _ClassifiedDeliveryError(
                IdleTargetDeliveryOutcome.STALE_MAPPING,
                "runtime_mapping_not_ready",
                "managed runtime mapping is not ready",
            )
        metadata = agent.get("metadata") if isinstance(agent.get("metadata"), Mapping) else {}
        codex_session_id = self._first_text(
            metadata.get("fork_codex_session_id"),
            metadata.get("last_resume_codex_session_id"),
            metadata.get("codex_session_id"),
        )
        mapped_session_id = str(mapping.get("codex_session_id") or "").strip()
        workspace_path = self._first_text(
            metadata.get("work_root"),
            metadata.get("cwd"),
        )
        mapped_workspace = str(mapping.get("cwd") or "").strip()
        workspace_digest = str(
            metadata.get("operator_workspace_set_digest") or ""
        ).strip().lower()
        authority_profile = str(
            metadata.get("operator_authority_profile") or ""
        ).strip()
        required = {
            "codex_session_id": codex_session_id,
            "mapped_codex_session_id": mapped_session_id,
            "server_mode": str(mapping.get("server_mode") or "").strip(),
            "server_id": str(mapping.get("server_id") or "").strip(),
            "socket_path": str(mapping.get("socket_path") or "").strip(),
            "session_name": str(mapping.get("session_name") or "").strip(),
            "window_id": str(mapping.get("window_id") or "").strip(),
            "pane_id": str(mapping.get("pane_id") or "").strip(),
            "workspace_path": workspace_path,
            "mapped_workspace": mapped_workspace,
            "authority_profile": authority_profile,
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            raise _ClassifiedDeliveryError(
                IdleTargetDeliveryOutcome.STALE_MAPPING,
                "runtime_identity_incomplete",
                "managed runtime identity is incomplete: " + ", ".join(sorted(missing)),
            )
        if codex_session_id != mapped_session_id:
            raise _ClassifiedDeliveryError(
                IdleTargetDeliveryOutcome.STALE_MAPPING,
                "codex_session_mismatch",
                "managed runtime Codex session does not match durable entity metadata",
            )
        if workspace_path != mapped_workspace:
            raise _ClassifiedDeliveryError(
                IdleTargetDeliveryOutcome.STALE_MAPPING,
                "workspace_mismatch",
                "managed runtime workspace does not match durable entity metadata",
            )
        if SHA256_RE.fullmatch(workspace_digest) is None:
            raise _ClassifiedDeliveryError(
                IdleTargetDeliveryOutcome.BLOCKED,
                "workspace_identity_unavailable",
                "operator workspace-set identity is unavailable",
            )
        try:
            pane_pid = int(mapping.get("pane_pid") or 0)
            start_ticks = int(mapping.get("process_start_ticks") or 0)
        except (TypeError, ValueError) as exc:
            raise _ClassifiedDeliveryError(
                IdleTargetDeliveryOutcome.STALE_MAPPING,
                "process_identity_invalid",
                "managed runtime process identity is invalid",
            ) from exc
        if pane_pid <= 0 or start_ticks <= 0:
            raise _ClassifiedDeliveryError(
                IdleTargetDeliveryOutcome.STALE_MAPPING,
                "process_identity_incomplete",
                "managed runtime process identity is incomplete",
            )
        return ManagedTerminalIdentity(
            entity_id=entity_id,
            codex_session_id=codex_session_id,
            server_mode=required["server_mode"],
            server_id=required["server_id"],
            socket_path=required["socket_path"],
            session_name=required["session_name"],
            window_id=required["window_id"],
            pane_id=required["pane_id"],
            pane_pid=pane_pid,
            process_start_ticks=start_ticks,
            workspace_path=workspace_path,
            workspace_set_digest=workspace_digest,
            authority_profile=authority_profile,
        )

    def _validate_runtime_snapshot(self, identity: ManagedTerminalIdentity) -> None:
        if self.runtime_snapshot is None:
            agent = self.store.get_agent(identity.entity_id) or {}
            state = str(agent.get("status") or "").strip().lower()
            if state not in {"ready", "registered", "waiting", "done", "complete"}:
                raise _ClassifiedDeliveryError(
                    IdleTargetDeliveryOutcome.TARGET_NOT_READY,
                    "runtime_state_not_ready",
                    "target has no ready Codex runtime evidence",
                )
            return
        try:
            snapshot = self.runtime_snapshot(identity.entity_id)
        except Exception as exc:
            raise _ClassifiedDeliveryError(
                IdleTargetDeliveryOutcome.AMBIGUOUS,
                "runtime_evidence_unavailable",
                "Codex runtime readiness evidence is unavailable",
            ) from exc
        if not isinstance(snapshot, Mapping):
            raise _ClassifiedDeliveryError(
                IdleTargetDeliveryOutcome.AMBIGUOUS,
                "runtime_evidence_malformed",
                "Codex runtime readiness evidence is malformed",
            )
        if str(snapshot.get("session_id") or "").strip() != identity.codex_session_id:
            raise _ClassifiedDeliveryError(
                IdleTargetDeliveryOutcome.STALE_MAPPING,
                "runtime_session_changed",
                "Codex runtime evidence belongs to another session",
            )
        state = str(snapshot.get("state") or "").strip().lower()
        if state not in RUNTIME_READY_STATES:
            raise _ClassifiedDeliveryError(
                IdleTargetDeliveryOutcome.TARGET_NOT_READY,
                "runtime_state_not_ready",
                f"Codex runtime state is {state or 'unknown'}, not input-ready",
            )

    def _validate_terminal(self, identity: ManagedTerminalIdentity) -> None:
        tmux_bin = runtime_mapping_tmux_binary(
            self.store.get_tmux_runtime_mapping(identity.entity_id) or {}
        )
        try:
            panes = tmux_support.list_panes(
                tmux_bin,
                socket_path=identity.socket_path,
            )
        except Exception as exc:
            raise _ClassifiedDeliveryError(
                IdleTargetDeliveryOutcome.AMBIGUOUS,
                "tmux_inventory_unavailable",
                "managed tmux inventory is unavailable",
            ) from exc
        matches = [
            pane
            for pane in panes
            if pane.pane_id == identity.pane_id
            and pane.session_name == identity.session_name
            and pane.window_id == identity.window_id
        ]
        if len(matches) != 1:
            raise _ClassifiedDeliveryError(
                IdleTargetDeliveryOutcome.STALE_MAPPING,
                "tmux_target_mismatch",
                "exact managed tmux pane identity is unavailable",
            )
        pane = matches[0]
        if pane.cwd != identity.workspace_path:
            raise _ClassifiedDeliveryError(
                IdleTargetDeliveryOutcome.STALE_MAPPING,
                "pane_workspace_changed",
                "managed tmux pane workspace changed",
            )
        observed_pid = tmux_support.pane_root_pid(
            identity.pane_id,
            tmux_bin=tmux_bin,
            socket_path=identity.socket_path,
        )
        if observed_pid != identity.pane_pid:
            raise _ClassifiedDeliveryError(
                IdleTargetDeliveryOutcome.STALE_MAPPING,
                "pane_process_changed",
                "managed tmux pane process identity changed",
            )
        observed_ticks = process_start_ticks(observed_pid)
        if observed_ticks is None:
            raise _ClassifiedDeliveryError(
                IdleTargetDeliveryOutcome.AMBIGUOUS,
                "process_start_evidence_unavailable",
                "managed runtime process start evidence is unavailable",
            )
        if observed_ticks != identity.process_start_ticks:
            raise _ClassifiedDeliveryError(
                IdleTargetDeliveryOutcome.STALE_MAPPING,
                "process_start_changed",
                "managed runtime process start identity changed",
            )
        try:
            captured = tmux_support.capture_pane(
                identity.pane_id,
                lines=80,
                tmux_bin=tmux_bin,
                socket_path=identity.socket_path,
            )
        except Exception as exc:
            raise _ClassifiedDeliveryError(
                IdleTargetDeliveryOutcome.AMBIGUOUS,
                "terminal_readiness_unavailable",
                "terminal input readiness could not be captured",
            ) from exc
        readiness = classify_codex_terminal_capture(captured)
        if not readiness.ready:
            raise _ClassifiedDeliveryError(
                readiness.outcome,
                readiness.reason_code,
                "Codex terminal is not at an idle input prompt",
            )

    def _record_without_agent(
        self,
        request: IdleTargetDeliveryRequest,
        *,
        outcome: IdleTargetDeliveryOutcome,
        reason_code: str,
        reason: str,
    ) -> IdleTargetDeliveryResult:
        message_sha256 = hashlib.sha256(request.message.encode("utf-8")).hexdigest()
        request_sha256 = self._request_sha256(
            request=request,
            resolved_delivery=str(request.requested_delivery or "auto"),
            message_sha256=message_sha256,
        )
        return self._record_direct_outcome(
            request=request,
            request_sha256=request_sha256,
            audit_payload={
                **dict(request.payload),
                "source": request.source,
                "requested_delivery": request.requested_delivery,
                "resolved_delivery": request.requested_delivery,
                "message_sha256": message_sha256,
                "message_bytes": len(request.message.encode("utf-8")),
            },
            outcome=outcome,
            reason_code=reason_code,
            reason=reason,
            registered_agent=False,
        )

    def _record_direct_outcome(
        self,
        *,
        request: IdleTargetDeliveryRequest,
        request_sha256: str,
        audit_payload: dict[str, Any],
        outcome: IdleTargetDeliveryOutcome,
        reason_code: str,
        reason: str,
        registered_agent: bool = True,
    ) -> IdleTargetDeliveryResult:
        command, created = self.repository.create_idempotent_command(
            CommandCreateRequest(
                agent_id=request.entity_id if registered_agent else None,
                type="send_input",
                payload=audit_payload,
            ),
            delivery_request_id=request.request_id,
            delivery_request_sha256=request_sha256,
            status=outcome.value,
        )
        result = IdleTargetDeliveryResult(
            request_id=request.request_id,
            command_id=str(command["command_id"]),
            entity_id=request.entity_id,
            outcome=outcome,
            reason_code=reason_code,
            reason=reason,
            target_identity_sha256=None,
            target_identity=None,
            replayed=not created,
            writer_client_id=None,
        )
        if created:
            self.repository.finish_command(
                str(command["command_id"]),
                expected_status=outcome.value,
                status=outcome.value,
                result=result.public_dict(),
            )
            self._append_audit(result)
        return result

    def _replay_result(
        self,
        request: IdleTargetDeliveryRequest,
        command: Mapping[str, Any],
    ) -> IdleTargetDeliveryResult:
        raw = command.get("result")
        if isinstance(raw, Mapping):
            outcome_text = str(raw.get("outcome") or "").strip()
            try:
                outcome = IdleTargetDeliveryOutcome(outcome_text)
            except ValueError:
                outcome = IdleTargetDeliveryOutcome.AMBIGUOUS
            return IdleTargetDeliveryResult(
                request_id=request.request_id,
                command_id=str(command.get("command_id") or ""),
                entity_id=request.entity_id,
                outcome=outcome,
                reason_code=str(raw.get("reason_code") or "replayed_delivery"),
                reason=str(raw.get("reason") or "delivery result replayed"),
                target_identity_sha256=(
                    str(raw.get("target_identity_sha256") or "") or None
                ),
                target_identity=(
                    dict(raw["target_identity"])
                    if isinstance(raw.get("target_identity"), Mapping)
                    else None
                ),
                replayed=True,
                writer_client_id=str(raw.get("writer_client_id") or "") or None,
            )
        if str(command.get("status") or "") == "queued":
            return IdleTargetDeliveryResult(
                request_id=request.request_id,
                command_id=str(command.get("command_id") or ""),
                entity_id=request.entity_id,
                outcome=IdleTargetDeliveryOutcome.QUEUED,
                reason_code="explicit_nohup_queue",
                reason="delivery was already queued for the explicit nohup target",
                target_identity_sha256=None,
                target_identity=None,
                replayed=True,
                writer_client_id=None,
            )
        result = IdleTargetDeliveryResult(
            request_id=request.request_id,
            command_id=str(command.get("command_id") or ""),
            entity_id=request.entity_id,
            outcome=IdleTargetDeliveryOutcome.AMBIGUOUS,
            reason_code="delivery_replay_ambiguous",
            reason="a prior delivery attempt has no terminal result; retry is suppressed",
            target_identity_sha256=None,
            target_identity=None,
            replayed=True,
            writer_client_id=None,
        )
        finalized = self.repository.finish_command(
            str(command.get("command_id") or ""),
            expected_status="preparing",
            status=IdleTargetDeliveryOutcome.AMBIGUOUS.value,
            result=result.public_dict(),
        )
        result = IdleTargetDeliveryResult(
            request_id=result.request_id,
            command_id=str(finalized.get("command_id") or result.command_id),
            entity_id=result.entity_id,
            outcome=result.outcome,
            reason_code=result.reason_code,
            reason=result.reason,
            target_identity_sha256=result.target_identity_sha256,
            target_identity=result.target_identity,
            replayed=True,
            writer_client_id=result.writer_client_id,
        )
        self._append_audit(
            result,
            event_type="idle_target_delivery_replay_ambiguous",
            extra={
                "prior_status": str(command.get("status") or ""),
                "repair_required": True,
                "repair_action": "inspect_exact_target_before_manual_resolution",
            },
        )
        return result

    def _result(
        self,
        *,
        request: IdleTargetDeliveryRequest,
        command_id: str,
        outcome: IdleTargetDeliveryOutcome,
        reason_code: str,
        reason: str,
        identity: ManagedTerminalIdentity | None,
        writer_client_id: str | None,
    ) -> IdleTargetDeliveryResult:
        return IdleTargetDeliveryResult(
            request_id=request.request_id,
            command_id=command_id,
            entity_id=request.entity_id,
            outcome=outcome,
            reason_code=reason_code,
            reason=reason,
            target_identity_sha256=identity.digest() if identity else None,
            target_identity=identity.public_dict() if identity else None,
            replayed=False,
            writer_client_id=writer_client_id,
        )

    def _append_audit(
        self,
        result: IdleTargetDeliveryResult,
        *,
        event_type: str = "idle_target_delivery",
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        self.store.append_event(
            event_type,
            {
                **result.public_dict(),
                **dict(extra or {}),
                "recorded_at": now_ts(),
            },
            result.request_id,
        )

    @staticmethod
    def _request_sha256(
        *,
        request: IdleTargetDeliveryRequest,
        resolved_delivery: str,
        message_sha256: str,
    ) -> str:
        encoded = json.dumps(
            {
                "request_id": request.request_id,
                "entity_id": request.entity_id,
                "source": request.source,
                "requested_delivery": request.requested_delivery,
                "resolved_delivery": resolved_delivery,
                "message_sha256": message_sha256,
                "payload": dict(request.payload),
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _writer_client_id(request_id: str) -> str:
        digest = hashlib.sha256(request_id.encode("utf-8")).hexdigest()
        return f"idle-delivery:{digest[:32]}"

    @staticmethod
    def _first_text(*values: object) -> str:
        for value in values:
            text = str(value or "").strip()
            if text:
                return text
        return ""
