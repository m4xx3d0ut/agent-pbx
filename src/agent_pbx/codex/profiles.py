from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Iterable, Mapping

from ..codex_cli import CodexModelOption, codex_model_config_overrides


PORTABLE_CODEX_SUBMIT_KEYS = ("enter",)
PORTABLE_CODEX_NEWLINE_KEYS = (
    "ctrl-j",
    "shift-enter",
    "alt-enter",
    "ctrl-enter",
)


def portable_codex_keymap_overrides() -> tuple[str, ...]:
    """Return the small portable keymap contract PBX manages."""

    return (
        "tui.keymap.composer.submit="
        + json.dumps(list(PORTABLE_CODEX_SUBMIT_KEYS), separators=(",", ":")),
        "tui.keymap.editor.insert_newline="
        + json.dumps(list(PORTABLE_CODEX_NEWLINE_KEYS), separators=(",", ":")),
    )


@dataclass(frozen=True)
class ManagedCodexProfile:
    """One exact, versioned Codex execution profile used by API and TUI."""

    profile_id: str
    label: str
    model: str
    reasoning_effort: str
    description: str
    verbosity: str = "high"
    reasoning_summary: str = "detailed"
    service_tier: str = ""
    aliases: tuple[str, ...] = ()
    eol: str = ""
    recommended: bool = False
    elevated: bool = False
    approval_class: str = "none"
    execution_mode: str = "single_agent"
    role_hints: tuple[str, ...] = ()
    capability_requirements: tuple[str, ...] = ()
    fallback_profile_ids: tuple[str, ...] = ()
    migration_only: bool = False

    @property
    def key(self) -> str:
        """Compatibility name used by the original TUI preset interface."""

        return self.profile_id

    @property
    def display_label(self) -> str:
        suffix = " recommended" if self.recommended else ""
        eol = f" EOL {self.eol}" if self.eol else ""
        return f"{self.label}{suffix}{eol}"

    def metadata(self) -> dict[str, str]:
        data = {
            "codex_model_preset": self.profile_id,
            "codex_model_label": self.label,
            "codex_model": self.model,
            "codex_model_reasoning_effort": self.reasoning_effort,
            "codex_model_execution_mode": self.execution_mode,
            "codex_model_approval_class": self.approval_class,
        }
        if self.verbosity:
            data["codex_model_verbosity"] = self.verbosity
        if self.reasoning_summary:
            data["codex_model_reasoning_summary"] = self.reasoning_summary
        if self.service_tier:
            data["codex_model_service_tier"] = self.service_tier
        if self.eol:
            data["codex_model_eol"] = self.eol
        return data

    def overrides(self) -> tuple[str, ...]:
        return tuple(
            codex_model_config_overrides(
                model=self.model,
                reasoning_effort=self.reasoning_effort,
                verbosity=self.verbosity,
                reasoning_summary=self.reasoning_summary,
                service_tier=self.service_tier,
            )
        ) + portable_codex_keymap_overrides()


DEFAULT_CALLER_PROFILE_ID = "sol-6.2-medium"
DEFAULT_OPERATOR_PROFILE_ID = "sol-6.2-high"


