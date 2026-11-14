from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any, Mapping

from .workerbee_modes import (
    WORKERBEE_ACCESS_PROFILES,
    WORKERBEE_PROFILE_LOCAL_DEPLOY,
    WORKERBEE_PROFILE_OBSERVE,
    WORKERBEE_PROFILE_REVIEW,
    WORKERBEE_PROFILE_RANK,
    WORKERBEE_PROFILE_TRUSTED_WORKBENCH,
    canonical_workerbee_access_profile,
)


WORKERBEE_PROFILE_INTENT_API_VERSION = "agent-pbx.workerbee-profile-intent/v1"
WORKERBEE_PROFILE_METADATA_KEY = "workerbee_mcp_profile"
WORKERBEE_PROFILE_INTENT_METADATA_KEY = "workerbee_profile_intent"
WORKERBEE_PROFILE_CEILING_METADATA_KEY = "workerbee_access_profile_ceiling"
WORKERBEE_WORKSPACE_POLICY_DIGEST_METADATA_KEY = (
    "workerbee_workspace_policy_digest"
)
_SHA256 = re.compile(r"[0-9a-f]{64}")


class WorkerBeeProfileIntentError(ValueError):
    """A persisted WorkerBee launch profile is invalid or no longer authoritative."""


def _text(metadata: Mapping[str, Any], key: str) -> str:
    return str(metadata.get(key) or "").strip()


def has_explicit_workerbee_profile(metadata: Mapping[str, Any]) -> bool:
    """Return whether durable entity metadata owns an explicit profile choice."""

    return bool(
        metadata.get(WORKERBEE_PROFILE_INTENT_METADATA_KEY) is not None
        or _text(metadata, WORKERBEE_PROFILE_METADATA_KEY)
    )


def configured_operator_workerbee_profile(
    entity_id: str,
    metadata: Mapping[str, Any],
    *,
    configured_default: str,
    existing_entity: bool,
) -> str:
    """Select a root Operator profile without migrating existing records.

    Explicit durable metadata always wins.  The host default applies only to a
    newly created root Operator; legacy/live entities without explicit metadata
    retain the committed local-deploy posture until a separately reviewed
    migration changes their durable record.
    """

    if _text(metadata, "agent_type") != "operator":
        return WORKERBEE_PROFILE_LOCAL_DEPLOY
    if (
        _text(metadata, "access_mode") == "review_readonly"
        or _text(metadata, "fork_purpose") == "review"
    ):
        return WORKERBEE_PROFILE_REVIEW
    if has_explicit_workerbee_profile(metadata):
        return resolve_workerbee_profile_intent(entity_id, metadata).profile
    if existing_entity:
        return WORKERBEE_PROFILE_LOCAL_DEPLOY
    selected = canonical_workerbee_access_profile(configured_default)
    if selected not in {
        WORKERBEE_PROFILE_OBSERVE,
        WORKERBEE_PROFILE_LOCAL_DEPLOY,
        WORKERBEE_PROFILE_TRUSTED_WORKBENCH,
    }:
        raise WorkerBeeProfileIntentError(
            "effective Operator WorkerBee default must be observe-local, "
            "local-deploy, or trusted-workbench"
        )
    return selected


@dataclass(frozen=True)
class WorkerBeeProfileIntent:
    entity_id: str
    profile: str
    access_mode: str
    profile_ceiling: str
    workspace_policy_digest: str
    source_entity_id: str
    api_version: str = WORKERBEE_PROFILE_INTENT_API_VERSION

    def public_dict(self) -> dict[str, str]:
        return asdict(self)


