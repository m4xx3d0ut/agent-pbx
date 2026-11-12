from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from pathlib import PurePosixPath
from typing import Any, Mapping, Protocol

from .contracts import DYNAMIC_RESOURCE_REQUEST_CONTRACT
from .store import Store


RESOURCE_CLASSES = frozenset(
    {
        "workspace_unix_socket",
        "loopback_service",
        "managed_cache",
        "existing_workerbee_service",
        "port_reservation",
    }
)
DISABLED_RESOURCE_CLASSES = frozenset(
    {"network_endpoint", "host_socket", "device", "credential_reference"}
)
RESOURCE_STATUSES = frozenset(
    {
        "pending_approval",
        "approved",
        "leased",
        "rejected",
        "revoked",
        "released",
        "expired",
        "recovery_required",
    }
)
RESOURCE_TERMINAL_STATUSES = frozenset(
    {"rejected", "revoked", "released", "expired"}
)
RESOURCE_APPROVAL_METHOD = "agent-pbx-tui-f8"
RESOURCE_RECEIPT_SCHEMA = "agent-pbx.dynamic-resource-receipt/v1"
RESOURCE_CLEANUP_SCHEMA = "agent-pbx.dynamic-resource-cleanup/v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")
TRUSTED_ROSIE_AUTO_RESOURCE_CLASSES = frozenset(
    {"workspace_unix_socket", "managed_cache"}
)
RESOURCE_POLICIES = frozenset({"public", "trusted_rosie"})
MAX_SPEC_BYTES = 8192


class DynamicResourceError(ValueError):
    pass


class DynamicResourceProvider(Protocol):
    provider_id: str

    def acquire(self, request: Mapping[str, Any]) -> dict[str, Any]: ...

    def release(self, request: Mapping[str, Any]) -> dict[str, Any]: ...

    def recover(self, request: Mapping[str, Any]) -> dict[str, Any] | None: ...


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def resource_approval_confirmation(request: Mapping[str, Any]) -> str:
    request_id = str(request.get("request_id") or "")
    digest = str(request.get("request_sha256") or "")
    return f"APPROVE RESOURCE {request_id} {digest}"


