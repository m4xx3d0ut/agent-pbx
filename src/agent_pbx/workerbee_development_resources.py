from __future__ import annotations

import hashlib
import json
import math
import re
import time
from typing import Any, Mapping, Protocol

from .contracts import WORKERBEE_DEVELOPMENT_CAPABILITY_BOUNDARY
from .dynamic_resources import (
    RESOURCE_CLEANUP_SCHEMA,
    RESOURCE_RECEIPT_SCHEMA,
    DynamicResourceError,
)


PROVIDER_ID = "workerbee-development/v1"
PROVIDER_CAPABILITY_SCHEMA = "agent-pbx.workerbee-development-capabilities/v1"
PROVIDER_REQUEST_SCHEMA = "agent-pbx.workerbee-development-resource/v1"
PROVIDER_RECEIPT_SCHEMA = "workerbee.platform-debug-development-resource/v1"
PROVIDER_CLEANUP_SCHEMA = (
    "workerbee.platform-debug-development-resource-cleanup/v1"
)
RESOURCE_CLASSES = (
    "build_scratch",
    "generated_output_staging",
    "managed_cache",
    "managed_venv",
    "service_state",
    "unix_socket_runtime",
)
MOUNT_TARGETS = {
    "build_scratch": "/workspace/.agent-pbx-dev/build",
    "generated_output_staging": "/workspace/.agent-pbx-dev/output",
    "managed_cache": "/workspace/.agent-pbx-dev/cache",
    "managed_venv": "/workspace/.agent-pbx-dev/venv",
    "service_state": "/workspace/.agent-pbx-dev/state",
    "unix_socket_runtime": "/workspace/.agent-pbx-dev/run",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")


class WorkerBeeDevelopmentAdapter(Protocol):
    """Private runtime adapter; public Agent PBX supplies no implementation."""

    def capabilities(
        self,
        *,
        workspace_binding_id: str,
        backend_generation: str,
    ) -> Mapping[str, Any]: ...

    def acquire(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def release(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def status(self, request: Mapping[str, Any]) -> Mapping[str, Any] | None: ...


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def unsigned_digest(payload: Mapping[str, Any], field: str) -> str:
    unsigned = dict(payload)
    unsigned.pop(field, None)
    return sha256(canonical_json(unsigned))


def _safe_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not SAFE_ID_RE.fullmatch(value):
        raise DynamicResourceError(f"WorkerBee {label} identity is invalid")
    return value


def _exact_int(value: object, label: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DynamicResourceError(f"WorkerBee {label} must be an integer")
    if not minimum <= value <= maximum:
        raise DynamicResourceError(f"WorkerBee {label} is outside its limit")
    return value


def _timestamp(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DynamicResourceError(f"WorkerBee {label} timestamp is invalid")
    result = float(value)
    if not math.isfinite(result):
        raise DynamicResourceError(f"WorkerBee {label} timestamp is invalid")
    return result


class WorkerBeeDevelopmentProvider:
    """Fail-closed adapter for generation-bound writable development overlays."""

    provider_id = PROVIDER_ID

    def __init__(self, adapter: WorkerBeeDevelopmentAdapter, *, clock: Any = time.time):
        self.adapter = adapter
        self.clock = clock

    def qualify_request(self, authority: Mapping[str, Any]) -> None:
        capability = self._capability(authority)
        if capability["capability_sha256"] != authority.get(
            "provider_capability_sha256"
        ):
            raise DynamicResourceError("WorkerBee capability digest mismatch")
        resource_class = str(authority.get("resource_class") or "")
        if resource_class not in RESOURCE_CLASSES:
            raise DynamicResourceError("WorkerBee development class is not qualified")
        if authority.get("approval_mode") == "auto":
            raise DynamicResourceError("WorkerBee development resources require approval")
        spec = authority.get("spec")
        quota = authority.get("quota")
        if not isinstance(spec, Mapping) or quota != spec.get("max_bytes"):
            raise DynamicResourceError("WorkerBee development quota mismatch")
        if quota > capability["max_quota_bytes"]:
            raise DynamicResourceError("WorkerBee development quota exceeds capability")
        if authority.get("ttl_seconds", 0) > capability["max_ttl_seconds"]:
            raise DynamicResourceError("WorkerBee development TTL exceeds capability")

    def acquire(self, request: Mapping[str, Any]) -> dict[str, Any]:
        capability = self._capability(request)
        self._require_capability_link(request, capability)
        provider_receipt = self.adapter.acquire(self._provider_request(request))
        validated = self._validate_provider_receipt(
            request,
            capability,
            provider_receipt,
        )
        metadata = {
            "provider_receipt_schema": validated["schema"],
            "provider_receipt_sha256": validated["receipt_sha256"],
            "mount_target": validated["mount_target"],
            "owner_uid": validated["owner_uid"],
            "owner_gid": validated["owner_gid"],
            "mode": validated["mode"],
            "source_mount_read_only": validated["source_mount_read_only"],
            "source_mount_rprivate": validated["source_mount_rprivate"],
            "writable_source": validated["writable_source"],
        }
        return {
            "schema": RESOURCE_RECEIPT_SCHEMA,
            "request_id": request["request_id"],
            "request_sha256": request["request_sha256"],
            "resource_class": request["resource_class"],
            "provider": request["provider"],
            "provider_generation": validated["provider_generation"],
            "resource_identity": validated["provider_resource_id"],
            "workspace_set_digest": request["workspace_set_digest"],
            "workerbee_binding_id": request["workerbee_binding_id"],
            "workerbee_backend_generation": request[
                "workerbee_backend_generation"
            ],
            "provider_capability_sha256": request[
                "provider_capability_sha256"
            ],
            "spec_sha256": sha256(canonical_json(request["spec"])),
            "resource_metadata": metadata,
            "resource_metadata_sha256": sha256(canonical_json(metadata)),
            "quota": request["quota"],
            "max_uses": request["max_uses"],
            "activated_at": validated["activated_at"],
            "expires_at": request["expires_at"],
            "restart_required": request["restart_required"],
        }

    def release(self, request: Mapping[str, Any]) -> dict[str, Any]:
        capability = self._capability(request)
        self._require_capability_link(request, capability)
        provider_receipt = request.get("provider_receipt")
        if not isinstance(provider_receipt, Mapping):
            raise DynamicResourceError("WorkerBee provider receipt is missing")
        metadata = provider_receipt.get("resource_metadata")
        if not isinstance(metadata, Mapping):
            raise DynamicResourceError("WorkerBee provider metadata is missing")
        release_request = {
            "schema": PROVIDER_REQUEST_SCHEMA,
            "operation": "release",
            "request_id": request["request_id"],
            "request_sha256": request["request_sha256"],
            "provider_resource_id": provider_receipt["resource_identity"],
            "provider_receipt_sha256": metadata.get("provider_receipt_sha256"),
            "provider_capability_sha256": request[
                "provider_capability_sha256"
            ],
            "workspace_binding_id": request["workerbee_binding_id"],
            "backend_generation": request["workerbee_backend_generation"],
        }
        cleanup = self._validate_cleanup(request, self.adapter.release(release_request))
        return {
            "schema": RESOURCE_CLEANUP_SCHEMA,
            "request_id": request["request_id"],
            "request_sha256": request["request_sha256"],
            "provider": request["provider"],
            "provider_generation": cleanup["provider_generation"],
            "resource_identity": cleanup["provider_resource_id"],
            "cleanup_complete": True,
            "result": "provider-released-zero-residue",
            "cleaned_at": cleanup["released_at"],
        }

    def recover(self, request: Mapping[str, Any]) -> dict[str, Any] | None:
        capability = self._capability(request)
        self._require_capability_link(request, capability)
        provider_receipt = request.get("provider_receipt")
        if not isinstance(provider_receipt, Mapping):
            return None
        metadata = provider_receipt.get("resource_metadata")
        if not isinstance(metadata, Mapping):
            return None
        status_request = {
            "schema": PROVIDER_REQUEST_SCHEMA,
            "operation": "status",
            "request_id": request["request_id"],
            "request_sha256": request["request_sha256"],
            "provider_resource_id": provider_receipt["resource_identity"],
            "provider_receipt_sha256": metadata.get("provider_receipt_sha256"),
            "provider_capability_sha256": request[
                "provider_capability_sha256"
            ],
            "workspace_binding_id": request["workerbee_binding_id"],
            "backend_generation": request["workerbee_backend_generation"],
        }
        current = self.adapter.status(status_request)
        if current is None:
            return None
        validated = self._validate_provider_receipt(request, capability, current)
        immutable_metadata = {
            "mount_target": validated["mount_target"],
            "owner_uid": validated["owner_uid"],
            "owner_gid": validated["owner_gid"],
            "mode": validated["mode"],
            "source_mount_read_only": validated["source_mount_read_only"],
            "source_mount_rprivate": validated["source_mount_rprivate"],
            "writable_source": validated["writable_source"],
        }
        if (
            validated["provider_resource_id"]
            != provider_receipt.get("resource_identity")
            or validated["provider_generation"]
            != provider_receipt.get("provider_generation")
            or any(metadata.get(key) != value for key, value in immutable_metadata.items())
        ):
            raise DynamicResourceError("WorkerBee recovered resource identity drifted")
        updated_metadata = dict(metadata)
        updated_metadata["provider_receipt_sha256"] = validated["receipt_sha256"]
        recovered = dict(provider_receipt)
        recovered.update(
            {
                "provider_generation": validated["provider_generation"],
                "resource_identity": validated["provider_resource_id"],
                "resource_metadata": updated_metadata,
                "resource_metadata_sha256": sha256(
                    canonical_json(updated_metadata)
                ),
                "activated_at": validated["activated_at"],
            }
        )
        return recovered

    def _capability(self, request: Mapping[str, Any]) -> dict[str, Any]:
        binding = _safe_id(request.get("workerbee_binding_id"), "binding")
        generation = _safe_id(
            request.get("workerbee_backend_generation"),
            "backend generation",
        )
        payload = self.adapter.capabilities(
            workspace_binding_id=binding,
            backend_generation=generation,
        )
        return self._validate_capability(
            payload,
            workspace_binding_id=binding,
            backend_generation=generation,
        )

    def _validate_capability(
        self,
        payload: Mapping[str, Any],
        *,
        workspace_binding_id: str,
        backend_generation: str,
    ) -> dict[str, Any]:
        expected = {
            "schema",
            "provider",
            "provider_generation",
            "backend",
            "backend_instance",
            "backend_generation",
            "workspace_binding_id",
            "resource_classes",
            "mount_targets",
            "request_schema",
            "receipt_schema",
            "cleanup_schema",
            "trusted_direct_bind_read_only",
            "trusted_direct_bind_rprivate",
            "writable_source",
            "max_ttl_seconds",
            "max_quota_bytes",
            "issued_at",
            "expires_at",
            "capability_sha256",
        }
        if not isinstance(payload, Mapping) or set(payload) != expected:
            raise DynamicResourceError("WorkerBee capability fields are invalid")
        result = dict(payload)
        try:
            WORKERBEE_DEVELOPMENT_CAPABILITY_BOUNDARY.validate_provider_evidence(
                result
            )
        except ValueError as exc:
            raise DynamicResourceError(str(exc)) from exc
        if (
            result.get("provider") != PROVIDER_ID
            or result.get("backend") != "platform_debug"
            or result.get("backend_generation") != backend_generation
            or result.get("workspace_binding_id") != workspace_binding_id
            or result.get("resource_classes") != list(RESOURCE_CLASSES)
            or result.get("mount_targets") != MOUNT_TARGETS
            or result.get("request_schema") != PROVIDER_REQUEST_SCHEMA
            or result.get("receipt_schema") != PROVIDER_RECEIPT_SCHEMA
            or result.get("cleanup_schema") != PROVIDER_CLEANUP_SCHEMA
        ):
            raise DynamicResourceError("WorkerBee capability identity mismatch")
        _safe_id(result.get("provider_generation"), "provider generation")
        _safe_id(result.get("backend_instance"), "backend instance")
        result["max_ttl_seconds"] = _exact_int(
            result.get("max_ttl_seconds"),
            "maximum TTL",
            minimum=60,
            maximum=86400,
        )
        result["max_quota_bytes"] = _exact_int(
            result.get("max_quota_bytes"),
            "maximum quota",
            minimum=1,
            maximum=1_000_000_000,
        )
        issued_at = _timestamp(result.get("issued_at"), "capability issue")
        expires_at = _timestamp(result.get("expires_at"), "capability expiry")
        now = float(self.clock())
        if (
            issued_at > now + 5.0
            or expires_at <= now
            or expires_at <= issued_at
            or expires_at - issued_at > 86400
        ):
            raise DynamicResourceError("WorkerBee capability is stale")
        digest = result.get("capability_sha256")
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise DynamicResourceError("WorkerBee capability digest is invalid")
        if digest != unsigned_digest(result, "capability_sha256"):
            raise DynamicResourceError("WorkerBee capability digest mismatch")
        return result

    @staticmethod
    def _require_capability_link(
        request: Mapping[str, Any],
        capability: Mapping[str, Any],
    ) -> None:
        if request.get("provider_capability_sha256") != capability.get(
            "capability_sha256"
        ):
            raise DynamicResourceError("WorkerBee capability changed")

    @staticmethod
    def _provider_request(request: Mapping[str, Any]) -> dict[str, Any]:
        resource_class = str(request["resource_class"])
        return {
            "schema": PROVIDER_REQUEST_SCHEMA,
            "operation": "acquire",
            "request_id": request["request_id"],
            "request_sha256": request["request_sha256"],
            "campaign_id": request.get("campaign_id"),
            "handoff_id": request.get("handoff_id"),
            "workspace_set_digest": request["workspace_set_digest"],
            "workspace_binding_id": request["workerbee_binding_id"],
            "backend_generation": request["workerbee_backend_generation"],
            "provider_capability_sha256": request[
                "provider_capability_sha256"
            ],
            "resource_class": resource_class,
            "mount_target": MOUNT_TARGETS[resource_class],
            "quota_bytes": request["quota"],
            "ttl_seconds": request["ttl_seconds"],
            "expires_at": request["expires_at"],
            "restart_required": request["restart_required"],
        }

    def _validate_provider_receipt(
        self,
        request: Mapping[str, Any],
        capability: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        expected = {
            "schema",
            "status",
            "request_id",
            "request_sha256",
            "provider",
            "provider_generation",
            "provider_capability_sha256",
            "resource_class",
            "provider_resource_id",
            "workspace_binding_id",
            "backend_generation",
            "mount_target",
            "owner_uid",
            "owner_gid",
            "mode",
            "quota_bytes",
            "source_mount_read_only",
            "source_mount_rprivate",
            "writable_source",
            "activated_at",
            "expires_at",
            "receipt_sha256",
        }
        if not isinstance(payload, Mapping) or set(payload) != expected:
            raise DynamicResourceError("WorkerBee provider receipt fields are invalid")
        result = dict(payload)
        resource_class = str(request.get("resource_class") or "")
        if (
            result.get("schema") != PROVIDER_RECEIPT_SCHEMA
            or result.get("status") != "acquired"
            or result.get("request_id") != request.get("request_id")
            or result.get("request_sha256") != request.get("request_sha256")
            or result.get("provider") != PROVIDER_ID
            or result.get("provider_generation")
            != capability.get("provider_generation")
            or result.get("provider_capability_sha256")
            != request.get("provider_capability_sha256")
            or result.get("resource_class") != resource_class
            or result.get("workspace_binding_id")
            != request.get("workerbee_binding_id")
            or result.get("backend_generation")
            != request.get("workerbee_backend_generation")
            or result.get("mount_target") != MOUNT_TARGETS.get(resource_class)
            or isinstance(result.get("quota_bytes"), bool)
            or result.get("quota_bytes") != request.get("quota")
            or result.get("source_mount_read_only") is not True
            or result.get("source_mount_rprivate") is not True
            or result.get("writable_source") is not False
            or result.get("expires_at") != request.get("expires_at")
        ):
            raise DynamicResourceError("WorkerBee provider receipt identity mismatch")
        _safe_id(result.get("provider_resource_id"), "provider resource")
        _exact_int(result.get("owner_uid"), "owner uid", minimum=0, maximum=2**31)
        _exact_int(result.get("owner_gid"), "owner gid", minimum=0, maximum=2**31)
        if result.get("mode") != 0o700 or isinstance(result.get("mode"), bool):
            raise DynamicResourceError("WorkerBee resource mode must be 0700")
        activated_at = _timestamp(result.get("activated_at"), "resource activation")
        now = float(self.clock())
        if (
            activated_at < float(request.get("created_at") or 0.0)
            or activated_at > now + 5.0
        ):
            raise DynamicResourceError("WorkerBee resource activation time is invalid")
        digest = result.get("receipt_sha256")
        if not isinstance(digest, str) or digest != unsigned_digest(
            result,
            "receipt_sha256",
        ):
            raise DynamicResourceError("WorkerBee provider receipt digest mismatch")
        return result

    def _validate_cleanup(
        self,
        request: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        expected = {
            "schema",
            "status",
            "request_id",
            "request_sha256",
            "provider",
            "provider_generation",
            "provider_capability_sha256",
            "provider_resource_id",
            "workspace_binding_id",
            "backend_generation",
            "cleanup_complete",
            "residue_count",
            "released_at",
            "receipt_sha256",
        }
        if not isinstance(payload, Mapping) or set(payload) != expected:
            raise DynamicResourceError("WorkerBee cleanup receipt fields are invalid")
        result = dict(payload)
        provider_receipt = request.get("provider_receipt")
        if not isinstance(provider_receipt, Mapping):
            raise DynamicResourceError("WorkerBee provider receipt is missing")
        if (
            result.get("schema") != PROVIDER_CLEANUP_SCHEMA
            or result.get("status") != "released"
            or result.get("request_id") != request.get("request_id")
            or result.get("request_sha256") != request.get("request_sha256")
            or result.get("provider") != PROVIDER_ID
            or result.get("provider_generation")
            != provider_receipt.get("provider_generation")
            or result.get("provider_capability_sha256")
            != request.get("provider_capability_sha256")
            or result.get("provider_resource_id")
            != provider_receipt.get("resource_identity")
            or result.get("workspace_binding_id")
            != request.get("workerbee_binding_id")
            or result.get("backend_generation")
            != request.get("workerbee_backend_generation")
            or result.get("cleanup_complete") is not True
            or isinstance(result.get("residue_count"), bool)
            or result.get("residue_count") != 0
        ):
            raise DynamicResourceError("WorkerBee cleanup receipt is invalid")
        released_at = _timestamp(result.get("released_at"), "resource release")
        if (
            released_at
            < float(provider_receipt.get("activated_at") or 0.0)
            or released_at > float(self.clock()) + 5.0
        ):
            raise DynamicResourceError("WorkerBee resource release time is invalid")
        digest = result.get("receipt_sha256")
        if not isinstance(digest, str) or digest != unsigned_digest(
            result,
            "receipt_sha256",
        ):
            raise DynamicResourceError("WorkerBee cleanup receipt digest mismatch")
        return result
