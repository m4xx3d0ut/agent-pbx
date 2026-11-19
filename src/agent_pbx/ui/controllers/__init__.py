"""TUI orchestration controllers with one-way host dependencies."""

from ..github import github_ref_number
from ..agent_types import (
    CALLER_AGENT_TYPE,
    DEFAULT_OPERATOR_FORK_PURPOSE,
    DEFAULT_OPERATOR_FORK_TRACK_ID,
    OPERATOR_AGENT_TYPE,
    OPERATOR_ROLE_FORK,
    OPERATOR_ROLE_ROOT,
)
from ..references import (
    CALLER_AGENT_REF_PATTERN,
    CALLER_AGENT_REF_PREFIX,
    CALLER_AGENT_REF_SAFE_VALUE,
    CallerAgentReference,
    FileCompletionContext,
    GitHubPromptReference,
    GitHubReferenceScope,
    ISSUE_NATURAL_REF_PATTERN,
    ISSUE_REF_PATTERN,
    ISSUE_REF_PREFIX,
    JOPLIN_NOTE_REF_PATTERN,
    JOPLIN_NOTE_REF_PREFIX,
    JoplinNoteReference,
    PULL_REQUEST_NATURAL_REF_PATTERN,
    PULL_REQUEST_REF_PATTERN,
    PULL_REQUEST_REF_PREFIX,
    caller_agent_ref_completion_tokens,
    caller_agent_ref_tokens,
    caller_agent_ref_tokens_in_message,
    format_caller_agent_references_for_prompt,
    format_joplin_note_references_for_prompt,
    issue_ref_completion_tokens,
    joplin_note_ref_completion_tokens,
    joplin_note_ref_tokens,
    joplin_note_ref_tokens_in_message,
    markdown_fence_for,
    pull_request_ref_completion_tokens,
)
from ._base import ControllerHost, TUIController, controller_method
from .campaigns import CampaignController
from .github import GitHubController
from .joplin import JoplinController
from .operator_handoffs import OperatorHandoffController
from .references import PromptReferenceController
from .workerbee import WorkerBeeController
from .workerbee_lifecycle import WorkerBeeLifecycleController
from .workerbee_projection import WorkerBeeProjectionController
from .workerbee_selector import WorkerBeeLaunchPreflightError


__all__ = [
    "CALLER_AGENT_REF_PATTERN",
    "CALLER_AGENT_REF_PREFIX",
    "CALLER_AGENT_REF_SAFE_VALUE",
    "CALLER_AGENT_TYPE",
    "CallerAgentReference",
    "DEFAULT_OPERATOR_FORK_PURPOSE",
    "DEFAULT_OPERATOR_FORK_TRACK_ID",
    "FileCompletionContext",
    "GitHubPromptReference",
    "GitHubReferenceScope",
    "ISSUE_NATURAL_REF_PATTERN",
    "ISSUE_REF_PATTERN",
    "ISSUE_REF_PREFIX",
    "JOPLIN_NOTE_REF_PATTERN",
    "JOPLIN_NOTE_REF_PREFIX",
    "JoplinNoteReference",
    "OPERATOR_AGENT_TYPE",
    "OPERATOR_ROLE_FORK",
    "OPERATOR_ROLE_ROOT",
    "PULL_REQUEST_NATURAL_REF_PATTERN",
    "PULL_REQUEST_REF_PATTERN",
    "PULL_REQUEST_REF_PREFIX",
    "PromptReferenceController",
    "caller_agent_ref_completion_tokens",
    "caller_agent_ref_tokens",
    "caller_agent_ref_tokens_in_message",
    "format_caller_agent_references_for_prompt",
    "format_joplin_note_references_for_prompt",
    "issue_ref_completion_tokens",
    "joplin_note_ref_completion_tokens",
    "joplin_note_ref_tokens",
    "joplin_note_ref_tokens_in_message",
    "markdown_fence_for",
    "pull_request_ref_completion_tokens",
    "ControllerHost",
    "CampaignController",
    "GitHubController",
    "JoplinController",
    "OperatorHandoffController",
    "TUIController",
    "WorkerBeeController",
    "WorkerBeeLifecycleController",
    "WorkerBeeProjectionController",
    "WorkerBeeLaunchPreflightError",
    "controller_method",
    "github_ref_number",
]
