from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import os
from pathlib import Path
import stat
from typing import Any, Iterable, Mapping

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback.
    import tomli as tomllib  # type: ignore[no-redef]

from ..codex_cli import CodexModelOption
from .profiles import (
    DEFAULT_CALLER_PROFILE_ID,
    DEFAULT_OPERATOR_PROFILE_ID,
    MANAGED_CODEX_PROFILE_ORDER,
    ManagedCodexProfile,
    managed_profile_for,
)


CODEX_MODEL_POLICY_FILE_ENV = "AGENT_PBX_CODEX_MODEL_POLICY_FILE"
CODEX_MODEL_POLICY_CONTROL_MODE_ENV = "AGENT_PBX_CODEX_MODEL_POLICY_CONTROL_MODE"
CODEX_MODEL_CALLER_PROFILE_ENV = "AGENT_PBX_CODEX_MODEL_CALLER_PROFILE"
CODEX_MODEL_OPERATOR_PROFILE_ENV = "AGENT_PBX_CODEX_MODEL_OPERATOR_PROFILE"
CODEX_MODEL_EDIT_FORK_PROFILE_ENV = "AGENT_PBX_CODEX_MODEL_EDIT_FORK_PROFILE"
CODEX_MODEL_REVIEW_FORK_PROFILE_ENV = "AGENT_PBX_CODEX_MODEL_REVIEW_FORK_PROFILE"
CODEX_MODEL_EFFICIENT_PROFILE_ENV = "AGENT_PBX_CODEX_MODEL_EFFICIENT_PROFILE"
CODEX_MODEL_VISUAL_PROFILE_ENV = "AGENT_PBX_CODEX_MODEL_VISUAL_PROFILE"
CODEX_MODEL_ELEVATION_PROFILES_ENV = "AGENT_PBX_CODEX_MODEL_ELEVATION_PROFILES"


class CodexModelControlMode(str, Enum):
    OBSERVE = "observe"
    GUARD_ELEVATED = "guard_elevated"
    ENFORCED = "enforced"


class CodexModelSelectionClass(str, Enum):
    MANAGED_QUALIFIED = "managed_qualified"
    CATALOG_OBSERVED = "catalog_observed"
    CUSTOM_PROVIDER_OBSERVED = "custom_provider_observed"
    CATALOG_STALE = "catalog_stale"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CodexModelPolicy:
    control_mode: CodexModelControlMode
    caller_profile_id: str
    operator_profile_id: str
    edit_fork_profile_id: str
    review_fork_profile_id: str
    efficient_profile_id: str
    visual_profile_id: str
    elevation_profile_ids: tuple[str, ...]
    source: str = "committed-default"
    source_sha256: str = ""
    warnings: tuple[str, ...] = ()

    def profile_id_for_role(self, role: str) -> str:
        normalized = str(role or "caller").strip().lower().replace("-", "_")
        if normalized in {"operator", "root_operator", "root"}:
            return self.operator_profile_id
        if normalized in {"edit_fork", "fork", "operator_fork"}:
            return self.edit_fork_profile_id
        if normalized in {"review", "review_fork"}:
            return self.review_fork_profile_id
        return self.caller_profile_id

    def profile_for_role(self, role: str) -> ManagedCodexProfile:
        profile = managed_profile_for(self.profile_id_for_role(role))
        if profile is None:  # Defensive: policy construction validates this.
            raise ValueError(f"model policy role {role!r} has no managed profile")
        return profile

    def public_dict(self) -> dict[str, Any]:
        return {
            "control_mode": self.control_mode.value,
            "role_defaults": {
                "caller": self.caller_profile_id,
                "operator": self.operator_profile_id,
                "edit_fork": self.edit_fork_profile_id,
                "review_fork": self.review_fork_profile_id,
            },
            "task_profiles": {
                "efficient": self.efficient_profile_id,
                "visual": self.visual_profile_id,
            },
            "elevation_profiles": list(self.elevation_profile_ids),
            "source": self.source,
            "source_sha256": self.source_sha256 or None,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class CodexModelSelection:
    model: str
    reasoning_effort: str
    model_provider: str
    classification: CodexModelSelectionClass
    managed_profile_id: str | None
    requires_approval: bool
    allowed: bool
    reason: str

    def public_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "model_provider": self.model_provider or None,
            "classification": self.classification.value,
            "managed_profile_id": self.managed_profile_id,
            "requires_approval": self.requires_approval,
            "allowed": self.allowed,
            "reason": self.reason,
        }


PUBLIC_CODEX_MODEL_POLICY = CodexModelPolicy(
    control_mode=CodexModelControlMode.GUARD_ELEVATED,
    caller_profile_id=DEFAULT_CALLER_PROFILE_ID,
    operator_profile_id=DEFAULT_OPERATOR_PROFILE_ID,
    edit_fork_profile_id=DEFAULT_OPERATOR_PROFILE_ID,
    review_fork_profile_id=DEFAULT_OPERATOR_PROFILE_ID,
    efficient_profile_id="luna-6-high",
    visual_profile_id="terra-6.1-high",
    elevation_profile_ids=(
        "sol-6.2-max",
        "sol-6.2-ultra",
        "astra-6.2-xhigh",
        "astra-6.2-max",
        "astra-6.2-ultra",
    ),
)


