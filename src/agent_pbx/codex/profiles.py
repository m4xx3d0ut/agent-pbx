from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from ..codex_cli import CodexModelOption, codex_model_config_overrides


@dataclass(frozen=True)
class ManagedCodexProfile:
    profile_id: str
    label: str
    model: str
    reasoning_effort: str
    verbosity: str = "high"
    reasoning_summary: str = "detailed"
    elevated: bool = False
    migration_only: bool = False

    def overrides(self) -> tuple[str, ...]:
        return tuple(
            codex_model_config_overrides(
                model=self.model,
                reasoning_effort=self.reasoning_effort,
                verbosity=self.verbosity,
                reasoning_summary=self.reasoning_summary,
            )
        )


DEFAULT_CALLER_PROFILE_ID = "sol-high"
DEFAULT_OPERATOR_PROFILE_ID = "sol-xhigh"


MANAGED_CODEX_PROFILES: dict[str, ManagedCodexProfile] = {
    "sol-high": ManagedCodexProfile(
        "sol-high", "Sol 5.6 / high", "gpt-5.6-sol", "high"
    ),
    "sol-xhigh": ManagedCodexProfile(
        "sol-xhigh", "Sol 5.6 / xhigh", "gpt-5.6-sol", "xhigh"
    ),
    "terra-xhigh": ManagedCodexProfile(
        "terra-xhigh", "Terra 5.6 / xhigh", "gpt-5.6-terra", "xhigh"
    ),
    "terra-max": ManagedCodexProfile(
        "terra-max", "Terra 5.6 / max", "gpt-5.6-terra", "max"
    ),
    "sol-max": ManagedCodexProfile(
        "sol-max", "Sol 5.6 / max", "gpt-5.6-sol", "max", elevated=True
    ),
    "codex-5.5-xhigh": ManagedCodexProfile(
        "codex-5.5-xhigh",
        "Codex 5.5 / xhigh (migration only)",
        "gpt-5.5",
        "xhigh",
        migration_only=True,
    ),
}


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
    for profile in MANAGED_CODEX_PROFILES.values():
        validation = validate_managed_profile(profile, catalog_tuple)
        views.append(
            {
                "profile_id": profile.profile_id,
                "label": profile.label,
                "model": profile.model,
                "reasoning_effort": profile.reasoning_effort,
                "verbosity": profile.verbosity,
                "reasoning_summary": profile.reasoning_summary,
                "elevated": profile.elevated,
                "migration_only": profile.migration_only,
                "available": validation.valid,
                "errors": list(validation.errors),
                "warnings": list(validation.warnings),
                "overrides": list(profile.overrides()),
            }
        )
    return views