class DynamicResourceBroker:
    """Typed, receipt-bound resource request and lease controller."""

    def __init__(
        self,
        store: Store,
        *,
        enabled: bool = False,
        providers: Mapping[str, DynamicResourceProvider] | None = None,
        clock: Any = time.time,
    ) -> None:
        self.store = store
        self.enabled = bool(enabled)
        self.providers = dict(providers or {})
        self.clock = clock

    def request(
        self,
        *,
        resource_class: str,
        requester_agent_id: str,
        operator_agent_id: str,
        campaign_id: str | None,
        handoff_id: str | None,
        workspace_set_digest: str,
        spec: Mapping[str, Any],
        workerbee_binding_id: str | None = None,
        workerbee_backend_generation: str | None = None,
        ttl_seconds: int = 3600,
        quota: int = 1,
        max_uses: int = 1,
        approval_mode: str = "manual",
        delegation_lease_id: str | None = None,
        policy: str = "public",
        runtime_state: str = "stopped",
    ) -> dict[str, Any]:
        self._require_enabled()
        normalized_class = str(resource_class or "").strip()
        if normalized_class in DISABLED_RESOURCE_CLASSES:
            raise DynamicResourceError(
                "high-risk resource class is modeled but not qualified"
            )
        if normalized_class not in RESOURCE_CLASSES:
            raise DynamicResourceError("unknown dynamic resource class")
        requester = self._safe_id(requester_agent_id, "requester")
        operator = self._safe_id(operator_agent_id, "operator")
        campaign = self._uuid_or_none(campaign_id, "campaign")
        handoff = self._uuid_or_none(handoff_id, "handoff")
        if not (campaign or handoff):
            raise DynamicResourceError(
                "resource request requires a campaign or handoff"
            )
        if not SHA256_RE.fullmatch(str(workspace_set_digest or "")):
            raise DynamicResourceError("resource workspace-set digest is invalid")
        self._validate_request_authority(
            requester_agent_id=requester,
            operator_agent_id=operator,
            campaign_id=campaign,
            handoff_id=handoff,
            workspace_set_digest=str(workspace_set_digest),
        )
        normalized_spec = self._validate_spec(normalized_class, spec)
        binding = self._optional_safe_id(workerbee_binding_id, "WorkerBee binding")
        generation = self._optional_safe_id(
            workerbee_backend_generation,
            "WorkerBee backend generation",
        )
        if normalized_class == "existing_workerbee_service" and not (
            binding and generation
        ):
            raise DynamicResourceError(
                "WorkerBee service resources require binding and backend generation"
            )
        if (
            isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, int)
            or not 60 <= ttl_seconds <= 86400
        ):
            raise DynamicResourceError("resource TTL must be between 60 and 86400 seconds")
        if (
            isinstance(quota, bool)
            or not isinstance(quota, int)
            or not 1 <= quota <= 1_000_000_000
        ):
            raise DynamicResourceError("resource quota is invalid")
        if (
            isinstance(max_uses, bool)
            or not isinstance(max_uses, int)
            or not 1 <= max_uses <= 100
        ):
            raise DynamicResourceError("resource max uses must be between 1 and 100")
        expected_quota = (
            normalized_spec["max_bytes"]
            if normalized_class == "managed_cache"
            else 1
        )
        if quota != expected_quota:
            raise DynamicResourceError(
                "resource quota does not match the closed class specification"
            )
        normalized_mode = str(approval_mode or "manual").strip().lower()
        normalized_policy = str(policy or "public").strip().lower()
        if normalized_mode not in {"manual", "campaign_lease", "auto"}:
            raise DynamicResourceError("resource approval mode is invalid")
        if normalized_policy not in RESOURCE_POLICIES:
            raise DynamicResourceError("resource policy is invalid")
        if normalized_mode == "auto" and not (
            normalized_policy == "trusted_rosie"
            and normalized_class in TRUSTED_ROSIE_AUTO_RESOURCE_CLASSES
        ):
            raise DynamicResourceError(
                "automatic resources require reviewed Trusted Rosie low-risk policy"
            )
        lease_id = str(delegation_lease_id or "").strip() or None
        if normalized_mode == "campaign_lease" and not lease_id:
            raise DynamicResourceError("campaign lease approval requires a lease id")
        if normalized_mode != "campaign_lease" and lease_id:
            raise DynamicResourceError("unexpected delegation lease id")
        normalized_runtime = str(runtime_state or "").strip().lower()
        if normalized_runtime not in {"stopped", "running"}:
            raise DynamicResourceError("resource runtime state is invalid")
        provider = self._provider_for(normalized_class)
        authority = {
            "schema": DYNAMIC_RESOURCE_REQUEST_CONTRACT.schema,
            "resource_class": normalized_class,
            "requester_agent_id": requester,
            "operator_agent_id": operator,
            "campaign_id": campaign,
            "handoff_id": handoff,
            "workspace_set_digest": workspace_set_digest,
            "workerbee_binding_id": binding,
            "workerbee_backend_generation": generation,
            "spec": normalized_spec,
            "provider": provider,
            "ttl_seconds": int(ttl_seconds),
            "quota": int(quota),
            "max_uses": int(max_uses),
            "approval_mode": normalized_mode,
            "delegation_lease_id": lease_id,
            "policy": normalized_policy,
            "restart_required": normalized_runtime == "running",
        }
        digest = _sha256(_canonical_json(authority))
        initial_status = "approved" if normalized_mode == "auto" else "pending_approval"
        request, created = self.store.request_or_reuse_dynamic_resource(
            request_id=str(uuid.uuid4()),
            request_sha256=digest,
            resource_class=normalized_class,
            requester_agent_id=requester,
            operator_agent_id=operator,
            campaign_id=campaign,
            handoff_id=handoff,
            workspace_set_digest=workspace_set_digest,
            workerbee_binding_id=binding,
            workerbee_backend_generation=generation,
            spec=normalized_spec,
            provider=provider,
            ttl_seconds=int(ttl_seconds),
            quota=int(quota),
            max_uses=int(max_uses),
            status=initial_status,
            approval_mode=normalized_mode,
            delegation_lease_id=lease_id,
            restart_required=normalized_runtime == "running",
            expires_at=float(self.clock()) + int(ttl_seconds),
        )
        if initial_status == "approved" and created:
            request = self.store.update_dynamic_resource_request(
                request["request_id"],
                expected_statuses=("approved",),
                status="approved",
                operator_agent_id=operator,
                event_type="auto_approved",
                approval={
                    "method": "trusted-rosie-reviewed-low-risk/v1",
                    "request_sha256": digest,
                    "approved_at": self.clock(),
                },
            )
        return {
            **request,
            "created": created,
            "approval_confirmation": (
                resource_approval_confirmation(request)
                if request["status"] == "pending_approval"
                and request["approval_mode"] == "manual"
                else None
            ),
        }

    def approve(
        self,
        *,
        request_id: str,
        operator_agent_id: str,
        approval_method: str,
        confirmation: str,
        approve: bool = True,
    ) -> dict[str, Any]:
        self._require_enabled()
        request = self._request(request_id)
        if request["approval_mode"] != "manual":
            raise DynamicResourceError("request does not accept manual approval")
        if request["operator_agent_id"] != operator_agent_id:
            raise DynamicResourceError("resource approval operator mismatch")
        if request["expires_at"] <= self.clock():
            self.store.update_dynamic_resource_request(
                request_id,
                expected_statuses=("pending_approval",),
                status="expired",
                operator_agent_id=operator_agent_id,
                event_type="expired",
            )
            raise DynamicResourceError("dynamic resource request has expired")
        if approval_method != RESOURCE_APPROVAL_METHOD:
            raise DynamicResourceError("resource approval requires exact F8 method")
        if confirmation != resource_approval_confirmation(request):
            raise DynamicResourceError("resource approval confirmation mismatch")
        target = "approved" if approve else "rejected"
        receipt = {
            "schema": "agent-pbx.dynamic-resource-approval/v1",
            "approval_id": str(uuid.uuid4()),
            "request_id": request_id,
            "request_sha256": request["request_sha256"],
            "campaign_id": request["campaign_id"],
            "handoff_id": request["handoff_id"],
            "workspace_set_digest": request["workspace_set_digest"],
            "resource_class": request["resource_class"],
            "method": approval_method,
            "approved": bool(approve),
            "operator_agent_id": operator_agent_id,
            "approved_at": self.clock(),
            "expires_at": request["expires_at"],
        }
        return self.store.update_dynamic_resource_request(
            request_id,
            expected_statuses=("pending_approval",),
            status=target,
            operator_agent_id=operator_agent_id,
            event_type=target,
            approval=receipt,
        )

    def acquire(self, *, request_id: str, operator_agent_id: str) -> dict[str, Any]:
        self._require_enabled()
        request = self._request(request_id)
        if request["operator_agent_id"] != operator_agent_id:
            raise DynamicResourceError("resource acquisition operator mismatch")
        if request["expires_at"] <= self.clock():
            return self.store.update_dynamic_resource_request(
                request_id,
                expected_statuses=("pending_approval", "approved"),
                status="expired",
                operator_agent_id=operator_agent_id,
                event_type="expired",
            )
        if request["status"] == "pending_approval" and (
            request["approval_mode"] == "campaign_lease"
        ):
            self._consume_delegation_lease(request)
            request = self.store.update_dynamic_resource_request(
                request_id,
                expected_statuses=("pending_approval",),
                status="approved",
                operator_agent_id=operator_agent_id,
                event_type="campaign_lease_approved",
                approval={
                    "schema": "agent-pbx.dynamic-resource-approval/v1",
                    "request_id": request_id,
                    "request_sha256": request["request_sha256"],
                    "method": "bounded-campaign-lease/v1",
                    "delegation_lease_id": request["delegation_lease_id"],
                    "approved_at": self.clock(),
                },
            )
        if request["status"] != "approved":
            raise DynamicResourceError("dynamic resource is not approved")
        provider = self.providers.get(request["provider"])
        if provider is None:
            raise DynamicResourceError("dynamic resource provider is not configured")
        try:
            receipt = provider.acquire(request)
            self._validate_provider_receipt(request, receipt)
        except Exception as exc:
            self.store.update_dynamic_resource_request(
                request_id,
                expected_statuses=("approved",),
                status="recovery_required",
                operator_agent_id=operator_agent_id,
                event_type="acquire_recovery_required",
                event_metadata={"error_type": type(exc).__name__},
            )
            raise DynamicResourceError("resource provider acquisition failed") from exc
        return self.store.update_dynamic_resource_request(
            request_id,
            expected_statuses=("approved",),
            status="leased",
            operator_agent_id=operator_agent_id,
            event_type="leased",
            provider_receipt=receipt,
        )

    def use(
        self,
        *,
        request_id: str,
        requester_agent_id: str,
        operator_agent_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_enabled()
        request = self._request(request_id)
        if (
            operator_agent_id is not None
            and request["operator_agent_id"] != operator_agent_id
        ):
            raise DynamicResourceError("resource use operator mismatch")
        return self.store.consume_dynamic_resource_request(
            request_id,
            requester_agent_id=requester_agent_id,
        )

    def release(
        self,
        *,
        request_id: str,
        operator_agent_id: str,
        revoke: bool = False,
    ) -> dict[str, Any]:
        self._require_enabled()
        request = self._request(request_id)
        if request["operator_agent_id"] != operator_agent_id:
            raise DynamicResourceError("resource release operator mismatch")
        if request["status"] in {"released", "revoked"}:
            return request
        target = "revoked" if revoke else "released"
        if request["status"] in {"pending_approval", "approved"}:
            return self.store.update_dynamic_resource_request(
                request_id,
                expected_statuses=(request["status"],),
                status=target,
                operator_agent_id=operator_agent_id,
                event_type=target,
                cleanup_receipt=self._cleanup_receipt(request, "not-acquired"),
            )
        if request["status"] not in {"leased", "recovery_required"}:
            raise DynamicResourceError("dynamic resource cannot be released")
        provider = self.providers.get(request["provider"])
        if provider is None:
            return self.store.update_dynamic_resource_request(
                request_id,
                expected_statuses=(request["status"],),
                status="recovery_required",
                operator_agent_id=operator_agent_id,
                event_type="release_recovery_required",
                event_metadata={"reason": "provider unavailable"},
            )
        try:
            cleanup = provider.release(request)
            self._validate_cleanup_receipt(request, cleanup)
        except Exception as exc:
            self.store.update_dynamic_resource_request(
                request_id,
                expected_statuses=(request["status"],),
                status="recovery_required",
                operator_agent_id=operator_agent_id,
                event_type="release_recovery_required",
                event_metadata={"error_type": type(exc).__name__},
            )
            raise DynamicResourceError("resource cleanup requires recovery") from exc
        return self.store.update_dynamic_resource_request(
            request_id,
            expected_statuses=(request["status"],),
            status=target,
            operator_agent_id=operator_agent_id,
            event_type=target,
            cleanup_receipt=cleanup,
        )

    def recover(self, *, request_id: str, operator_agent_id: str) -> dict[str, Any]:
        self._require_enabled()
        request = self._request(request_id)
        if request["operator_agent_id"] != operator_agent_id:
            raise DynamicResourceError("resource recovery operator mismatch")
        if request["status"] not in {"leased", "recovery_required"}:
            raise DynamicResourceError("resource does not require recovery")
        provider = self.providers.get(request["provider"])
        receipt = provider.recover(request) if provider is not None else None
        if receipt is None:
            if request["status"] == "recovery_required":
                return request
            return self.store.update_dynamic_resource_request(
                request_id,
                expected_statuses=("leased",),
                status="recovery_required",
                operator_agent_id=operator_agent_id,
                event_type="restart_recovery_required",
            )
        self._validate_provider_receipt(request, receipt)
        if request["status"] == "leased":
            return request
        return self.store.update_dynamic_resource_request(
            request_id,
            expected_statuses=("recovery_required",),
            status="leased",
            operator_agent_id=operator_agent_id,
            event_type="recovered",
            provider_receipt=receipt,
        )

    def list(
        self,
        *,
        status: str | None = None,
        campaign_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        self._require_enabled()
        if status is not None and status not in RESOURCE_STATUSES:
            raise DynamicResourceError("dynamic resource status is invalid")
        requests = self.store.list_dynamic_resource_requests(
            status=status,
            campaign_id=campaign_id,
            limit=limit,
        )
        return [
            {
                **request,
                "approval_confirmation": (
                    resource_approval_confirmation(request)
                    if request["status"] == "pending_approval"
                    and request["approval_mode"] == "manual"
                    else None
                ),
            }
            for request in requests
        ]

    def _consume_delegation_lease(self, request: Mapping[str, Any]) -> None:
        lease_id = str(request.get("delegation_lease_id") or "")
        lease = self.store.get_operator_delegation_lease(lease_id)
        if lease is None or lease.get("status") != "approved" or lease.get("expired"):
            raise DynamicResourceError("resource delegation lease is not active")
        action = f"dynamic_resource:{request['resource_class']}"
        allowed = set(lease.get("allowed_action_classes") or [])
        if "dynamic_resource" not in allowed and action not in allowed:
            raise DynamicResourceError("delegation lease does not allow this resource class")
        if request.get("campaign_id") not in set(lease.get("campaign_ids") or []):
            raise DynamicResourceError("resource campaign differs from delegation lease")
        if lease.get("workspace_set_digest") != request.get("workspace_set_digest"):
            raise DynamicResourceError("resource workspace differs from delegation lease")
        targets = set(lease.get("target_operator_agent_ids") or [])
        if request.get("operator_agent_id") not in targets:
            raise DynamicResourceError("resource operator is outside the delegation lease")
        denied = set(lease.get("denied_actions") or [])
        if "dynamic_resource" in denied or action in denied:
            raise DynamicResourceError("resource action is denied by delegation lease")
        self.store.consume_operator_delegation_lease(
            lease_id,
            handoff_id=f"dynamic-resource:{request['request_id']}",
        )

    @staticmethod
    def _validate_spec(resource_class: str, spec: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(spec, Mapping):
            raise DynamicResourceError("resource specification must be an object")
        value = dict(spec)
        try:
            encoded = _canonical_json(value)
        except (TypeError, ValueError) as exc:
            raise DynamicResourceError("resource specification is not JSON-safe") from exc
        if len(encoded) > MAX_SPEC_BYTES:
            raise DynamicResourceError("resource specification exceeds the byte limit")
        if resource_class == "workspace_unix_socket":
            if set(value) != {"relative_path"}:
                raise DynamicResourceError("workspace socket fields are invalid")
            path = value.get("relative_path")
            if not isinstance(path, str):
                raise DynamicResourceError("workspace socket path is invalid")
            raw_parts = path.split("/")
            parsed = PurePosixPath(path)
            if (
                parsed.is_absolute()
                or not parsed.parts
                or any(part in {"", ".", ".."} for part in raw_parts)
                or parsed.suffix != ".sock"
                or len(path.encode("utf-8")) > 240
            ):
                raise DynamicResourceError("workspace socket path is unsafe")
            return {"relative_path": str(parsed)}
        if resource_class == "loopback_service":
            if set(value) != {"host", "port", "protocol"}:
                raise DynamicResourceError("loopback service fields are invalid")
            if value.get("host") not in {"127.0.0.1", "::1"}:
                raise DynamicResourceError("loopback service must use a numeric loopback host")
            DynamicResourceBroker._port(value.get("port"))
            if value.get("protocol") not in {"tcp", "udp"}:
                raise DynamicResourceError("loopback service protocol is invalid")
            return value
        if resource_class == "managed_cache":
            if set(value) != {"name", "max_bytes"}:
                raise DynamicResourceError("managed cache fields are invalid")
            if not SAFE_ID_RE.fullmatch(str(value.get("name") or "")):
                raise DynamicResourceError("managed cache name is invalid")
            size = value.get("max_bytes")
            if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= 1_000_000_000:
                raise DynamicResourceError("managed cache byte quota is invalid")
            return value
        if resource_class == "existing_workerbee_service":
            if set(value) != {"service_id"} or not SAFE_ID_RE.fullmatch(
                str(value.get("service_id") or "")
            ):
                raise DynamicResourceError("WorkerBee service identity is invalid")
            return value
        if resource_class == "port_reservation":
            if set(value) != {"port", "protocol"}:
                raise DynamicResourceError("port reservation fields are invalid")
            DynamicResourceBroker._port(value.get("port"))
            if value.get("protocol") not in {"tcp", "udp"}:
                raise DynamicResourceError("port reservation protocol is invalid")
            return value
        raise DynamicResourceError("unsupported resource class")

    @staticmethod
    def _port(value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not 1024 <= value <= 65535:
            raise DynamicResourceError("resource port is invalid")
        return value

    @staticmethod
    def _provider_for(resource_class: str) -> str:
        if resource_class == "existing_workerbee_service":
            return "workerbee-existing/v1"
        return "agent-pbx-local/v1"

    @staticmethod
    def _safe_id(value: object, label: str) -> str:
        normalized = str(value or "").strip()
        if not SAFE_ID_RE.fullmatch(normalized):
            raise DynamicResourceError(f"resource {label} identity is invalid")
        return normalized

    @staticmethod
    def _optional_safe_id(value: object, label: str) -> str | None:
        normalized = str(value or "").strip()
        if not normalized:
            return None
        if not SAFE_ID_RE.fullmatch(normalized):
            raise DynamicResourceError(f"resource {label} identity is invalid")
        return normalized

    @staticmethod
    def _uuid_or_none(value: object, label: str) -> str | None:
        normalized = str(value or "").strip()
        if not normalized:
            return None
        try:
            return str(uuid.UUID(normalized))
        except (ValueError, TypeError, AttributeError) as exc:
            raise DynamicResourceError(f"resource {label} identity is invalid") from exc

    def _request(self, request_id: str) -> dict[str, Any]:
        try:
            normalized = str(uuid.UUID(str(request_id)))
        except (ValueError, TypeError, AttributeError) as exc:
            raise DynamicResourceError("dynamic resource request id is invalid") from exc
        request = self.store.get_dynamic_resource_request(normalized)
        if request is None:
            raise DynamicResourceError("dynamic resource request not found")
        return request

    def _validate_request_authority(
        self,
        *,
        requester_agent_id: str,
        operator_agent_id: str,
        campaign_id: str | None,
        handoff_id: str | None,
        workspace_set_digest: str,
    ) -> None:
        requester = self.store.get_agent(requester_agent_id)
        if requester is None:
            raise DynamicResourceError("resource requester is not registered")
        operator = self.store.get_agent(operator_agent_id)
        if operator is None or operator.get("agent_type") != "operator":
            raise DynamicResourceError("resource operator is not registered")
        metadata = operator.get("metadata")
        activated_digest = (
            str(metadata.get("operator_authority_activated_workspace_set_digest") or "")
            if isinstance(metadata, Mapping)
            else ""
        )
        if activated_digest != workspace_set_digest:
            raise DynamicResourceError(
                "resource workspace differs from activated operator authority"
            )
        if campaign_id is not None:
            campaign = self.store.get_operator_campaign(campaign_id)
            if (
                campaign is None
                or campaign.get("operator_agent_id") != operator_agent_id
                or campaign.get("status") != "running"
            ):
                raise DynamicResourceError("resource campaign is not active for operator")
        if handoff_id is None:
            return
        handoff = self.store.get_operator_handoff(handoff_id)
        if handoff is None or handoff.get("status") not in {"acknowledged", "running"}:
            raise DynamicResourceError("resource handoff is not active")
        if operator_agent_id not in {
            handoff.get("source_agent_id"),
            handoff.get("target_operator_agent_id"),
        }:
            raise DynamicResourceError("resource handoff operator mismatch")
        execution_scope = handoff.get("execution_scope")
        if isinstance(execution_scope, Mapping):
            scope_digest = str(execution_scope.get("workspace_set_digest") or "")
            if scope_digest and scope_digest != workspace_set_digest:
                raise DynamicResourceError("resource handoff workspace mismatch")
            scope_campaign = str(execution_scope.get("campaign_id") or "")
            if campaign_id and scope_campaign and scope_campaign != campaign_id:
                raise DynamicResourceError("resource handoff campaign mismatch")

    def _validate_provider_receipt(
        self,
        request: Mapping[str, Any],
        receipt: Mapping[str, Any],
    ) -> None:
        expected = {
            "schema", "request_id", "request_sha256", "resource_class",
            "provider", "provider_generation", "resource_identity",
            "workspace_set_digest", "workerbee_binding_id",
            "workerbee_backend_generation", "spec_sha256", "quota",
            "max_uses", "activated_at", "expires_at", "restart_required",
        }
        activated_at = receipt.get("activated_at") if isinstance(receipt, Mapping) else None
        expected_spec_sha256 = _sha256(_canonical_json(request.get("spec") or {}))
        now = float(self.clock())
        if (
            not isinstance(receipt, Mapping)
            or set(receipt) != expected
            or receipt.get("schema") != RESOURCE_RECEIPT_SCHEMA
            or receipt.get("request_id") != request.get("request_id")
            or receipt.get("request_sha256") != request.get("request_sha256")
            or receipt.get("resource_class") != request.get("resource_class")
            or receipt.get("provider") != request.get("provider")
            or not self._receipt_text(receipt.get("provider_generation"), 120)
            or not self._receipt_text(receipt.get("resource_identity"), 240)
            or receipt.get("workspace_set_digest")
            != request.get("workspace_set_digest")
            or receipt.get("workerbee_binding_id")
            != request.get("workerbee_binding_id")
            or receipt.get("workerbee_backend_generation")
            != request.get("workerbee_backend_generation")
            or receipt.get("spec_sha256") != expected_spec_sha256
            or isinstance(receipt.get("quota"), bool)
            or receipt.get("quota") != request.get("quota")
            or isinstance(receipt.get("max_uses"), bool)
            or receipt.get("max_uses") != request.get("max_uses")
            or isinstance(activated_at, bool)
            or not isinstance(activated_at, (int, float))
            or float(activated_at) < float(request.get("created_at") or 0.0)
            or float(activated_at) > now + 5.0
            or isinstance(receipt.get("expires_at"), bool)
            or receipt.get("expires_at") != request.get("expires_at")
            or not isinstance(receipt.get("restart_required"), bool)
            or receipt.get("restart_required") != request.get("restart_required")
        ):
            raise DynamicResourceError("dynamic resource provider receipt is invalid")

    def _cleanup_receipt(self, request: Mapping[str, Any], result: str) -> dict[str, Any]:
        return {
            "schema": RESOURCE_CLEANUP_SCHEMA,
            "request_id": request["request_id"],
            "request_sha256": request["request_sha256"],
            "provider": request["provider"],
            "provider_generation": None,
            "resource_identity": None,
            "cleanup_complete": True,
            "result": result,
            "cleaned_at": self.clock(),
        }

    def _validate_cleanup_receipt(
        self,
        request: Mapping[str, Any],
        receipt: Mapping[str, Any],
    ) -> None:
        expected = {
            "schema", "request_id", "request_sha256", "provider",
            "provider_generation", "resource_identity", "cleanup_complete",
            "result", "cleaned_at",
        }
        provider_receipt = request.get("provider_receipt")
        cleaned_at = receipt.get("cleaned_at") if isinstance(receipt, Mapping) else None
        if (
            not isinstance(receipt, Mapping)
            or set(receipt) != expected
            or receipt.get("schema") != RESOURCE_CLEANUP_SCHEMA
            or receipt.get("request_id") != request.get("request_id")
            or receipt.get("request_sha256") != request.get("request_sha256")
            or receipt.get("provider") != request.get("provider")
            or not isinstance(provider_receipt, Mapping)
            or receipt.get("provider_generation")
            != provider_receipt.get("provider_generation")
            or receipt.get("resource_identity")
            != provider_receipt.get("resource_identity")
            or receipt.get("cleanup_complete") is not True
            or not self._receipt_text(receipt.get("result"), 120)
            or isinstance(cleaned_at, bool)
            or not isinstance(cleaned_at, (int, float))
            or float(cleaned_at) > float(self.clock()) + 5.0
        ):
            raise DynamicResourceError("dynamic resource cleanup receipt is invalid")

    @staticmethod
    def _receipt_text(value: object, limit: int) -> bool:
        return (
            isinstance(value, str)
            and 0 < len(value.encode("utf-8")) <= limit
            and value.isprintable()
        )

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise DynamicResourceError("dynamic resource broker is disabled")
