from __future__ import annotations

from typing import Any
from urllib.parse import quote

from textual.widgets import TextArea

from ...client import auth_headers
from ..agent_types import (
    DEFAULT_OPERATOR_FORK_PURPOSE,
    DEFAULT_OPERATOR_FORK_TRACK_ID,
    OPERATOR_AGENT_TYPE,
    OPERATOR_ROLE_FORK,
    OPERATOR_ROLE_ROOT,
)
from ..references import (
    CALLER_AGENT_REF_PATTERN,
    CallerAgentReference,
    FileCompletionContext,
    GitHubPromptReference,
    GitHubReferenceScope,
    ISSUE_NATURAL_REF_PATTERN,
    ISSUE_REF_PATTERN,
    JOPLIN_NOTE_REF_PATTERN,
    JoplinNoteReference,
    PULL_REQUEST_NATURAL_REF_PATTERN,
    PULL_REQUEST_REF_PATTERN,
    caller_agent_ref_completion_tokens,
    caller_agent_ref_tokens,
    caller_agent_ref_tokens_in_message,
    format_caller_agent_references_for_prompt,
    format_joplin_note_references_for_prompt,
    issue_ref_completion_tokens,
    joplin_note_ref_completion_tokens,
    joplin_note_ref_tokens,
    markdown_fence_for,
    pull_request_ref_completion_tokens,
)
from ..github import github_ref_number
from ._base import TUIController


