from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Iterable

from .github import github_ref_number
from .screens import slugify


def _short_stable_hash(value: str, length: int = 8) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


JOPLIN_NOTE_REF_PREFIX = "@joplin:"

JOPLIN_NOTE_REF_PATTERN = re.compile(
    r"(?:^|(?<=[\s(\[{'\"`]))(@joplin:[A-Za-z0-9_.~-]+)"
)

CALLER_AGENT_REF_PREFIX = "@caller:"

CALLER_AGENT_REF_PATTERN = re.compile(
    r"(?:^|(?<=[\s(\[{'\"`]))(@caller:[A-Za-z0-9_.~-]+)"
)

PULL_REQUEST_REF_PREFIX = "@pr:"

PULL_REQUEST_REF_PATTERN = re.compile(
    r"(?:^|(?<=[\s(\[{'\"`]))(@pr:(\d+))\b",
    re.IGNORECASE,
)

ISSUE_REF_PREFIX = "@issue:"

ISSUE_REF_PATTERN = re.compile(
    r"(?:^|(?<=[\s(\[{'\"`]))(@issue:(\d+))\b",
    re.IGNORECASE,
)

PULL_REQUEST_NATURAL_REF_PATTERN = re.compile(
    r"\b((?:PR|pull request)\s*#?\s*(\d+))\b",
    re.IGNORECASE,
)

ISSUE_NATURAL_REF_PATTERN = re.compile(
    r"\b((?:issues?)\s*#?\s*(\d+))\b",
    re.IGNORECASE,
)

CALLER_AGENT_REF_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class FileCompletionContext:
    input_id: str
    line: int
    start_col: int
    end_col: int
    prefix: str
    directory: str
    name_prefix: str


@dataclass(frozen=True)
class JoplinNoteReference:
    token: str
    note_id: str
    title: str
    body: str
    updated_time: int | float | None = None
    scope_agent_id: str | None = None
    scope_project: str | None = None
    caller_token: str | None = None


@dataclass(frozen=True)
class GitHubReferenceScope:
    kind: str
    token: str
    number: int
    scope_agent_id: str
    caller_token: str | None = None


@dataclass(frozen=True)
class GitHubPromptReference:
    kind: str
    token: str
    number: int
    scope_agent_id: str
    scope_project: str
    payload: dict[str, Any]
    caller_token: str | None = None


@dataclass(frozen=True)
class CallerAgentReference:
    token: str
    agent_id: str
    name: str | None
    project: str
    status: str
    pbx_mode: str
    pbx_active: bool
    active_campaign_count: int
    tmux_pane: str | None = None
    active_operator_fork_id: str | None = None
    active_fork_agent_id: str | None = None
    active_fork_track_id: str | None = None
    active_fork_purpose: str | None = None
    active_fork_source_session_id: str | None = None
    active_fork_tmux_pane: str | None = None


def joplin_note_ref_slug(note: dict[str, Any]) -> str:
    title = str(note.get("title") or "").strip()
    note_id = str(note.get("id") or "").strip()
    return slugify(title or note_id[:12] or "note")


def joplin_note_ref_tokens(
    notes: Iterable[dict[str, Any]],
) -> dict[str, str]:
    note_list = [note for note in notes if note.get("id")]
    slug_counts: dict[str, int] = {}
    short_id_counts: dict[str, int] = {}
    for note in note_list:
        slug = joplin_note_ref_slug(note).lower()
        short_id = str(note.get("id") or "")[:8].lower()
        slug_counts[slug] = slug_counts.get(slug, 0) + 1
        if short_id:
            short_id_counts[short_id] = short_id_counts.get(short_id, 0) + 1
    tokens: dict[str, str] = {}
    for note in note_list:
        note_id = str(note.get("id") or "")
        slug = joplin_note_ref_slug(note)
        slug_key = slug.lower()
        if slug_counts.get(slug_key, 0) == 1:
            tokens[f"{JOPLIN_NOTE_REF_PREFIX}{slug}"] = note_id
        else:
            tokens[f"{JOPLIN_NOTE_REF_PREFIX}{slug}~{note_id[:8]}"] = note_id
        tokens[f"{JOPLIN_NOTE_REF_PREFIX}{note_id}"] = note_id
        if note_id[:8] and short_id_counts.get(note_id[:8].lower(), 0) == 1:
            tokens[f"{JOPLIN_NOTE_REF_PREFIX}{note_id[:8]}"] = note_id
    return tokens


