from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Callable

from .pull_requests import (
    GITHUB_BIN_ENV,
    PULL_REQUESTS_ALLOWED_REPOS_ENV,
    PULL_REQUESTS_TIMEOUT_ENV,
    agent_cwd,
    author_login,
    env_flag,
    env_float,
    env_repo_tuple,
    label_names,
    run_github_command,
)


ISSUES_ENABLED_ENV = "AGENT_PBX_ISSUES_ENABLED"
ISSUES_CLOSE_ENABLED_ENV = "AGENT_PBX_ISSUES_CLOSE_ENABLED"
DEFAULT_ISSUES_TIMEOUT_SECONDS = 20.0

Runner = Callable[
    [list[str], Path, float],
    subprocess.CompletedProcess[str],
]


@dataclass(frozen=True, slots=True)
class IssueConfig:
    enabled: bool = False
    close_enabled: bool = False
    gh_bin: str = "gh"
    timeout_seconds: float = DEFAULT_ISSUES_TIMEOUT_SECONDS
    allowed_repos: tuple[str, ...] = ()

    @property
    def configured(self) -> bool:
        return self.enabled


class IssueError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = True,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}

    def as_error(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.details:
            payload["details"] = self.details
        return payload


class IssueService:
    def __init__(
        self,
        config: IssueConfig | None = None,
        *,
        runner: Runner | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config or env_issue_config()
        self.runner = runner or run_github_command
        self.clock = clock

    def status_for_agent(self, agent: dict[str, Any]) -> dict[str, Any]:
        agent_id = str(agent.get("agent_id") or "")
        cwd = agent_cwd(agent)
        base = {
            "configured": self.config.configured,
            "available": False,
            "agent_id": agent_id,
            "agent_project": str(agent.get("project") or "") or None,
            "cwd": cwd,
            "gh_bin": self.config.gh_bin,
            "close_enabled": self.config.close_enabled,
            "allowed_repos": list(self.config.allowed_repos),
            "checked_at": self.clock(),
            "repo": None,
            "repo_url": None,
            "error": None,
        }
        if not self.config.enabled:
            return {
                **base,
                "error": {
                    "code": "ISSUES_INTEGRATION_DISABLED",
                    "message": f"Set {ISSUES_ENABLED_ENV}=1 to enable issue support.",
                    "retryable": True,
                },
            }
        try:
            gh_bin = self.resolve_gh_bin()
            cwd_path = self.require_cwd(cwd)
            repo = self.resolve_repo(gh_bin, cwd_path)
            self.require_repo_allowed(repo["name_with_owner"])
        except IssueError as exc:
            return {**base, "error": exc.as_error()}
        return {
            **base,
            "available": True,
            "gh_bin": str(gh_bin),
            "repo": repo["name_with_owner"],
            "repo_url": repo.get("url"),
            "error": None,
        }

    def list_for_agent(
        self,
        agent: dict[str, Any],
        *,
        state: str = "open",
        limit: int = 30,
    ) -> dict[str, Any]:
        status = self.status_for_agent(agent)
        if not status.get("available"):
            return {**status, "issues": []}
        state_value = state if state in {"open", "closed", "all"} else "open"
        try:
            data = self.run_json(
                [
                    str(status["gh_bin"]),
                    "issue",
                    "list",
                    "--limit",
                    str(min(max(int(limit), 1), 100)),
                    "--state",
                    state_value,
                    "--json",
                    "number,title,state,author,labels,assignees,milestone,updatedAt,createdAt,url,closed,closedAt",
                ],
                Path(str(status["cwd"])),
            )
        except IssueError as exc:
            return {**status, "available": False, "error": exc.as_error(), "issues": []}
        items = data if isinstance(data, list) else []
        return {
            **status,
            "state": state_value,
            "issues": [normalize_issue_summary(item) for item in items if isinstance(item, dict)],
        }

    def detail_for_agent(self, agent: dict[str, Any], number: int) -> dict[str, Any]:
        status = self.require_available_status(agent)
        data = self.run_json(
            [
                str(status["gh_bin"]),
                "issue",
                "view",
                str(number),
                "--comments",
                "--json",
                "number,title,state,author,labels,assignees,milestone,updatedAt,createdAt,url,closed,closedAt,body,comments",
            ],
            Path(str(status["cwd"])),
        )
        if not isinstance(data, dict):
            raise IssueError(
                "ISSUE_UNEXPECTED_JSON",
                "gh returned issue JSON that was not an object",
                retryable=True,
            )
        return {
            **normalize_issue_detail(data),
            "repo": status.get("repo"),
            "repo_url": status.get("repo_url"),
            "agent_id": status.get("agent_id"),
            "cwd": status.get("cwd"),
        }

    def clear_for_agent(
        self,
        agent: dict[str, Any],
        number: int,
        *,
        comment: str,
        confirm: str,
    ) -> dict[str, Any]:
        if not self.config.close_enabled:
            raise IssueError(
                "ISSUE_CLOSE_DISABLED",
                f"Set {ISSUES_CLOSE_ENABLED_ENV}=1 to enable operator issue clearing.",
                retryable=True,
            )
        expected = f"clear issue #{number}"
        if confirm.strip().lower() != expected.lower():
            raise IssueError(
                "ISSUE_CLEAR_CONFIRMATION_REQUIRED",
                f"Confirmation must exactly match: {expected}",
                retryable=False,
            )
        comment_body = comment.strip()
        if not comment_body:
            raise IssueError(
                "ISSUE_CLEAR_COMMENT_REQUIRED",
                "A mitigation summary comment is required before closing an issue.",
                retryable=False,
            )
        status = self.require_available_status(agent)
        cwd = Path(str(status["cwd"]))
        self.run_command(
            [
                str(status["gh_bin"]),
                "issue",
                "comment",
                str(number),
                "--body",
                comment_body,
            ],
            cwd,
        )
        result = self.run_command(
            [
                str(status["gh_bin"]),
                "issue",
                "close",
                str(number),
            ],
            cwd,
        )
        return {
            "ok": True,
            "closed": True,
            "number": number,
            "repo": status.get("repo"),
            "url": self.detail_url_from_output(result.stdout, result.stderr),
            "output": (result.stdout or result.stderr or "").strip(),
        }

    def mitigation_prompt(self, detail: dict[str, Any]) -> str:
        labels = detail.get("labels")
        label_text = ", ".join(labels) if isinstance(labels, list) and labels else "-"
        assignees = detail.get("assignees")
        assignee_text = ", ".join(assignees) if isinstance(assignees, list) and assignees else "-"
        comments = detail.get("comments")
        comment_count = len(comments) if isinstance(comments, list) else 0
        return "\n".join(
            [
                f"Mitigate GitHub Issue #{detail.get('number')} in {detail.get('repo')}.",
                f"Title: {detail.get('title')}",
                f"URL: {detail.get('url')}",
                f"State: {detail.get('state')}",
                f"Labels: {label_text}",
                f"Assignees: {assignee_text}",
                f"Comments: {comment_count}",
                "",
                "Investigate the issue, make or recommend the required fix, run appropriate validation, and report findings through Agent PBX. Do not close or edit the issue; clearing is an explicit operator-only TUI/API action.",
            ]
        )

    def require_available_status(self, agent: dict[str, Any]) -> dict[str, Any]:
        status = self.status_for_agent(agent)
        if status.get("available"):
            return status
        error = status.get("error") if isinstance(status.get("error"), dict) else {}
        raise IssueError(
            str(error.get("code") or "ISSUE_UNAVAILABLE"),
            str(error.get("message") or "Issue integration is unavailable."),
            retryable=bool(error.get("retryable", True)),
            details=error.get("details") if isinstance(error.get("details"), dict) else None,
        )

    def resolve_gh_bin(self) -> Path:
        configured = self.config.gh_bin.strip() or "gh"
        expanded = Path(configured).expanduser()
        if expanded.name != configured or "/" in configured:
            if not expanded.is_file():
                raise IssueError(
                    "GH_BIN_NOT_FOUND",
                    f"GitHub CLI executable not found: {expanded}",
                    retryable=True,
                )
            if not os.access(expanded, os.X_OK):
                raise IssueError(
                    "GH_BIN_NOT_EXECUTABLE",
                    f"GitHub CLI path is not executable: {expanded}",
                    retryable=True,
                )
            return expanded
        found = shutil.which(configured)
        if not found:
            raise IssueError(
                "GH_BIN_NOT_FOUND",
                f"GitHub CLI executable not found on PATH: {configured}",
                retryable=True,
            )
        return Path(found)

    def require_cwd(self, cwd: str | None) -> Path:
        if not cwd:
            raise IssueError(
                "AGENT_CWD_MISSING",
                "agent metadata does not include an absolute cwd",
                retryable=True,
            )
        cwd_path = Path(cwd).expanduser()
        if not cwd_path.is_dir():
            raise IssueError(
                "AGENT_CWD_NOT_FOUND",
                f"agent cwd does not exist or is not a directory: {cwd}",
                retryable=True,
            )
        return cwd_path

    def resolve_repo(self, gh_bin: Path, cwd: Path) -> dict[str, str]:
        data = self.run_json(
            [str(gh_bin), "repo", "view", "--json", "nameWithOwner,url"],
            cwd,
        )
        if not isinstance(data, dict):
            raise IssueError(
                "ISSUE_REPO_UNEXPECTED_JSON",
                "gh repo view returned JSON that was not an object",
                retryable=True,
            )
        name = str(data.get("nameWithOwner") or "").strip()
        if not name:
            raise IssueError(
                "ISSUE_REPO_UNRESOLVED",
                "Unable to resolve GitHub repository from agent cwd",
                retryable=True,
            )
        return {"name_with_owner": name, "url": str(data.get("url") or "")}

    def require_repo_allowed(self, repo: str) -> None:
        allowed = {item.lower() for item in self.config.allowed_repos if item.strip()}
        if not allowed or repo.lower() in allowed:
            return
        raise IssueError(
            "ISSUE_REPO_NOT_ALLOWED",
            f"Repository {repo} is not in {PULL_REQUESTS_ALLOWED_REPOS_ENV}.",
            retryable=False,
            details={"repo": repo, "allowed_repos": sorted(allowed)},
        )

    def run_json(self, argv: list[str], cwd: Path) -> Any:
        result = self.run_command(argv, cwd)
        try:
            return json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise IssueError(
                "GH_INVALID_JSON",
                f"GitHub CLI returned invalid JSON: {exc}",
                retryable=True,
            ) from exc

    def run_command(self, argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        try:
            result = self.runner(argv, cwd, self.config.timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            raise IssueError(
                "GH_TIMEOUT",
                f"GitHub CLI command timed out after {self.config.timeout_seconds:g}s",
                retryable=True,
            ) from exc
        except OSError as exc:
            raise IssueError("GH_EXEC_FAILED", str(exc), retryable=True) from exc
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "GitHub CLI command failed").strip()
            raise IssueError(
                "GH_COMMAND_FAILED",
                message,
                retryable=True,
                details={"returncode": result.returncode, "argv": argv[1:]},
            )
        return result

    def detail_url_from_output(self, stdout: str, stderr: str) -> str | None:
        text = f"{stdout}\n{stderr}"
        for token in text.split():
            if token.startswith("https://"):
                return token
        return None


def normalize_issue_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": int(item.get("number") or 0),
        "title": str(item.get("title") or ""),
        "state": str(item.get("state") or ""),
        "author": author_login(item.get("author")),
        "url": str(item.get("url") or ""),
        "labels": label_names(item.get("labels")),
        "assignees": user_logins(item.get("assignees")),
        "milestone": milestone_title(item.get("milestone")),
        "updated_at": item.get("updatedAt"),
        "created_at": item.get("createdAt"),
        "closed": bool(item.get("closed")),
        "closed_at": item.get("closedAt"),
    }


def normalize_issue_detail(item: dict[str, Any]) -> dict[str, Any]:
    summary = normalize_issue_summary(item)
    summary.update(
        {
            "body": str(item.get("body") or ""),
            "comments": normalize_issue_comments(item.get("comments")),
        }
    )
    return summary


def normalize_issue_comments(comments: object) -> list[dict[str, Any]]:
    if not isinstance(comments, list):
        return []
    normalized: list[dict[str, Any]] = []
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        normalized.append(
            {
                "author": author_login(comment.get("author")),
                "body": str(comment.get("body") or ""),
                "created_at": comment.get("createdAt"),
                "updated_at": comment.get("updatedAt"),
                "url": str(comment.get("url") or ""),
            }
        )
    return normalized


def user_logins(users: object) -> list[str]:
    logins: list[str] = []
    if not isinstance(users, list):
        return logins
    for user in users:
        if isinstance(user, dict) and user.get("login"):
            logins.append(str(user["login"]))
        elif isinstance(user, str):
            logins.append(user)
    return logins


def milestone_title(milestone: object) -> str | None:
    if isinstance(milestone, dict):
        title = milestone.get("title")
        return str(title) if title else None
    return str(milestone) if milestone else None


def env_issue_config() -> IssueConfig:
    return IssueConfig(
        enabled=env_flag(ISSUES_ENABLED_ENV),
        close_enabled=env_flag(ISSUES_CLOSE_ENABLED_ENV),
        gh_bin=os.getenv(GITHUB_BIN_ENV, "gh").strip() or "gh",
        timeout_seconds=env_float(
            PULL_REQUESTS_TIMEOUT_ENV,
            DEFAULT_ISSUES_TIMEOUT_SECONDS,
        ),
        allowed_repos=env_repo_tuple(PULL_REQUESTS_ALLOWED_REPOS_ENV),
    )