def build_workerbee_profile_intent(
    entity_id: str,
    metadata: Mapping[str, Any],
    *,
    profile: str | None = None,
    source_entity_id: str | None = None,
) -> WorkerBeeProfileIntent:
    """Build a launch intent from current durable authority evidence.

    Trusted Workbench is intentionally unavailable to ordinary callers and to
    review forks.  Its authorization is independent from Codex/filesystem/Git
    authority: an Operator needs an explicit WorkerBee ceiling plus a durable
    WorkerBee workspace-policy root-set digest.
    """

    resolved_entity_id = str(entity_id or "").strip()
    if not resolved_entity_id:
        raise WorkerBeeProfileIntentError("WorkerBee profile intent has no entity id")
    access_mode = _text(metadata, "access_mode")
    requested = str(profile if profile is not None else metadata.get(WORKERBEE_PROFILE_METADATA_KEY) or "").strip()
    if access_mode == "review_readonly":
        if requested == WORKERBEE_PROFILE_TRUSTED_WORKBENCH:
            raise WorkerBeeProfileIntentError(
                "review-readonly forks cannot inherit trusted-workbench"
            )
        selected = WORKERBEE_PROFILE_REVIEW
    else:
        selected = canonical_workerbee_access_profile(
            requested or WORKERBEE_PROFILE_LOCAL_DEPLOY
        )
        if selected == WORKERBEE_PROFILE_REVIEW:
            raise WorkerBeeProfileIntentError(
                "review-readonly is derived from access_mode, not selectable"
            )

    ceiling = _text(metadata, WORKERBEE_PROFILE_CEILING_METADATA_KEY)
    if not ceiling:
        ceiling = WORKERBEE_PROFILE_LOCAL_DEPLOY
    ceiling = canonical_workerbee_access_profile(ceiling)
    if ceiling == WORKERBEE_PROFILE_REVIEW:
        raise WorkerBeeProfileIntentError(
            "review-readonly cannot be used as a WorkerBee profile ceiling"
        )
    if WORKERBEE_PROFILE_RANK[selected] > WORKERBEE_PROFILE_RANK[ceiling]:
        raise WorkerBeeProfileIntentError(
            "WorkerBee profile exceeds the independent administrator ceiling"
        )
    workspace_policy_digest = _text(
        metadata, WORKERBEE_WORKSPACE_POLICY_DIGEST_METADATA_KEY
    )
    if selected == WORKERBEE_PROFILE_TRUSTED_WORKBENCH:
        if _text(metadata, "agent_type") != "operator":
            raise WorkerBeeProfileIntentError(
                "trusted-workbench is restricted to Operator entities"
            )
        if access_mode == "review_readonly" or _text(metadata, "fork_purpose") == "review":
            raise WorkerBeeProfileIntentError(
                "review forks cannot receive trusted-workbench"
            )
        if not _SHA256.fullmatch(workspace_policy_digest):
            raise WorkerBeeProfileIntentError(
                "trusted-workbench requires a valid WorkerBee workspace-policy digest"
            )

    return WorkerBeeProfileIntent(
        entity_id=resolved_entity_id,
        profile=selected,
        access_mode=access_mode,
        profile_ceiling=ceiling,
        workspace_policy_digest=workspace_policy_digest,
        source_entity_id=str(source_entity_id or resolved_entity_id).strip(),
    )


def resolve_workerbee_profile_intent(
    entity_id: str,
    metadata: Mapping[str, Any],
) -> WorkerBeeProfileIntent:
    """Resolve and revalidate a durable profile intent.

    Legacy entities without an intent retain the committed local-deploy default.
    An explicit trusted-workbench profile, however, is never accepted without
    the versioned intent that binds the authority and workspace-set identities.
    """

    raw = metadata.get(WORKERBEE_PROFILE_INTENT_METADATA_KEY)
    explicit_profile = _text(metadata, WORKERBEE_PROFILE_METADATA_KEY)
    if raw is None:
        if explicit_profile == WORKERBEE_PROFILE_TRUSTED_WORKBENCH:
            raise WorkerBeeProfileIntentError(
                "trusted-workbench metadata is missing its durable profile intent"
            )
        return build_workerbee_profile_intent(entity_id, metadata)
    if not isinstance(raw, Mapping):
        raise WorkerBeeProfileIntentError("WorkerBee profile intent must be an object")
    if set(raw) != {
        "api_version",
        "entity_id",
        "profile",
        "access_mode",
        "profile_ceiling",
        "workspace_policy_digest",
        "source_entity_id",
    }:
        raise WorkerBeeProfileIntentError("WorkerBee profile intent has an invalid field set")
    if _text(raw, "api_version") != WORKERBEE_PROFILE_INTENT_API_VERSION:
        raise WorkerBeeProfileIntentError("WorkerBee profile intent version is unsupported")
    persisted_profile = _text(raw, "profile")
    if persisted_profile not in WORKERBEE_ACCESS_PROFILES:
        raise WorkerBeeProfileIntentError("WorkerBee profile intent contains an invalid profile")
    current = build_workerbee_profile_intent(
        entity_id,
        metadata,
        profile=persisted_profile,
        source_entity_id=_text(raw, "source_entity_id"),
    )
    if current.public_dict() != {key: str(value) for key, value in raw.items()}:
        raise WorkerBeeProfileIntentError(
            "WorkerBee profile intent no longer matches current entity authority"
        )
    if explicit_profile and explicit_profile != current.profile:
        raise WorkerBeeProfileIntentError(
            "WorkerBee profile metadata does not match its durable intent"
        )
    return current