MANAGED_CODEX_PROFILE_ORDER: tuple[ManagedCodexProfile, ...] = (
    ManagedCodexProfile(
        "sol-6.2-medium",
        "Sol 6.2 / medium",
        "gpt-6.2-sol",
        "medium",
        "Public caller Agent default for everyday coding and engineering work.",
        aliases=("gpt-6.2-sol-medium",),
        recommended=True,
        role_hints=("caller",),
        fallback_profile_ids=(
            "sol-6.1-medium",
            "sol-6-medium",
            "sol-5.6-high",
        ),
    ),
    ManagedCodexProfile(
        "sol-6.2-high",
        "Sol 6.2 / high",
        "gpt-6.2-sol",
        "high",
        "Public root Operator and review-fork default.",
        aliases=("gpt-6.2-sol-high",),
        role_hints=("operator", "review"),
        fallback_profile_ids=(
            "sol-6.1-high",
            "sol-6-high",
            "sol-5.6-xhigh",
        ),
    ),
    ManagedCodexProfile(
        "sol-6.2-xhigh",
        "Sol 6.2 / xhigh",
        "gpt-6.2-sol",
        "xhigh",
        "Trusted-workstation Agent and Operator profile with extra reasoning depth.",
        aliases=("gpt-6.2-sol-xhigh",),
        role_hints=("trusted_caller", "trusted_operator"),
    ),
    ManagedCodexProfile(
        "sol-6.2-max",
        "Sol 6.2 / max",
        "gpt-6.2-sol",
        "max",
        "Bounded elevation for difficult indivisible engineering work.",
        aliases=("gpt-6.2-sol-max",),
        elevated=True,
        approval_class="bounded",
        role_hints=("elevation",),
    ),
    ManagedCodexProfile(
        "sol-6.2-ultra",
        "Sol 6.2 / ultra",
        "gpt-6.2-sol",
        "ultra",
        "Capability-gated elevation for difficult work that benefits from delegation.",
        aliases=("gpt-6.2-sol-ultra",),
        elevated=True,
        approval_class="bounded_capability",
        execution_mode="delegated",
        role_hints=("elevation",),
        capability_requirements=("reasoning_ultra", "codex_subagents"),
    ),
    ManagedCodexProfile(
        "astra-6.2-xhigh",
        "Astra 6.2 / xhigh",
        "gpt-6.2-astra",
        "xhigh",
        "Exceptional architecture, debugging, and research profile.",
        aliases=("gpt-6.2-astra-xhigh",),
        elevated=True,
        approval_class="bounded",
        role_hints=("elevation",),
    ),
    ManagedCodexProfile(
        "astra-6.2-max",
        "Astra 6.2 / max",
        "gpt-6.2-astra",
        "max",
        "Bounded Astra elevation for the hardest indivisible tasks.",
        aliases=("gpt-6.2-astra-max",),
        elevated=True,
        approval_class="bounded_strong",
        role_hints=("elevation",),
    ),
    ManagedCodexProfile(
        "astra-6.2-ultra",
        "Astra 6.2 / ultra",
        "gpt-6.2-astra",
        "ultra",
        "Separately qualified Astra elevation with automatic delegation.",
        aliases=("gpt-6.2-astra-ultra",),
        elevated=True,
        approval_class="bounded_capability_strong",
        execution_mode="delegated",
        role_hints=("elevation",),
        capability_requirements=("reasoning_ultra", "codex_subagents"),
    ),
    ManagedCodexProfile(
        "luna-6-high",
        "Luna 6 / high",
        "gpt-6-luna",
        "high",
        "Efficient profile for routine and high-volume text or code tasks.",
        aliases=("gpt-6-luna-high", "luna-high"),
        role_hints=("efficient",),
    ),
    ManagedCodexProfile(
        "terra-6.1-high",
        "Terra 6.1 / high",
        "gpt-6.1-terra",
        "high",
        "Visual-understanding and computer-use profile.",
        aliases=("gpt-6.1-terra-high",),
        role_hints=("visual", "computer_use"),
    ),
    ManagedCodexProfile(
        "terra-6.1-xhigh",
        "Terra 6.1 / xhigh",
        "gpt-6.1-terra",
        "xhigh",
        "Trusted-workstation visual-understanding and computer-use profile.",
        aliases=("gpt-6.1-terra-xhigh",),
        role_hints=("visual", "computer_use", "trusted"),
    ),
    ManagedCodexProfile(
        "sol-5.6-high",
        "Sol 5.6 / high",
        "gpt-5.6-sol",
        "high",
        "Compatibility profile for caller sessions created before v2.6.",
        aliases=("sol-high", "sol5.6-high", "gpt-5.6-sol-high"),
        migration_only=True,
    ),
    ManagedCodexProfile(
        "sol-5.6-xhigh",
        "Sol 5.6 / xhigh",
        "gpt-5.6-sol",
        "xhigh",
        "Compatibility profile for Operator sessions created before v2.6.",
        aliases=("sol-xhigh", "sol", "sol5.6-xhigh", "gpt-5.6-sol-xhigh"),
        migration_only=True,
    ),
    ManagedCodexProfile(
        "sol-5.6-max",
        "Sol 5.6 / max",
        "gpt-5.6-sol",
        "max",
        "Compatibility elevation profile retained for rollback.",
        aliases=("sol-max", "sol5.6-max", "gpt-5.6-sol-max"),
        elevated=True,
        approval_class="bounded",
        migration_only=True,
    ),
    ManagedCodexProfile(
        "terra-5.6-xhigh",
        "Terra 5.6 / xhigh",
        "gpt-5.6-terra",
        "xhigh",
        "Compatibility visual profile retained for rollback.",
        aliases=("terra-xhigh", "terra", "gpt-5.6-terra-xhigh"),
        migration_only=True,
    ),
    ManagedCodexProfile(
        "terra-5.6-max",
        "Terra 5.6 / max",
        "gpt-5.6-terra",
        "max",
        "Compatibility visual profile retained for rollback.",
        aliases=("terra-max", "terra5.6-max", "gpt-5.6-terra-max"),
        migration_only=True,
    ),
    ManagedCodexProfile(
        "legacy-5.5-xhigh",
        "Codex 5.5 / xhigh (migration only)",
        "gpt-5.5",
        "xhigh",
        "Legacy Codex 5.5 posture retained only for migration records.",
        aliases=("codex-5.5-xhigh", "codex-5.5", "gpt-5.5", "5.5", "legacy"),
        eol="2026-10-14",
        migration_only=True,
    ),
)


