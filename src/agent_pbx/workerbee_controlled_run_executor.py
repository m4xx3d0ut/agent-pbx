from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
from contextlib import suppress
from typing import Any, Awaitable, Callable, Mapping

from .security import now_ts
from .store import Store
from .workerbee_controlled_run import (
    CONTROLLED_RUN_REQUIRED_TOOLS,
    WorkerBeeControlledRunError,
    WorkerBeeControlledRunService,
)
from .workerbee_mcp import call_workerbee_mcp_tool
from .workerbee_runtime import WorkerBeeRuntimeRegistry


ToolCaller = Callable[..., Awaitable[dict[str, Any]]]

POLL_SECONDS_ENV = "AGENT_PBX_WORKERBEE_CONTROLLED_RUN_POLL_SECONDS"
DEFAULT_POLL_SECONDS = 1.0
MIN_POLL_SECONDS = 0.05
MAX_POLL_SECONDS = 30.0

_BACKEND_ACTIVE_STATES = {
    "accepted",
    "preparing",
    "ready",
    "running",
    "collecting",
    "cleaning",
    "recovering",
}
_BACKEND_TERMINAL_STATES = {
    "succeeded",
    "failed",
    "canceled",
    "cancelled",
    "cleanup_failed",
}


class WorkerBeeControlledRunExecutor:
    """Drive approved requests through five fixed WorkerBee appliance tools."""

    def __init__(
        self,
        store: Store,
        service: WorkerBeeControlledRunService,
        runtime_registry: WorkerBeeRuntimeRegistry,
        *,
        tool_caller: ToolCaller = call_workerbee_mcp_tool,
        environ: Mapping[str, str] | None = None,
        executor_id: str | None = None,
    ) -> None:
        self.store = store
        self.service = service
        self.runtime_registry = runtime_registry
        self.tool_caller = tool_caller
        self.environ = dict(os.environ if environ is None else environ)
        self.executor_id = executor_id or f"agent-pbx:{socket.gethostname()}:{os.getpid()}"
        self.poll_seconds = self._poll_seconds()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._closing = False

    def _poll_seconds(self) -> float:
        raw = self.environ.get(POLL_SECONDS_ENV)
        try:
            value = float(raw) if raw is not None else DEFAULT_POLL_SECONDS
        except ValueError:
            value = DEFAULT_POLL_SECONDS
        return min(max(value, MIN_POLL_SECONDS), MAX_POLL_SECONDS)

    async def start(self) -> None:
        self._closing = False
        requests = await asyncio.to_thread(
            self.store.list_workerbee_controlled_run_requests,
            statuses=(
                "approved", "claimed", "provisioning", "canary", "running",
                "collecting", "cleaning", "recovery_required",
            ),
            limit=500,
        )
        for request in requests:
            self.schedule(str(request["request_id"]))

    async def stop(self) -> None:
        self._closing = True
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    def schedule(self, request_id: str) -> bool:
        request_id = str(request_id or "").strip()
        existing = self._tasks.get(request_id)
        if not request_id or self._closing or (existing is not None and not existing.done()):
            return False
        task = asyncio.create_task(
            self._run_guarded(request_id),
            name=f"workerbee-controlled-run:{request_id}",
        )
        self._tasks[request_id] = task
        task.add_done_callback(lambda completed, rid=request_id: self._forget(rid, completed))
        return True

    def _forget(self, request_id: str, task: asyncio.Task[None]) -> None:
        if self._tasks.get(request_id) is task:
            self._tasks.pop(request_id, None)

    async def cancel(
        self, request_id: str, *, canceled_by: str, reason: str
    ) -> dict[str, Any]:
        request = await asyncio.to_thread(
            self.store.get_workerbee_controlled_run_request, request_id
        )
        if request is None:
            raise KeyError("WorkerBee controlled-run request not found")
        run_id = str(request.get("workerbee_run_id") or "")
        if not run_id:
            return await asyncio.to_thread(
                self.service.cancel,
                request_id,
                canceled_by=canceled_by,
                reason=reason,
            )
        runtime = self._require_runtime(request)
        await self._call(
            runtime.endpoint,
            "workerbee_v1_controlled_run_cancel",
            {"run_id": run_id, "reason": str(reason)[:1000]},
        )
        self.schedule(request_id)
        latest = await asyncio.to_thread(
            self.store.get_workerbee_controlled_run_request, request_id
        )
        assert latest is not None
        return latest

    async def _run_guarded(self, request_id: str) -> None:
        try:
            await self._run(request_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - persist normalized executor failure
            await self._fail(request_id, exc)

    async def _run(self, request_id: str) -> None:
        request = await asyncio.to_thread(
            self.store.get_workerbee_controlled_run_request, request_id
        )
        if request is None:
            return
        status = str(request.get("status") or "")
        if status in {
            "pending", "rejected", "expired", "succeeded", "failed", "canceled"
        }:
            return
        if status in {
            "provisioning", "canary", "running", "collecting", "cleaning",
            "recovery_required",
        }:
            request = await asyncio.to_thread(
                self.store.claim_workerbee_controlled_run_request,
                request_id,
                executor_id=self.executor_id,
                lease_seconds=300,
            )
            status = str(request.get("status") or "")
            if status == "recovery_required" and request.get("workerbee_run_id"):
                request = await self._transition(
                    request,
                    "running",
                    expected=("recovery_required",),
                    run_id=str(request["workerbee_run_id"]),
                    workspace_id=request.get("workerbee_workspace_id"),
                    result={"phase": "recovery_observation"},
                )
                status = "running"
        if status in {"approved", "claimed"} and not request.get("workerbee_run_id"):
            request = await asyncio.to_thread(
                self.store.claim_workerbee_controlled_run_request,
                request_id,
                executor_id=self.executor_id,
                lease_seconds=300,
            )
            request = await self._transition(
                request,
                "provisioning",
                expected=("claimed",),
                result={"phase": "appliance_preflight"},
            )
            runtime = self._require_runtime(request)
            payload = self._request_payload(request)
            preflight = await self._call(
                runtime.endpoint,
                "workerbee_v1_controlled_run_preflight",
                payload,
            )
            self._validate_preflight(request, preflight)
            request = await self._transition(
                request,
                "canary",
                expected=("provisioning",),
                result={"phase": "appliance_start", "preflight": self._bounded(preflight)},
            )
            started = await self._call(
                runtime.endpoint,
                "workerbee_v1_controlled_run_start",
                payload,
            )
            data = self._data(started)
            run_id = str(data.get("run_id") or data.get("controlled_run_id") or "").strip()
            workspace_id = str(data.get("workspace_id") or "").strip()
            if not run_id:
                raise WorkerBeeControlledRunError(
                    "WORKERBEE_CONTROLLED_RUN_START_INVALID",
                    "WorkerBee omitted the controlled run ID.",
                )
            request = await self._transition(
                request,
                "running",
                expected=("canary",),
                run_id=run_id,
                workspace_id=workspace_id or None,
                result={"phase": "running"},
            )

        run_id = str(request.get("workerbee_run_id") or "").strip()
        if not run_id:
            raise WorkerBeeControlledRunError(
                "WORKERBEE_CONTROLLED_RUN_RECOVERY_INCOMPLETE",
                "An active controlled run has no WorkerBee run ID.",
            )
        runtime = self._require_runtime(request)
        profile = request.get("profile") if isinstance(request.get("profile"), dict) else {}
        deadline = float(request.get("started_at") or now_ts()) + int(
            profile.get("max_seconds") or 1800
        ) + 300
        terminal: dict[str, Any] | None = None
        while terminal is None:
            if now_ts() > deadline:
                await self._call(
                    runtime.endpoint,
                    "workerbee_v1_controlled_run_cancel",
                    {"run_id": run_id, "reason": "Agent PBX controlled-run deadline exceeded"},
                )
            observed = await self._call(
                runtime.endpoint,
                "workerbee_v1_controlled_run_status",
                {"run_id": run_id},
            )
            data = self._data(observed)
            backend_state = str(data.get("status") or data.get("state") or "").lower()
            if backend_state in _BACKEND_TERMINAL_STATES:
                terminal = data
                break
            if backend_state not in _BACKEND_ACTIVE_STATES:
                raise WorkerBeeControlledRunError(
                    "WORKERBEE_CONTROLLED_RUN_STATUS_INVALID",
                    f"WorkerBee returned unsupported state {backend_state!r}.",
                )
            if str(request.get("status")) == "running" and backend_state in {"collecting", "cleaning"}:
                request = await self._transition(
                    request,
                    "collecting",
                    expected=("running",),
                    result={"phase": backend_state},
                )
            if str(request.get("status")) == "collecting" and backend_state == "cleaning":
                request = await self._transition(
                    request,
                    "cleaning",
                    expected=("collecting",),
                    result={"phase": "cleaning"},
                )
            await asyncio.sleep(self.poll_seconds)

        if str(request.get("status")) == "running":
            request = await self._transition(
                request,
                "collecting",
                expected=("running",),
                result={"phase": "collecting"},
            )
        if str(request.get("status")) == "collecting":
            request = await self._transition(
                request,
                "cleaning",
                expected=("collecting",),
                result={"phase": "cleaning"},
            )
        receipt_response = await self._call(
            runtime.endpoint,
            "workerbee_v1_controlled_run_receipt",
            {"run_id": run_id},
        )
        receipt = self._data(receipt_response)
        cleanup = receipt.get("cleanup") if isinstance(receipt.get("cleanup"), dict) else {}
        residue = cleanup.get("residue") if isinstance(cleanup, dict) else None
        cleanup_complete = bool(cleanup.get("complete")) and residue in (None, [])
        backend_state = str(terminal.get("status") or terminal.get("state") or "").lower()
        succeeded = backend_state == "succeeded" and cleanup_complete
        outcome = "succeeded" if succeeded else (
            "canceled" if backend_state in {"canceled", "cancelled"} else "failed"
        )
        terminal_state = (
            "succeeded"
            if succeeded
            else ("canceled" if outcome == "canceled" else "failed")
        )
        request = await self._transition(
            request,
            terminal_state,
            expected=("cleaning",),
            result={"backend": self._bounded(terminal), "cleanup_complete": cleanup_complete},
            error_code=None if succeeded else (
                "WORKERBEE_CONTROLLED_RUN_CLEANUP_INCOMPLETE"
                if not cleanup_complete
                else "WORKERBEE_CONTROLLED_RUN_FAILED"
            ),
            error=None if succeeded else "WorkerBee controlled run did not complete cleanly.",
        )
        evidence = receipt.get("evidence") if isinstance(receipt.get("evidence"), dict) else {}
        await asyncio.to_thread(
            self.store.record_workerbee_controlled_run_receipt,
            request_id,
            outcome=outcome,
            backend_run_id=run_id,
            workspace_id=(
                str(receipt.get("workspace_id") or request.get("workerbee_workspace_id") or "")
                or None
            ),
            evidence=self._bounded(evidence),
            cleanup=self._bounded(cleanup),
        )

    async def _transition(
        self,
        request: Mapping[str, Any],
        status: str,
        *,
        expected: tuple[str, ...],
        run_id: str | None = None,
        workspace_id: str | None = None,
        result: Mapping[str, Any] | None = None,
        error_code: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.store.transition_workerbee_controlled_run_request,
            str(request["request_id"]),
            executor_id=self.executor_id,
            status=status,
            expected_statuses=expected,
            workerbee_run_id=run_id,
            workerbee_workspace_id=workspace_id,
            result=result,
            error_code=error_code,
            error=error,
            lease_seconds=300,
        )

    def _require_runtime(self, request: Mapping[str, Any]):
        runtime = self.runtime_registry.selected()
        if runtime is None:
            raise WorkerBeeControlledRunError(
                "WORKERBEE_CONTROLLED_RUN_BACKEND_UNSELECTED",
                "No verified WorkerBee backend is selected.",
            )
        if (
            runtime.backend != request.get("backend")
            or runtime.instance_id != request.get("backend_instance_id")
            or runtime.generation != int(request.get("backend_generation") or 0)
        ):
            raise WorkerBeeControlledRunError(
                "WORKERBEE_CONTROLLED_RUN_BACKEND_DRIFT",
                "The selected WorkerBee backend changed after approval.",
            )
        return runtime

    @staticmethod
    def _request_payload(request: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "request_id": str(request["request_id"]),
            "idempotency_key": str(request["request_id"]),
            "request_sha256": str(request["request_sha256"]),
            "root_operator_agent_id": str(request["root_operator_agent_id"]),
            "target_fork_agent_id": str(request["target_fork_agent_id"]),
            "campaign_id": str(request["campaign_id"]),
            "workspace_binding_id": str(request["workspace_binding_id"]),
            "profile": dict(request.get("profile") or {}),
            "profile_sha256": str(request["profile_sha256"]),
            "baseline_commit": str(request["baseline_commit"]),
            "candidate_commit": str(request["candidate_commit"]),
            "recipe_id": str(request["recipe_id"]),
            "tool_manifest_sha256": str(request["tool_manifest_sha256"]),
        }

    @staticmethod
    def _validate_preflight(
        request: Mapping[str, Any], response: Mapping[str, Any]
    ) -> None:
        data = WorkerBeeControlledRunExecutor._data(response)
        if not bool(data.get("ok", response.get("ok", False))):
            raise WorkerBeeControlledRunError(
                "WORKERBEE_CONTROLLED_RUN_PREFLIGHT_FAILED",
                str(data.get("error") or "WorkerBee rejected controlled-run preflight."),
            )
        for key in ("request_sha256", "profile_sha256", "tool_manifest_sha256"):
            if str(data.get(key) or "") != str(request.get(key) or ""):
                raise WorkerBeeControlledRunError(
                    "WORKERBEE_CONTROLLED_RUN_PREFLIGHT_DRIFT",
                    f"WorkerBee preflight did not confirm {key}.",
                )

    async def _call(
        self, endpoint: str, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        if tool_name not in CONTROLLED_RUN_REQUIRED_TOOLS:
            raise RuntimeError("controlled-run executor attempted an unapproved tool")
        return await self.tool_caller(
            endpoint,
            tool_name,
            arguments,
            timeout_seconds=30.0,
        )

    async def _fail(self, request_id: str, exc: Exception) -> None:
        request = await asyncio.to_thread(
            self.store.get_workerbee_controlled_run_request, request_id
        )
        if request is None or str(request.get("status")) in {
            "succeeded", "failed", "rejected", "canceled", "expired"
        }:
            return
        error_code = (
            exc.code
            if isinstance(exc, WorkerBeeControlledRunError)
            else "WORKERBEE_CONTROLLED_RUN_EXECUTOR_ERROR"
        )
        message = (
            exc.message if isinstance(exc, WorkerBeeControlledRunError) else type(exc).__name__
        )
        current_status = str(request.get("status") or "")
        may_have_started = bool(request.get("workerbee_run_id")) or current_status in {
            "canary", "running", "collecting", "cleaning", "recovery_required"
        }
        failure_status = "recovery_required" if may_have_started else "failed"
        with suppress(Exception):
            await asyncio.to_thread(
                self.store.transition_workerbee_controlled_run_request,
                request_id,
                executor_id=str(request.get("executor_id") or self.executor_id),
                status=failure_status,
                expected_statuses=(current_status,),
                workerbee_run_id=request.get("workerbee_run_id"),
                workerbee_workspace_id=request.get("workerbee_workspace_id"),
                result={"failed_at": now_ts()},
                error_code=error_code,
                error=str(message)[:4000],
            )

    @staticmethod
    def _data(response: Mapping[str, Any]) -> dict[str, Any]:
        data = response.get("data")
        return dict(data) if isinstance(data, Mapping) else dict(response)

    @staticmethod
    def _bounded(value: Mapping[str, Any], *, limit: int = 32_768) -> dict[str, Any]:
        raw = json.dumps(dict(value), sort_keys=True, default=str)
        if len(raw.encode("utf-8")) <= limit:
            return dict(value)
        return {
            "truncated": True,
            "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            "bytes": len(raw.encode("utf-8")),
        }