def default_codex_model_policy_path(
    environ: Mapping[str, str] | None = None,
) -> Path:
    env = os.environ if environ is None else environ
    configured = str(env.get(CODEX_MODEL_POLICY_FILE_ENV) or "").strip()
    if configured:
        return Path(configured).expanduser()
    config_home = str(env.get("XDG_CONFIG_HOME") or "").strip()
    root = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return root / "agent-pbx" / "model-policy.toml"


def load_codex_model_policy(
    *,
    environ: Mapping[str, str] | None = None,
    path: Path | None = None,
    strict: bool = False,
) -> CodexModelPolicy:
    """Load owner-local model policy over safe committed defaults.

    Invalid local values degrade to the corresponding committed default unless
    ``strict`` is requested. This keeps a stale workstation override from
    making the daemon or TUI unstartable after a Codex catalog migration.
    """

    env = dict(os.environ if environ is None else environ)
    policy_path = (path or default_codex_model_policy_path(env)).expanduser()
    data: Mapping[str, Any] = {}
    source = PUBLIC_CODEX_MODEL_POLICY.source
    digest = ""
    warnings: list[str] = []
    if policy_path.is_file():
        try:
            raw = policy_path.read_bytes()
            parsed = tomllib.loads(raw.decode("utf-8"))
            if not isinstance(parsed, Mapping):
                raise ValueError("model policy root must be a TOML table")
            data = parsed
            source = str(policy_path.resolve(strict=False))
            digest = hashlib.sha256(raw).hexdigest()
            mode = stat.S_IMODE(policy_path.stat().st_mode)
            if mode & 0o077:
                warnings.append(
                    f"Local model policy {policy_path} is readable by group or others; "
                    "use mode 0600 for workstation-private policy."
                )
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError) as exc:
            if strict:
                raise ValueError(f"unable to load Codex model policy: {exc}") from exc
            warnings.append(f"Ignoring invalid local model policy: {exc}")
            data = {}

    defaults = _table(data, "defaults")
    tasks = _table(data, "tasks")
    elevations = _table(data, "elevations")
    raw_mode = str(
        env.get(CODEX_MODEL_POLICY_CONTROL_MODE_ENV)
        or data.get("control_mode")
        or PUBLIC_CODEX_MODEL_POLICY.control_mode.value
    ).strip()
    try:
        control_mode = CodexModelControlMode(raw_mode)
    except ValueError as exc:
        if strict:
            raise ValueError(f"unsupported Codex model control mode: {raw_mode!r}") from exc
        warnings.append(
            f"Unsupported Codex model control mode {raw_mode!r}; using "
            f"{PUBLIC_CODEX_MODEL_POLICY.control_mode.value!r}."
        )
        control_mode = PUBLIC_CODEX_MODEL_POLICY.control_mode

    values = {
        "caller_profile_id": env.get(CODEX_MODEL_CALLER_PROFILE_ENV)
        or defaults.get("caller"),
        "operator_profile_id": env.get(CODEX_MODEL_OPERATOR_PROFILE_ENV)
        or defaults.get("operator"),
        "edit_fork_profile_id": env.get(CODEX_MODEL_EDIT_FORK_PROFILE_ENV)
        or defaults.get("edit_fork"),
        "review_fork_profile_id": env.get(CODEX_MODEL_REVIEW_FORK_PROFILE_ENV)
        or defaults.get("review_fork"),
        "efficient_profile_id": env.get(CODEX_MODEL_EFFICIENT_PROFILE_ENV)
        or tasks.get("efficient"),
        "visual_profile_id": env.get(CODEX_MODEL_VISUAL_PROFILE_ENV)
        or tasks.get("visual"),
    }
    resolved: dict[str, str] = {}
    for field, fallback in (
        ("caller_profile_id", PUBLIC_CODEX_MODEL_POLICY.caller_profile_id),
        ("operator_profile_id", PUBLIC_CODEX_MODEL_POLICY.operator_profile_id),
        ("edit_fork_profile_id", PUBLIC_CODEX_MODEL_POLICY.edit_fork_profile_id),
        ("review_fork_profile_id", PUBLIC_CODEX_MODEL_POLICY.review_fork_profile_id),
        ("efficient_profile_id", PUBLIC_CODEX_MODEL_POLICY.efficient_profile_id),
        ("visual_profile_id", PUBLIC_CODEX_MODEL_POLICY.visual_profile_id),
    ):
        resolved[field] = _resolve_base_profile(
            values[field],
            fallback=fallback,
            field=field,
            warnings=warnings,
            strict=strict,
        )

    raw_elevation_profiles: object = env.get(CODEX_MODEL_ELEVATION_PROFILES_ENV)
    if raw_elevation_profiles is None:
        raw_elevation_profiles = elevations.get("allowed")
    elevation_profile_ids = _resolve_elevation_profiles(
        raw_elevation_profiles,
        warnings=warnings,
        strict=strict,
    )
    return CodexModelPolicy(
        control_mode=control_mode,
        elevation_profile_ids=elevation_profile_ids,
        source=source,
        source_sha256=digest,
        warnings=tuple(warnings),
        **resolved,
    )