def _int_value(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class PromptReferenceController(TUIController[Any]):
    def joplin_note_completion_matches(
        self,
        context: FileCompletionContext,
        agent_id: str,
    ) -> tuple[str, ...]:
        cache_agent_id = self.joplin_note_cache_agent_id(agent_id)
        notes = self.joplin_notes_by_agent.get(cache_agent_id, {})
        if not notes:
            return ()
        prefix_key = context.prefix.lower()
        return tuple(
            token
            for token in joplin_note_ref_completion_tokens(notes.values())
            if token.lower().startswith(prefix_key)
        )

    def caller_agent_completion_matches(
        self,
        context: FileCompletionContext,
        agent_id: str,
    ) -> tuple[str, ...]:
        if not self.is_operator_agent_id(agent_id):
            return ()
        prefix_key = context.prefix.lower()
        return tuple(
            token
            for token in caller_agent_ref_completion_tokens(self.caller_agents())
            if token.lower().startswith(prefix_key)
        )

    def pull_request_completion_matches(
        self,
        context: FileCompletionContext,
        agent_id: str,
    ) -> tuple[str, ...]:
        repo_agent_id = self.repo_scope_agent_id(agent_id)
        pulls = self.pull_requests_by_agent.get(
            repo_agent_id
        ) or self.pull_requests_by_agent.get(
            agent_id,
            {},
        )
        if not pulls:
            return ()
        prefix_key = context.prefix.lower()
        return tuple(
            token
            for token in pull_request_ref_completion_tokens(pulls.values())
            if token.lower().startswith(prefix_key)
        )

    def issue_completion_matches(
        self,
        context: FileCompletionContext,
        agent_id: str,
    ) -> tuple[str, ...]:
        repo_agent_id = self.repo_scope_agent_id(agent_id)
        issues = self.issues_by_agent.get(repo_agent_id) or self.issues_by_agent.get(
            agent_id,
            {},
        )
        if not issues:
            return ()
        prefix_key = context.prefix.lower()
        return tuple(
            token
            for token in issue_ref_completion_tokens(issues.values())
            if token.lower().startswith(prefix_key)
        )

    def project_for_agent(self, agent_id: str) -> str:
        agent = self.agents.get(agent_id)
        if isinstance(agent, dict) and agent.get("project"):
            return str(agent["project"])
        return agent_id

    def joplin_scope_agent_id(self, agent_id: str) -> str:
        agent = self.agents.get(agent_id)
        if (
            isinstance(agent, dict)
            and self.agent_type(agent) == OPERATOR_AGENT_TYPE
            and self.operator_role(agent) == OPERATOR_ROLE_FORK
        ):
            metadata = (
                agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
            )
            source_agent_id = str(metadata.get("source_caller_agent_id") or "").strip()
            if source_agent_id:
                return source_agent_id
        return agent_id

    def joplin_note_cache_agent_id(self, agent_id: str) -> str:
        return self.joplin_scope_agent_id(agent_id)

    def joplin_project_for_agent(self, agent_id: str) -> str:
        return self.project_for_agent(self.joplin_scope_agent_id(agent_id))

    def use_agent_joplin_note_scope(self, agent_id: str) -> bool:
        agent = self.agents.get(agent_id)
        return (
            isinstance(agent, dict)
            and self.agent_type(agent) == OPERATOR_AGENT_TYPE
            and self.operator_role(agent) == OPERATOR_ROLE_ROOT
        )

    def agent_joplin_notes_url(self, agent_id: str, note_id: str | None = None) -> str:
        base = f"/v1/agents/{quote(agent_id, safe='')}/joplin/notes"
        if note_id is None:
            return base
        return f"{base}/{quote(note_id, safe='')}"

    def joplin_notes_url_for_agent(
        self,
        agent_id: str,
        note_id: str | None = None,
    ) -> str:
        if self.use_agent_joplin_note_scope(agent_id):
            return self.agent_joplin_notes_url(agent_id, note_id)
        return self.project_joplin_notes_url(
            self.joplin_project_for_agent(agent_id),
            note_id,
        )

    def joplin_edit_url_for_agent(self, agent_id: str, note_id: str) -> str:
        return f"{self.joplin_notes_url_for_agent(agent_id, note_id)}/edit"

    def joplin_native_conflict_promote_url_for_agent(
        self,
        agent_id: str,
        note_id: str,
    ) -> str:
        return f"{self.joplin_notes_url_for_agent(agent_id, note_id)}/promote-conflict"

    def joplin_conflict_url_for_agent(
        self,
        agent_id: str,
        conflict_id: str,
    ) -> str:
        if self.use_agent_joplin_note_scope(agent_id):
            return (
                f"/v1/agents/{quote(agent_id, safe='')}/joplin/conflicts/"
                f"{quote(conflict_id, safe='')}/resolve"
            )
        project = quote(self.joplin_project_for_agent(agent_id), safe="")
        return (
            f"/v1/projects/{project}/joplin/conflicts/"
            f"{quote(conflict_id, safe='')}/resolve"
        )

    def joplin_scope_label_for_agent(self, agent_id: str) -> str:
        if self.use_agent_joplin_note_scope(agent_id):
            return f"agent {agent_id}"
        return f"project {self.joplin_project_for_agent(agent_id)}"

    def repo_scope_agent_id(self, agent_id: str) -> str:
        agent = self.agents.get(agent_id)
        if not isinstance(agent, dict) or self.agent_type(agent) != OPERATOR_AGENT_TYPE:
            return agent_id
        metadata = (
            agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
        )
        if self.operator_role(agent) == OPERATOR_ROLE_FORK:
            source_agent_id = str(metadata.get("source_caller_agent_id") or "").strip()
            if source_agent_id:
                return source_agent_id
        source_agent_id = str(
            metadata.get("default_source_caller_agent_id")
            or metadata.get("source_caller_agent_id")
            or ""
        ).strip()
        if source_agent_id:
            return source_agent_id
        inferred_source_agent_id = self.single_active_operator_fork_source_agent_id(
            self.logical_operator_id_for_agent(agent)
        )
        if inferred_source_agent_id:
            return inferred_source_agent_id
        return agent_id

    def default_github_reference_scope_agent_id(self, agent_id: str) -> str | None:
        agent = self.agents.get(agent_id)
        if not isinstance(agent, dict) or self.agent_type(agent) != OPERATOR_AGENT_TYPE:
            return agent_id
        repo_agent_id = self.repo_scope_agent_id(agent_id)
        if repo_agent_id and repo_agent_id != agent_id:
            return repo_agent_id
        return None

    def project_joplin_notes_url(self, project: str, note_id: str | None = None) -> str:
        base = f"/v1/projects/{quote(project, safe='')}/joplin/notes"
        if note_id is None:
            return base
        return f"{base}/{quote(note_id, safe='')}"

    def text_before_completion_context(
        self,
        text_area: TextArea,
        context: FileCompletionContext,
    ) -> str:
        lines = text_area.text.split("\n")
        if context.line < 0 or context.line >= len(lines):
            return ""
        prefix_lines = lines[: context.line]
        prefix_lines.append(lines[context.line][: context.start_col])
        return "\n".join(prefix_lines)

    def caller_scope_agent_id_before_context(
        self,
        text_area: TextArea,
        context: FileCompletionContext,
    ) -> str | None:
        token = None
        before = self.text_before_completion_context(text_area, context)
        for match in CALLER_AGENT_REF_PATTERN.finditer(before):
            token = match.group(1)
        if token is None:
            return None
        return self.resolve_caller_agent_ref_token(token)

    def joplin_completion_agent_id(
        self,
        text_area: TextArea,
        context: FileCompletionContext,
        default_agent_id: str,
    ) -> str:
        if not self.is_operator_agent_id(default_agent_id):
            return default_agent_id
        return (
            self.caller_scope_agent_id_before_context(text_area, context)
            or default_agent_id
        )

    def github_completion_agent_id(
        self,
        text_area: TextArea,
        context: FileCompletionContext,
        default_agent_id: str,
    ) -> str:
        if not self.is_operator_agent_id(default_agent_id):
            return default_agent_id
        return (
            self.caller_scope_agent_id_before_context(text_area, context)
            or self.default_github_reference_scope_agent_id(default_agent_id)
            or default_agent_id
        )

    async def fetch_joplin_note_summaries(
        self,
        agent_id: str,
    ) -> dict[str, dict[str, Any]]:
        await self.refresh_joplin_status()
        if not (self.joplin_configured and self.joplin_available):
            raise RuntimeError(
                self.format_joplin_unavailable_summary(self.joplin_status)
            )
        cache_agent_id = self.joplin_note_cache_agent_id(agent_id)
        response = await self.api_client().get(
            self.joplin_notes_url_for_agent(agent_id),
            headers=auth_headers(self.token),
            timeout=20,
        )
        response.raise_for_status()
        notes = response.json()
        if not isinstance(notes, list):
            raise ValueError("Joplin notes response was not a list")
        existing = self.joplin_notes_by_agent.get(cache_agent_id, {})
        note_map: dict[str, dict[str, Any]] = {}
        for note in notes:
            if not isinstance(note, dict):
                continue
            note_id = str(note.get("id") or "")
            if not note_id:
                continue
            merged = dict(existing.get(note_id, {}))
            merged.update(note)
            note_map[note_id] = merged
        self.joplin_notes_by_agent[cache_agent_id] = note_map
        if cache_agent_id != agent_id:
            self.joplin_notes_by_agent[agent_id] = note_map
        return note_map

    def resolve_joplin_note_ref_token(
        self,
        token: str,
        notes: dict[str, dict[str, Any]],
    ) -> str | None:
        token_map = joplin_note_ref_tokens(notes.values())
        lower_map = {
            candidate.lower(): note_id for candidate, note_id in token_map.items()
        }
        note_id = lower_map.get(token.lower())
        if note_id is not None:
            return note_id
        stripped = token.rstrip(".")
        if stripped != token:
            return lower_map.get(stripped.lower())
        return None

    def resolve_caller_agent_ref_token(self, token: str) -> str | None:
        token_map = caller_agent_ref_tokens(self.caller_agents())
        lower_map = {
            candidate.lower(): agent_id for candidate, agent_id in token_map.items()
        }
        agent_id = lower_map.get(token.lower())
        if agent_id is not None:
            return agent_id
        stripped = token.rstrip(".")
        if stripped != token:
            return lower_map.get(stripped.lower())
        return None

    def caller_agent_reference_from_agent(
        self,
        token: str,
        agent: dict[str, Any],
        *,
        logical_operator_id: str | None = None,
        active_fork: dict[str, Any] | None = None,
    ) -> CallerAgentReference:
        agent_id = str(agent.get("agent_id") or "")
        metadata = (
            agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
        )
        tmux_pane = self.tmux_agent_targets.get(agent_id)
        if not tmux_pane:
            for key in ("tmux_pane_id", "tmux_pane", "pane_id"):
                value = metadata.get(key)
                if isinstance(value, str) and value.strip():
                    tmux_pane = value.strip()
                    break
        active_fork = active_fork or self.active_operator_fork_for_caller(
            agent,
            logical_operator_id=logical_operator_id,
        )
        active_operator_fork_id = None
        active_fork_agent_id = None
        active_fork_track_id = None
        active_fork_purpose = None
        active_fork_source_session_id = None
        active_fork_tmux_pane = None
        if active_fork is not None:
            fork_metadata = (
                active_fork.get("metadata")
                if isinstance(active_fork.get("metadata"), dict)
                else {}
            )
            active_operator_fork_id = (
                str(active_fork.get("operator_fork_id") or "").strip() or None
            )
            active_fork_agent_id = (
                str(
                    active_fork.get("fork_agent_id")
                    or active_fork.get("agent_id")
                    or ""
                ).strip()
                or None
            )
            active_fork_track_id = (
                str(
                    active_fork.get("fork_track_id")
                    or fork_metadata.get("fork_track_id")
                    or DEFAULT_OPERATOR_FORK_TRACK_ID
                ).strip()
                or None
            )
            active_fork_purpose = (
                str(
                    active_fork.get("fork_purpose")
                    or fork_metadata.get("fork_purpose")
                    or DEFAULT_OPERATOR_FORK_PURPOSE
                ).strip()
                or None
            )
            active_fork_source_session_id = (
                str(
                    active_fork.get("source_codex_session_id")
                    or fork_metadata.get("source_codex_session_id")
                    or ""
                ).strip()
                or None
            )
            active_fork_tmux_pane = (
                str(
                    active_fork.get("tmux_pane_id")
                    or fork_metadata.get("tmux_pane_id")
                    or ""
                ).strip()
                or None
            )
        return CallerAgentReference(
            token=token,
            agent_id=agent_id,
            name=str(agent["name"]) if agent.get("name") else None,
            project=str(agent.get("project") or ""),
            status=self.format_agent_status(agent),
            pbx_mode=self.agent_pbx_mode(agent),
            pbx_active=bool(agent.get("pbx_active", True)),
            active_campaign_count=_int_value(agent.get("active_campaign_count")) or 0,
            tmux_pane=tmux_pane,
            active_operator_fork_id=active_operator_fork_id,
            active_fork_agent_id=active_fork_agent_id,
            active_fork_track_id=active_fork_track_id,
            active_fork_purpose=active_fork_purpose,
            active_fork_source_session_id=active_fork_source_session_id,
            active_fork_tmux_pane=active_fork_tmux_pane,
        )

    async def fetch_joplin_note_for_reference(
        self,
        agent_id: str,
        note_id: str,
    ) -> dict[str, Any]:
        cache_agent_id = self.joplin_note_cache_agent_id(agent_id)
        response = await self.api_client().get(
            self.joplin_notes_url_for_agent(agent_id, note_id),
            headers=auth_headers(self.token),
            timeout=20,
        )
        response.raise_for_status()
        note = response.json()
        if not isinstance(note, dict):
            raise ValueError(f"Joplin note {note_id} response was not an object")
        self.joplin_notes_by_agent.setdefault(cache_agent_id, {})[note_id] = note
        if cache_agent_id != agent_id:
            self.joplin_notes_by_agent.setdefault(agent_id, {})[note_id] = note
        return note

    async def joplin_note_reference_scopes(
        self,
        agent_id: str,
        message: str,
    ) -> list[tuple[str, str, str | None]] | None:
        events: list[tuple[int, int, str, str]] = []
        for match in CALLER_AGENT_REF_PATTERN.finditer(message):
            events.append((match.start(1), 0, "caller", match.group(1)))
        for match in JOPLIN_NOTE_REF_PATTERN.finditer(message):
            events.append((match.start(1), 1, "joplin", match.group(1)))
        if not any(kind == "joplin" for _start, _order, kind, _token in events):
            return []
        if not agent_id:
            self.notify(
                "Select an agent before sending @joplin note references.",
                severity="warning",
            )
            return None
        if any(kind == "caller" for _start, _order, kind, _token in events) and not (
            self.is_operator_agent_id(agent_id)
        ):
            self.notify(
                "Select an operator before sending caller-scoped @joplin references.",
                severity="warning",
            )
            return None

        events.sort(key=lambda event: (event[0], event[1]))
        current_scope_agent_id = agent_id
        current_caller_token: str | None = None
        refreshed_agents = False
        seen: set[tuple[str, str]] = set()
        scopes: list[tuple[str, str, str | None]] = []
        for _start, _order, kind, token in events:
            if kind == "caller":
                target_agent_id = self.resolve_caller_agent_ref_token(token)
                if target_agent_id is None and not refreshed_agents:
                    await self.refresh_agents()
                    refreshed_agents = True
                    target_agent_id = self.resolve_caller_agent_ref_token(token)
                if target_agent_id is None:
                    raise ValueError(f"No caller agent matches {token}")
                current_scope_agent_id = target_agent_id
                current_caller_token = token
                continue
            key = (current_scope_agent_id, token.lower())
            if key in seen:
                continue
            seen.add(key)
            scopes.append((token, current_scope_agent_id, current_caller_token))
        return scopes

    def github_reference_events(
        self,
        message: str,
    ) -> list[tuple[int, int, str, str, int | None]]:
        events: list[tuple[int, int, str, str, int | None]] = []
        for match in CALLER_AGENT_REF_PATTERN.finditer(message):
            events.append((match.start(1), 0, "caller", match.group(1), None))
        for match in PULL_REQUEST_REF_PATTERN.finditer(message):
            events.append(
                (
                    match.start(1),
                    1,
                    "pull_request",
                    match.group(1),
                    github_ref_number(match.group(2)),
                )
            )
        for match in ISSUE_REF_PATTERN.finditer(message):
            events.append(
                (
                    match.start(1),
                    1,
                    "issue",
                    match.group(1),
                    github_ref_number(match.group(2)),
                )
            )
        for match in PULL_REQUEST_NATURAL_REF_PATTERN.finditer(message):
            events.append(
                (
                    match.start(1),
                    2,
                    "pull_request",
                    match.group(1),
                    github_ref_number(match.group(2)),
                )
            )
        for match in ISSUE_NATURAL_REF_PATTERN.finditer(message):
            events.append(
                (
                    match.start(1),
                    2,
                    "issue",
                    match.group(1),
                    github_ref_number(match.group(2)),
                )
            )
        events.sort(key=lambda event: (event[0], event[1]))
        return events

    async def github_reference_scopes(
        self,
        agent_id: str,
        message: str,
    ) -> list[GitHubReferenceScope] | None:
        events = self.github_reference_events(message)
        if not any(
            kind in {"pull_request", "issue"} for _s, _o, kind, _t, _n in events
        ):
            return []
        if not agent_id:
            self.notify(
                "Select an agent before sending GitHub PR or issue references.",
                severity="warning",
            )
            return None
        if any(kind == "caller" for _s, _o, kind, _t, _n in events) and not (
            self.is_operator_agent_id(agent_id)
        ):
            self.notify(
                "Select an operator before sending caller-scoped GitHub references.",
                severity="warning",
            )
            return None

        default_scope_agent_id = self.default_github_reference_scope_agent_id(agent_id)
        current_scope_agent_id = default_scope_agent_id or agent_id
        current_caller_token: str | None = None
        operator_without_default_scope = (
            self.is_operator_agent_id(agent_id) and default_scope_agent_id is None
        )
        refreshed_agents = False
        caller_tokens = [
            token for _start, _order, kind, token, _number in events if kind == "caller"
        ]
        unique_caller_tokens = list(dict.fromkeys(caller_tokens))
        if operator_without_default_scope and len(unique_caller_tokens) == 1:
            only_caller_token = unique_caller_tokens[0]
            target_agent_id = self.resolve_caller_agent_ref_token(only_caller_token)
            if target_agent_id is None:
                await self.refresh_agents()
                refreshed_agents = True
                target_agent_id = self.resolve_caller_agent_ref_token(only_caller_token)
            if target_agent_id is not None:
                current_scope_agent_id = target_agent_id
                current_caller_token = only_caller_token
                operator_without_default_scope = False
        seen: set[tuple[str, str, int]] = set()
        scopes: list[GitHubReferenceScope] = []
        for _start, _order, kind, token, number in events:
            if kind == "caller":
                target_agent_id = self.resolve_caller_agent_ref_token(token)
                if target_agent_id is None and not refreshed_agents:
                    await self.refresh_agents()
                    refreshed_agents = True
                    target_agent_id = self.resolve_caller_agent_ref_token(token)
                if target_agent_id is None:
                    raise ValueError(f"No caller agent matches {token}")
                current_scope_agent_id = target_agent_id
                current_caller_token = token
                continue
            if number is None:
                continue
            if operator_without_default_scope and current_caller_token is None:
                raise ValueError(
                    f"{token} needs a caller scope; add @caller:<agent> "
                    "or start the operator from a caller."
                )
            key = (kind, current_scope_agent_id, number)
            if key in seen:
                continue
            seen.add(key)
            scopes.append(
                GitHubReferenceScope(
                    kind=kind,
                    token=token,
                    number=number,
                    scope_agent_id=current_scope_agent_id,
                    caller_token=current_caller_token,
                )
            )
        return scopes

    async def expand_github_references(
        self,
        agent_id: str,
        message: str,
        *,
        reference_text: str | None = None,
    ) -> str | None:
        try:
            scopes = await self.github_reference_scopes(
                agent_id,
                reference_text or message,
            )
        except Exception as exc:
            self.notify(f"GitHub reference failed: {exc}", severity="error")
            return None
        if scopes is None:
            return None
        if not scopes:
            return message
        try:
            references: list[GitHubPromptReference] = []
            for scope in scopes:
                if scope.kind == "pull_request":
                    payload = await self.fetch_pull_request_for_reference(
                        scope.scope_agent_id,
                        scope.number,
                    )
                elif scope.kind == "issue":
                    payload = await self.fetch_issue_for_reference(
                        scope.scope_agent_id,
                        scope.number,
                    )
                else:
                    continue
                references.append(
                    GitHubPromptReference(
                        kind=scope.kind,
                        token=scope.token,
                        number=scope.number,
                        scope_agent_id=scope.scope_agent_id,
                        scope_project=self.project_for_agent(scope.scope_agent_id),
                        payload=payload,
                        caller_token=scope.caller_token,
                    )
                )
        except Exception as exc:
            self.notify(f"GitHub reference failed: {exc}", severity="error")
            return None
        return self.format_github_references_for_prompt(message, references)

    def format_github_references_for_prompt(
        self,
        message: str,
        references: list[GitHubPromptReference],
    ) -> str:
        if not references:
            return message
        lines = [
            message.rstrip(),
            "",
            "---",
            "",
            "## GitHub PR and Issue References",
            "",
        ]
        for index, reference in enumerate(references, start=1):
            if reference.kind == "pull_request":
                label = "Pull Request"
                title = str(reference.payload.get("title") or "")
                heading = f"PR #{reference.number}"
                detail = self.format_pull_request_detail(reference.payload)
            else:
                label = "Issue"
                title = str(reference.payload.get("title") or "")
                heading = f"Issue #{reference.number}"
                detail = self.format_issue_detail(reference.payload)
            if title:
                heading = f"{heading} - {title}"
            fence = markdown_fence_for(detail)
            lines.extend(
                [
                    f"### {index}. {heading}",
                    "",
                    f"- Ref: `{reference.token}`",
                    f"- Type: `{label}`",
                    f"- Number: `{reference.number}`",
                    f"- Agent Scope: `{reference.scope_agent_id}`",
                    f"- Project: `{reference.scope_project}`",
                ]
            )
            if reference.caller_token:
                lines.append(f"- Caller Ref: `{reference.caller_token}`")
            if reference.payload.get("repo"):
                lines.append(f"- Repo: `{reference.payload.get('repo')}`")
            if reference.payload.get("url"):
                lines.append(f"- URL: `{reference.payload.get('url')}`")
            lines.extend(["", f"{fence}text", detail, fence, ""])
        return "\n".join(lines).rstrip() + "\n"

    async def expand_joplin_note_references(
        self,
        agent_id: str,
        message: str,
        *,
        reference_text: str | None = None,
    ) -> str | None:
        try:
            scopes = await self.joplin_note_reference_scopes(
                agent_id,
                reference_text or message,
            )
        except Exception as exc:
            self.notify(f"Joplin note reference failed: {exc}", severity="error")
            return None
        if scopes is None:
            return None
        if not scopes:
            return message
        try:
            notes_by_agent: dict[str, dict[str, dict[str, Any]]] = {}
            references: list[JoplinNoteReference] = []
            for token, scope_agent_id, caller_token in scopes:
                notes = notes_by_agent.get(scope_agent_id)
                if notes is None:
                    notes = await self.fetch_joplin_note_summaries(scope_agent_id)
                    notes_by_agent[scope_agent_id] = notes
                note_id = self.resolve_joplin_note_ref_token(token, notes)
                if note_id is None:
                    project = self.joplin_project_for_agent(scope_agent_id)
                    raise ValueError(
                        f"No scoped Joplin note matches {token} in {project}"
                    )
                note = await self.fetch_joplin_note_for_reference(
                    scope_agent_id, note_id
                )
                scoped_by_caller = caller_token is not None
                references.append(
                    JoplinNoteReference(
                        token=token,
                        note_id=note_id,
                        title=str(note.get("title") or note_id),
                        body=str(note.get("body") or ""),
                        updated_time=note.get("updated_time"),
                        scope_agent_id=scope_agent_id if scoped_by_caller else None,
                        scope_project=(
                            self.joplin_project_for_agent(scope_agent_id)
                            if scoped_by_caller
                            else None
                        ),
                        caller_token=caller_token,
                    )
                )
        except Exception as exc:
            self.notify(f"Joplin note reference failed: {exc}", severity="error")
            return None
        return format_joplin_note_references_for_prompt(message, references)

    async def expand_caller_agent_references(
        self,
        agent_id: str,
        message: str,
        *,
        reference_text: str | None = None,
    ) -> str | None:
        tokens = caller_agent_ref_tokens_in_message(reference_text or message)
        if not tokens:
            return message
        if not agent_id or not self.is_operator_agent_id(agent_id):
            self.notify(
                "Select an operator before sending @caller references.",
                severity="warning",
            )
            return None
        operator_agent = self.agents.get(agent_id)
        logical_operator_id = (
            self.logical_operator_id_for_agent(operator_agent)
            if operator_agent is not None
            else agent_id
        )
        ensure_forks = (
            operator_agent is not None
            and self.agent_type(operator_agent) == OPERATOR_AGENT_TYPE
            and self.operator_role(operator_agent) == OPERATOR_ROLE_ROOT
        )
        try:
            references: list[CallerAgentReference] = []
            seen_agent_ids: set[str] = set()
            ensured_forks_by_caller: dict[str, dict[str, Any]] = {}
            for token in tokens:
                target_agent_id = self.resolve_caller_agent_ref_token(token)
                if target_agent_id is None:
                    await self.refresh_agents()
                    target_agent_id = self.resolve_caller_agent_ref_token(token)
                if target_agent_id is None:
                    raise ValueError(f"No caller agent matches {token}")
                if target_agent_id in seen_agent_ids:
                    continue
                target_agent = self.agents.get(target_agent_id)
                if target_agent is None:
                    raise ValueError(f"Caller agent {target_agent_id} is not loaded")
                active_fork: dict[str, Any] | None = None
                if ensure_forks:
                    active_fork = ensured_forks_by_caller.get(target_agent_id)
                    if active_fork is None:
                        active_fork = await self.ensure_operator_fork_from_tui(
                            logical_operator_id=logical_operator_id,
                            source_caller_agent_id=target_agent_id,
                        )
                        if active_fork is not None:
                            ensured_forks_by_caller[target_agent_id] = active_fork
                        else:
                            self.notify(
                                (
                                    "Operator fork unavailable for "
                                    f"{target_agent_id}; sending caller reference "
                                    f"to {agent_id}."
                                ),
                                severity="warning",
                            )
                references.append(
                    self.caller_agent_reference_from_agent(
                        token,
                        target_agent,
                        logical_operator_id=logical_operator_id,
                        active_fork=active_fork,
                    )
                )
                seen_agent_ids.add(target_agent_id)
        except Exception as exc:
            self.notify(f"Caller agent reference failed: {exc}", severity="error")
            return None
        return format_caller_agent_references_for_prompt(message, references)

    async def expand_prompt_references(
        self,
        agent_id: str,
        message: str,
    ) -> str | None:
        reference_text = message
        expanded_message = await self.expand_github_references(
            agent_id,
            message,
            reference_text=reference_text,
        )
        if expanded_message is None:
            return None
        expanded_message = await self.expand_joplin_note_references(
            agent_id,
            expanded_message,
            reference_text=reference_text,
        )
        if expanded_message is None:
            return None
        return await self.expand_caller_agent_references(
            agent_id,
            expanded_message,
            reference_text=reference_text,
        )

    async def route_operator_prompt_to_fork(
        self,
        agent_id: str,
        message: str,
    ) -> str | None:
        agent = self.agents.get(agent_id)
        if agent is None or self.agent_type(agent) != OPERATOR_AGENT_TYPE:
            return agent_id
        _ = message
        return agent_id