MANAGED_CODEX_PROFILES: dict[str, ManagedCodexProfile] = {
    profile.profile_id: profile for profile in MANAGED_CODEX_PROFILE_ORDER
}
for _profile in MANAGED_CODEX_PROFILE_ORDER:
    for _alias in _profile.aliases:
        MANAGED_CODEX_PROFILES.setdefault(_alias.casefold(), _profile)


def managed_profile_for(value: object) -> ManagedCodexProfile | None:
    key = str(value or "").strip().casefold()
    if not key:
        return None
    return MANAGED_CODEX_PROFILES.get(key)


def managed_profile_from_metadata(
    metadata: Mapping[str, Any] | None,
) -> ManagedCodexProfile | None:
    """Recover an exact profile without trusting free-form display text."""

    if not isinstance(metadata, Mapping):
        return None
    raw_profile_id = str(metadata.get("codex_model_preset") or "").strip()
    profile = managed_profile_for(raw_profile_id)
    if profile is not None:
        return profile
    model = str(metadata.get("codex_model") or "").strip()
    effort = str(metadata.get("codex_model_reasoning_effort") or "").strip()
    summary = str(metadata.get("codex_model_reasoning_summary") or "").strip()
    verbosity = str(metadata.get("codex_model_verbosity") or "").strip()
    service_tier = str(metadata.get("codex_model_service_tier") or "").strip()
    if raw_profile_id.startswith("catalog:") and model and effort:
        return ManagedCodexProfile(
            profile_id=raw_profile_id,
            label=str(metadata.get("codex_model_label") or f"{model} / {effort}"),
            model=model,
            reasoning_effort=effort,
            description="Session-local Codex catalog choice adopted by Agent PBX.",
            verbosity=verbosity or "high",
            reasoning_summary=summary or "detailed",
            service_tier=service_tier,
            elevated=effort in {"max", "ultra"},
            approval_class=("bounded_catalog" if effort in {"max", "ultra"} else "none"),
            execution_mode=("delegated" if effort == "ultra" else "single_agent"),
        )
    for candidate in MANAGED_CODEX_PROFILE_ORDER:
        if candidate.model != model or candidate.reasoning_effort != effort:
            continue
        if summary and candidate.reasoning_summary != summary:
            continue
        if candidate.verbosity and verbosity and candidate.verbosity != verbosity:
            continue
        if service_tier and candidate.service_tier != service_tier:
            continue
        return candidate
    return None


@dataclass(frozen=True)
class ProfileValidation:
    valid: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


def validate_managed_profile(
    profile: ManagedCodexProfile,
    catalog: Iterable[CodexModelOption],
) -> ProfileValidation:
    models = {item.slug: item for item in catalog}
    model = models.get(profile.model)
    errors: list[str] = []
    warnings: list[str] = []
    if model is None:
        errors.append(f"Model {profile.model!r} is absent from `codex debug models`.")
    else:
        supported = set(model.supported_reasoning_levels)
        if supported and profile.reasoning_effort not in supported:
            errors.append(
                f"Model {profile.model!r} does not advertise reasoning effort "
                f"{profile.reasoning_effort!r}."
            )
        if profile.service_tier and profile.service_tier not in set(model.service_tiers):
            errors.append(
                f"Model {profile.model!r} does not advertise service tier "
                f"{profile.service_tier!r}."
            )
        if model.supports_verbosity is False:
            warnings.append(f"Model {profile.model!r} does not advertise verbosity control.")
    if profile.migration_only:
        warnings.append("This profile is retained only for migration compatibility.")
    if profile.elevated:
        warnings.append("This profile requires an approved, unexpired elevation lease.")
    return ProfileValidation(not errors, tuple(errors), tuple(warnings))


def managed_profile_view(catalog: Iterable[CodexModelOption]) -> list[dict[str, Any]]:
    catalog_tuple = tuple(catalog)
    views: list[dict[str, Any]] = []
    for profile in MANAGED_CODEX_PROFILE_ORDER:
        validation = validate_managed_profile(profile, catalog_tuple)
        views.append(
            {
                "profile_id": profile.profile_id,
                "label": profile.label,
                "model": profile.model,
                "reasoning_effort": profile.reasoning_effort,
                "verbosity": profile.verbosity,
                "reasoning_summary": profile.reasoning_summary,
                "service_tier": profile.service_tier or "standard",
                "execution_mode": profile.execution_mode,
                "approval_class": profile.approval_class,
                "role_hints": list(profile.role_hints),
                "capability_requirements": list(profile.capability_requirements),
                "fallback_profile_ids": list(profile.fallback_profile_ids),
                "elevated": profile.elevated,
                "migration_only": profile.migration_only,
                "available": validation.valid,
                "errors": list(validation.errors),
                "warnings": list(validation.warnings),
                "overrides": list(profile.overrides()),
            }
        )
    return views