def classify_codex_model_selection(
    *,
    model: object,
    reasoning_effort: object,
    catalog: Iterable[CodexModelOption],
    policy: CodexModelPolicy | None = None,
    model_provider: object = "",
    catalog_fresh: bool = True,
) -> CodexModelSelection:
    """Classify a live Codex ``/model`` choice without narrowing the picker."""

    active_policy = policy or PUBLIC_CODEX_MODEL_POLICY
    model_slug = str(model or "").strip()
    effort = str(reasoning_effort or "").strip()
    provider = str(model_provider or "").strip()
    exact_profile = next(
        (
            profile
            for profile in MANAGED_CODEX_PROFILE_ORDER
            if profile.model == model_slug and profile.reasoning_effort == effort
        ),
        None,
    )
    catalog_models = {item.slug: item for item in catalog}
    catalog_model = catalog_models.get(model_slug)
    if exact_profile is not None:
        classification = CodexModelSelectionClass.MANAGED_QUALIFIED
    elif catalog_model is not None and (
        not catalog_model.supported_reasoning_levels
        or effort in set(catalog_model.supported_reasoning_levels)
    ):
        classification = CodexModelSelectionClass.CATALOG_OBSERVED
    elif provider and provider.casefold() not in {"openai", "chatgpt"}:
        classification = CodexModelSelectionClass.CUSTOM_PROVIDER_OBSERVED
    elif not catalog_fresh or not catalog_models:
        classification = CodexModelSelectionClass.CATALOG_STALE
    else:
        classification = CodexModelSelectionClass.UNKNOWN

    requires_approval = bool(
        (exact_profile is not None and exact_profile.elevated)
        or effort in {"max", "ultra"}
    )
    if active_policy.control_mode is CodexModelControlMode.OBSERVE:
        allowed = True
        reason = "PBX records direct Codex model choices without gating them."
    elif active_policy.control_mode is CodexModelControlMode.GUARD_ELEVATED:
        allowed = not requires_approval
        reason = (
            "An approved bounded model-elevation lease is required."
            if requires_approval
            else "The live catalog choice is allowed and recorded."
        )
    else:
        allowed = bool(exact_profile is not None and not requires_approval)
        reason = (
            "The selection matches an approved PBX profile."
            if allowed
            else "Enforced policy requires an approved PBX profile or elevation lease."
        )
    return CodexModelSelection(
        model=model_slug,
        reasoning_effort=effort,
        model_provider=provider,
        classification=classification,
        managed_profile_id=exact_profile.profile_id if exact_profile else None,
        requires_approval=requires_approval,
        allowed=allowed,
        reason=reason,
    )


def _table(data: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = data.get(key)
    return value if isinstance(value, Mapping) else {}


def _resolve_base_profile(
    value: object,
    *,
    fallback: str,
    field: str,
    warnings: list[str],
    strict: bool,
) -> str:
    profile = managed_profile_for(value or fallback)
    if profile is not None and not profile.elevated and not profile.migration_only:
        return profile.profile_id
    message = f"Invalid non-elevated model policy value for {field}: {value!r}."
    if strict:
        raise ValueError(message)
    warnings.append(f"{message} Using {fallback!r}.")
    return fallback


def _resolve_elevation_profiles(
    value: object,
    *,
    warnings: list[str],
    strict: bool,
) -> tuple[str, ...]:
    if value is None:
        entries: Iterable[object] = PUBLIC_CODEX_MODEL_POLICY.elevation_profile_ids
    elif isinstance(value, str):
        entries = (item.strip() for item in value.split(","))
    elif isinstance(value, (list, tuple)):
        entries = value
    else:
        entries = ()
        warnings.append("Ignoring invalid elevation profile list in local model policy.")
    resolved: list[str] = []
    for entry in entries:
        profile = managed_profile_for(entry)
        if profile is None or not profile.elevated or profile.migration_only:
            message = f"Invalid model elevation profile: {entry!r}."
            if strict:
                raise ValueError(message)
            warnings.append(message)
            continue
        if profile.profile_id not in resolved:
            resolved.append(profile.profile_id)
    if not resolved:
        if strict:
            raise ValueError("model policy must allow at least one elevation profile")
        warnings.append("No valid local elevations remained; using committed defaults.")
        return PUBLIC_CODEX_MODEL_POLICY.elevation_profile_ids
    return tuple(resolved)