def joplin_note_ref_completion_tokens(
    notes: Iterable[dict[str, Any]],
) -> tuple[str, ...]:
    note_list = [note for note in notes if note.get("id")]
    slug_counts: dict[str, int] = {}
    for note in note_list:
        slug = joplin_note_ref_slug(note).lower()
        slug_counts[slug] = slug_counts.get(slug, 0) + 1
    tokens: list[str] = []
    seen: set[str] = set()
    for note in note_list:
        note_id = str(note.get("id") or "")
        slug = joplin_note_ref_slug(note)
        token = (
            f"{JOPLIN_NOTE_REF_PREFIX}{slug}"
            if slug_counts.get(slug.lower(), 0) == 1
            else f"{JOPLIN_NOTE_REF_PREFIX}{slug}~{note_id[:8]}"
        )
        if token.lower() in seen:
            continue
        seen.add(token.lower())
        tokens.append(token)
    return tuple(sorted(tokens, key=str.lower))


def joplin_note_ref_tokens_in_message(message: str) -> tuple[str, ...]:
    seen: set[str] = set()
    tokens: list[str] = []
    for match in JOPLIN_NOTE_REF_PATTERN.finditer(message):
        token = match.group(1)
        key = token.lower()
        if key not in seen:
            seen.add(key)
            tokens.append(token)
    return tuple(tokens)


def caller_agent_ref_slug(agent: dict[str, Any]) -> str:
    name = str(agent.get("name") or "").strip()
    agent_id = str(agent.get("agent_id") or "").strip()
    return slugify(name or agent_id)


def caller_agent_ref_token_entries(
    agents: Iterable[dict[str, Any]],
    *,
    primary_only: bool = False,
) -> list[tuple[str, str]]:
    agent_list = [agent for agent in agents if agent.get("agent_id")]
    slug_counts: dict[str, int] = {}
    for agent in agent_list:
        slug = caller_agent_ref_slug(agent).lower()
        slug_counts[slug] = slug_counts.get(slug, 0) + 1

    entries: list[tuple[str, str]] = []
    for agent in agent_list:
        agent_id = str(agent.get("agent_id") or "")
        slug = caller_agent_ref_slug(agent)
        slug_key = slug.lower()
        suffix = (
            ""
            if slug_counts.get(slug_key, 0) == 1
            else f"~{_short_stable_hash(agent_id)}"
        )
        entries.append((f"{CALLER_AGENT_REF_PREFIX}{slug}{suffix}", agent_id))
        if primary_only or not CALLER_AGENT_REF_SAFE_VALUE.match(agent_id):
            continue
        entries.append((f"{CALLER_AGENT_REF_PREFIX}{agent_id}", agent_id))
    return entries


def unique_reference_token_map(entries: Iterable[tuple[str, str]]) -> dict[str, str]:
    by_lower: dict[str, set[str]] = {}
    original_token: dict[str, str] = {}
    for token, value in entries:
        lower = token.lower()
        by_lower.setdefault(lower, set()).add(value)
        original_token.setdefault(lower, token)
    return {
        original_token[lower]: next(iter(values))
        for lower, values in by_lower.items()
        if len(values) == 1
    }


def caller_agent_ref_tokens(
    agents: Iterable[dict[str, Any]],
) -> dict[str, str]:
    return unique_reference_token_map(caller_agent_ref_token_entries(agents))


def caller_agent_ref_completion_tokens(
    agents: Iterable[dict[str, Any]],
) -> tuple[str, ...]:
    token_map = unique_reference_token_map(
        caller_agent_ref_token_entries(agents, primary_only=True)
    )
    return tuple(sorted(token_map, key=str.lower))


def caller_agent_ref_tokens_in_message(message: str) -> tuple[str, ...]:
    seen: set[str] = set()
    tokens: list[str] = []
    for match in CALLER_AGENT_REF_PATTERN.finditer(message):
        token = match.group(1)
        key = token.lower()
        if key not in seen:
            seen.add(key)
            tokens.append(token)
    return tuple(tokens)


def pull_request_ref_completion_tokens(
    pulls: Iterable[dict[str, Any]],
) -> tuple[str, ...]:
    numbers = {
        number
        for item in pulls
        if (number := github_ref_number(item.get("number"))) is not None
    }
    return tuple(f"{PULL_REQUEST_REF_PREFIX}{number}" for number in sorted(numbers))


def issue_ref_completion_tokens(
    issues: Iterable[dict[str, Any]],
) -> tuple[str, ...]:
    numbers = {
        number
        for item in issues
        if (number := github_ref_number(item.get("number"))) is not None
    }
    return tuple(f"{ISSUE_REF_PREFIX}{number}" for number in sorted(numbers))


def markdown_fence_for(body: str) -> str:
    fence = "```"
    while fence in body:
        fence += "`"
    return fence


def format_joplin_note_references_for_prompt(
    message: str,
    references: list[JoplinNoteReference],
) -> str:
    if not references:
        return message
    lines = [
        message.rstrip(),
        "",
        "---",
        "",
        "## Joplin Note References",
        "",
    ]
    for index, reference in enumerate(references, start=1):
        body = reference.body.rstrip() or "(empty note)"
        fence = markdown_fence_for(body)
        lines.extend(
            [
                f"### {index}. {reference.title or reference.note_id}",
                "",
                f"- Ref: `{reference.token}`",
                f"- Note ID: `{reference.note_id}`",
            ]
        )
        if reference.scope_agent_id:
            lines.append(f"- Agent Scope: `{reference.scope_agent_id}`")
        if reference.scope_project:
            lines.append(f"- Project: `{reference.scope_project}`")
        if reference.caller_token:
            lines.append(f"- Caller Ref: `{reference.caller_token}`")
        if reference.updated_time is not None:
            lines.append(f"- Updated: `{reference.updated_time}`")
        lines.extend(["", f"{fence}markdown", body, fence, ""])
    return "\n".join(lines).rstrip() + "\n"


def format_caller_agent_references_for_prompt(
    message: str,
    references: list[CallerAgentReference],
) -> str:
    if not references:
        return message
    lines = [
        message.rstrip(),
        "",
        "---",
        "",
        "## Caller Agent References",
        "",
    ]
    for index, reference in enumerate(references, start=1):
        title = reference.name or reference.agent_id
        lines.extend(
            [
                f"### {index}. {title}",
                "",
                f"- Ref: `{reference.token}`",
                f"- Agent ID: `{reference.agent_id}`",
                f"- Project: `{reference.project}`",
                f"- Status: `{reference.status}`",
                f"- PBX Mode: `{reference.pbx_mode}`",
                f"- PBX Active: `{str(reference.pbx_active).lower()}`",
                f"- Active Campaigns: `{reference.active_campaign_count}`",
            ]
        )
        if reference.tmux_pane:
            lines.append(f"- Tmux Pane: `{reference.tmux_pane}`")
        if reference.active_operator_fork_id:
            lines.append(
                f"- Active Operator Fork ID: `{reference.active_operator_fork_id}`"
            )
        if reference.active_fork_agent_id:
            lines.append(f"- Active Fork Agent ID: `{reference.active_fork_agent_id}`")
        if reference.active_fork_track_id:
            lines.append(f"- Active Fork Track: `{reference.active_fork_track_id}`")
        if reference.active_fork_purpose:
            lines.append(f"- Active Fork Purpose: `{reference.active_fork_purpose}`")
        if reference.active_fork_source_session_id:
            lines.append(
                f"- Active Fork Source Session: `{reference.active_fork_source_session_id}`"
            )
        if reference.active_fork_tmux_pane:
            lines.append(
                f"- Active Fork Tmux Pane: `{reference.active_fork_tmux_pane}`"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
