from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Callable


PULL_REQUESTS_ENABLED_ENV = "AGENT_PBX_PR_ENABLED"
PULL_REQUESTS_MERGE_ENABLED_ENV = "AGENT_PBX_PR_MERGE_ENABLED"
GITHUB_BIN_ENV = "AGENT_PBX_GH_BIN"
GITHUB_REMOTE_ENV = "AGENT_PBX_GITHUB_REMOTE"
GITHUB_SSH_COMMAND_ENV = "AGENT_PBX_GITHUB_SSH_COMMAND"
GITHUB_SSH_COMMAND_OVERRIDES_ENV = "AGENT_PBX_GITHUB_SSH_COMMAND_OVERRIDES_JSON"
PULL_REQUESTS_TIMEOUT_ENV = "AGENT_PBX_PR_TIMEOUT_SECONDS"
PULL_REQUESTS_ALLOWED_REPOS_ENV = "AGENT_PBX_PR_ALLOWED_REPOS"
DEFAULT_PULL_REQUESTS_TIMEOUT_SECONDS = 20.0

Runner = Callable[
    [list[str], Path, float, dict[str, str] | None],
    subprocess.CompletedProcess[str],
]


@dataclass(frozen=True, slots=True)
class PullRequestConfig:
    enabled: bool = False
    merge_enabled: bool = False
    gh_bin: str = "gh"
    timeout_seconds: float = DEFAULT_PULL_REQUESTS_TIMEOUT_SECONDS
    allowed_repos: tuple[str, ...] = ()
    github_remote: str | None = None
    github_ssh_command: str | None = None
    github_ssh_command_overrides: dict[str, str] | None = None

    @property
    def configured(self) -> bool:
        return self.enabled


class PullRequestError(RuntimeError):
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


class PullRequestService:
    def __init__(
        self,
        config: PullRequestConfig | None = None,
        *,
        runner: Runner | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config or env_pull_request_config()
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
            "merge_enabled": self.config.merge_enabled,
            "allowed_repos": list(self.config.allowed_repos),
            "github_remote": self.config.github_remote,
            "github_ssh_command_configured": bool(self.config.github_ssh_command),
            "github_ssh_command_override_count": len(
                self.config.github_ssh_command_overrides or {}
            ),
            "checked_at": self.clock(),
            "repo": None,
            "repo_url": None,
            "error": None,
        }
        if not self.config.enabled:
            return {
                **base,
                "error": {
                    "code": "PR_INTEGRATION_DISABLED",
                    "message": f"Set {PULL_REQUESTS_ENABLED_ENV}=1 to enable pull request support.",
                    "retryable": True,
                },
            }
        try:
            gh_bin = self.resolve_gh_bin()
            cwd_path = self.require_cwd(cwd)
            repo = self.resolve_repo(gh_bin, cwd_path)
            self.require_repo_allowed(repo["name_with_owner"])
        except PullRequestError as exc:
            return {**base, "error": exc.as_error()}
        return {
            **base,
            "available": True,
            "gh_bin": str(gh_bin),
            "repo": repo["name_with_owner"],
            "repo_url": repo.get("url"),
            "error": None,
        }

    def list_for_agent(self, agent: dict[str, Any], *, limit: int = 30) -> dict[str, Any]:
        status = self.status_for_agent(agent)
        if not status.get("available"):
            return {**status, "pull_requests": []}
        try:
            data = self.run_json(
                [
                    str(status["gh_bin"]),
                    "pr",
                    "list",
                    "--repo",
                    str(status["repo"]),
                    "--limit",
                    str(min(max(int(limit), 1), 100)),
                    "--state",
                    "open",
                    "--json",
                    "number,title,state,isDraft,author,headRefName,baseRefName,updatedAt,url,labels,reviewDecision,statusCheckRollup",
                ],
                Path(str(status["cwd"])),
            )
        except PullRequestError as exc:
            return {**status, "available": False, "error": exc.as_error(), "pull_requests": []}
        items = data if isinstance(data, list) else []
        return {
            **status,
            "pull_requests": [normalize_pr_summary(item) for item in items if isinstance(item, dict)],
        }

    def detail_for_agent(self, agent: dict[str, Any], number: int) -> dict[str, Any]:
        status = self.require_available_status(agent)
        data = self.run_json(
            [
                str(status["gh_bin"]),
                "pr",
                "view",
                str(number),
                "--repo",
                str(status["repo"]),
                "--json",
                "number,title,state,isDraft,author,headRefName,baseRefName,updatedAt,createdAt,url,body,labels,reviewDecision,statusCheckRollup,mergeStateStatus,mergeable,files,commits",
            ],
            Path(str(status["cwd"])),
        )
        if not isinstance(data, dict):
            raise PullRequestError(
                "PR_UNEXPECTED_JSON",
                "gh returned pull request JSON that was not an object",
                retryable=True,
            )
        return {
            **normalize_pr_detail(data),
            "repo": status.get("repo"),
            "repo_url": status.get("repo_url"),
            "agent_id": status.get("agent_id"),
            "cwd": status.get("cwd"),
        }

    def merge_for_agent(
        self,
        agent: dict[str, Any],
        number: int,
        *,
        method: str,
        confirm: str,
    ) -> dict[str, Any]:
        if not self.config.merge_enabled:
            raise PullRequestError(
                "PR_MERGE_DISABLED",
                f"Set {PULL_REQUESTS_MERGE_ENABLED_ENV}=1 to enable operator merge.",
                retryable=True,
            )
        expected = f"merge PR #{number}"
        if confirm.strip().lower() != expected.lower():
            raise PullRequestError(
                "PR_MERGE_CONFIRMATION_REQUIRED",
                f"Confirmation must exactly match: {expected}",
                retryable=False,
            )
        if method not in {"squash", "merge", "rebase"}:
            raise PullRequestError(
                "PR_MERGE_METHOD_INVALID",
                "Merge method must be squash, merge, or rebase.",
                retryable=False,
            )
        status = self.require_available_status(agent)
        flag = {"squash": "--squash", "merge": "--merge", "rebase": "--rebase"}[method]
        result = self.run_command(
            [
                str(status["gh_bin"]),
                "pr",
                "merge",
                str(number),
                "--repo",
                str(status["repo"]),
                flag,
            ],
            Path(str(status["cwd"])),
        )
        return {
            "ok": True,
            "merged": True,
            "number": number,
            "method": method,
            "repo": status.get("repo"),
            "url": self.detail_url_from_merge_output(result.stdout, result.stderr),
            "output": (result.stdout or result.stderr or "").strip(),
        }

    def review_prompt(self, detail: dict[str, Any]) -> str:
        files = detail.get("files")
        file_names = []
        if isinstance(files, list):
            for item in files[:30]:
                if isinstance(item, dict) and item.get("path"):
                    file_names.append(str(item["path"]))
        checks = detail.get("checks")
        return "\n".join(
            [
                f"Review GitHub PR #{detail.get('number')} in {detail.get('repo')}.",
                f"Title: {detail.get('title')}",
                f"URL: {detail.get('url')}",
                f"Branch: {detail.get('head_ref')} -> {detail.get('base_ref')}",
                f"Review decision: {detail.get('review_decision') or '-'}",
                f"Checks: {format_checks_for_prompt(checks)}",
                "",
                "Review the diff and report findings through Agent PBX. Prioritize correctness, regressions, security, and missing tests. Do not merge the PR.",
                "Files: " + (", ".join(file_names) if file_names else "-"),
            ]
        )

    def workerbee_validation_prompt(self, detail: dict[str, Any]) -> str:
        return "\n".join(
            [
                f"Run appropriate WorkerBee validation for GitHub PR #{detail.get('number')} in {detail.get('repo')}.",
                f"Title: {detail.get('title')}",
                f"URL: {detail.get('url')}",
                "",
                "Use the local project workflow and report validation commands, results, and any artifacts through Agent PBX. Do not merge the PR.",
            ]
        )

    def require_available_status(self, agent: dict[str, Any]) -> dict[str, Any]:
        status = self.status_for_agent(agent)
        if status.get("available"):
            return status
        error = status.get("error") if isinstance(status.get("error"), dict) else {}
        raise PullRequestError(
            str(error.get("code") or "PR_UNAVAILABLE"),
            str(error.get("message") or "Pull request integration is unavailable."),
            retryable=bool(error.get("retryable", True)),
            details=error.get("details") if isinstance(error.get("details"), dict) else None,
        )

    def resolve_gh_bin(self) -> Path:
        configured = self.config.gh_bin.strip() or "gh"
        expanded = Path(configured).expanduser()
        if expanded.name != configured or "/" in configured:
            if not expanded.is_file():
                raise PullRequestError(
                    "GH_BIN_NOT_FOUND",
                    f"GitHub CLI executable not found: {expanded}",
                    retryable=True,
                )
            if not os.access(expanded, os.X_OK):
                raise PullRequestError(
                    "GH_BIN_NOT_EXECUTABLE",
                    f"GitHub CLI path is not executable: {expanded}",
                    retryable=True,
                )
            return expanded
        found = shutil.which(configured)
        if not found:
            raise PullRequestError(
                "GH_BIN_NOT_FOUND",
                f"GitHub CLI executable not found on PATH: {configured}",
                retryable=True,
            )
        return Path(found)

    def require_cwd(self, cwd: str | None) -> Path:
        if not cwd:
            raise PullRequestError(
                "AGENT_CWD_MISSING",
                "agent metadata does not include an absolute cwd",
                retryable=True,
            )
        cwd_path = Path(cwd).expanduser()
        if not cwd_path.is_dir():
            raise PullRequestError(
                "AGENT_CWD_NOT_FOUND",
                f"agent cwd does not exist or is not a directory: {cwd}",
                retryable=True,
            )
        return cwd_path

    def resolve_repo(self, gh_bin: Path, cwd: Path) -> dict[str, str]:
        remote_repo = self.resolve_github_remote_repo(cwd)
        if remote_repo is not None:
            return remote_repo
        data = self.run_json(
            [str(gh_bin), "repo", "view", "--json", "nameWithOwner,url"],
            cwd,
        )
        if not isinstance(data, dict):
            raise PullRequestError(
                "PR_REPO_UNEXPECTED_JSON",
                "gh repo view returned JSON that was not an object",
                retryable=True,
            )
        name = str(data.get("nameWithOwner") or "").strip()
        if not name:
            raise PullRequestError(
                "PR_REPO_UNRESOLVED",
                "Unable to resolve GitHub repository from agent cwd",
                retryable=True,
            )
        return {"name_with_owner": name, "url": str(data.get("url") or "")}

    def resolve_github_remote_repo(self, cwd: Path) -> dict[str, str] | None:
        remote = (self.config.github_remote or "").strip()
        if not remote:
            return None
        url = git_remote_url(cwd, remote)
        if not url:
            return None
        parsed = parse_github_remote_url(url)
        if parsed is None:
            return None
        return {
            "name_with_owner": parsed,
            "url": f"https://github.com/{parsed}",
        }

    def require_repo_allowed(self, repo: str) -> None:
        allowed = {item.lower() for item in self.config.allowed_repos if item.strip()}
        if not allowed or repo.lower() in allowed:
            return
        raise PullRequestError(
            "PR_REPO_NOT_ALLOWED",
            f"Repository {repo} is not in {PULL_REQUESTS_ALLOWED_REPOS_ENV}.",
            retryable=False,
            details={"repo": repo, "allowed_repos": sorted(allowed)},
        )

    def run_json(self, argv: list[str], cwd: Path) -> Any:
        result = self.run_command(argv, cwd)
        try:
            return json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise PullRequestError(
                "GH_INVALID_JSON",
                f"GitHub CLI returned invalid JSON: {exc}",
                retryable=True,
            ) from exc

    def run_command(self, argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        repo = command_repo(argv)
        env = github_command_env(
            repo,
            self.config.github_ssh_command,
            self.config.github_ssh_command_overrides or {},
        )
        try:
            result = self.runner(argv, cwd, self.config.timeout_seconds, env)
        except subprocess.TimeoutExpired as exc:
            raise PullRequestError(
                "GH_TIMEOUT",
                f"GitHub CLI command timed out after {self.config.timeout_seconds:g}s",
                retryable=True,
            ) from exc
        except OSError as exc:
            raise PullRequestError("GH_EXEC_FAILED", str(exc), retryable=True) from exc
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "GitHub CLI command failed").strip()
            raise PullRequestError(
                "GH_COMMAND_FAILED",
                message,
                retryable=True,
                details={"returncode": result.returncode, "argv": argv[1:]},
            )
        return result

    def detail_url_from_merge_output(self, stdout: str, stderr: str) -> str | None:
        text = f"{stdout}\n{stderr}"
        for token in text.split():
            if token.startswith("https://"):
                return token
        return None


def run_github_command(
    argv: list[str],
    cwd: Path,
    timeout: float,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    merged_env = None
    if env:
        merged_env = os.environ.copy()
        merged_env.update(env)
    return subprocess.run(
        argv,
        cwd=cwd,
        timeout=timeout,
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
        env=merged_env,
    )


def git_remote_url(cwd: Path, remote: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", remote],
            cwd=cwd,
            timeout=5,
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    url = result.stdout.strip()
    return url or None


def parse_github_remote_url(url: str) -> str | None:
    value = url.strip()
    prefixes = (
        "git@github.com:",
        "ssh://git@github.com/",
        "https://github.com/",
        "http://github.com/",
    )
    repo = ""
    for prefix in prefixes:
        if value.startswith(prefix):
            repo = value[len(prefix) :]
            break
    if not repo:
        return None
    if repo.endswith(".git"):
        repo = repo[:-4]
    repo = repo.strip("/")
    parts = [part for part in repo.split("/") if part]
    if len(parts) != 2:
        return None
    return f"{parts[0]}/{parts[1]}"


def command_repo(argv: list[str]) -> str | None:
    for index, value in enumerate(argv):
        if value == "--repo" and index + 1 < len(argv):
            repo = argv[index + 1].strip()
            return repo or None
        if value.startswith("--repo="):
            repo = value.split("=", 1)[1].strip()
            return repo or None
    return None


def github_command_env(
    repo: str | None,
    github_ssh_command: str | None,
    overrides: dict[str, str],
) -> dict[str, str] | None:
    command = ""
    if repo:
        command = overrides.get(repo.lower(), "").strip()
    if not command:
        command = (github_ssh_command or "").strip()
    if not command:
        return None
    return {"GIT_SSH_COMMAND": command}


def normalize_pr_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": int(item.get("number") or 0),
        "title": str(item.get("title") or ""),
        "state": str(item.get("state") or ""),
        "is_draft": bool(item.get("isDraft")),
        "author": author_login(item.get("author")),
        "head_ref": str(item.get("headRefName") or ""),
        "base_ref": str(item.get("baseRefName") or ""),
        "updated_at": item.get("updatedAt"),
        "url": str(item.get("url") or ""),
        "labels": label_names(item.get("labels")),
        "review_decision": item.get("reviewDecision"),
        "checks": summarize_status_check_rollup(item.get("statusCheckRollup")),
    }


def normalize_pr_detail(item: dict[str, Any]) -> dict[str, Any]:
    summary = normalize_pr_summary(item)
    summary.update(
        {
            "created_at": item.get("createdAt"),
            "body": str(item.get("body") or ""),
            "merge_state_status": item.get("mergeStateStatus"),
            "mergeable": item.get("mergeable"),
            "files": item.get("files") if isinstance(item.get("files"), list) else [],
            "commits": item.get("commits") if isinstance(item.get("commits"), list) else [],
        }
    )
    return summary


def author_login(author: object) -> str | None:
    if isinstance(author, dict):
        login = author.get("login")
        return str(login) if login else None
    return str(author) if author else None


def label_names(labels: object) -> list[str]:
    names: list[str] = []
    if not isinstance(labels, list):
        return names
    for label in labels:
        if isinstance(label, dict) and label.get("name"):
            names.append(str(label["name"]))
        elif isinstance(label, str):
            names.append(label)
    return names


def summarize_status_check_rollup(value: object) -> dict[str, Any]:
    checks = value if isinstance(value, list) else []
    summary = {"total": 0, "success": 0, "failed": 0, "pending": 0, "unknown": 0}
    names: list[str] = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        summary["total"] += 1
        name = check.get("name") or check.get("context") or check.get("workflowName")
        if name:
            names.append(str(name))
        state = str(
            check.get("conclusion")
            or check.get("status")
            or check.get("state")
            or ""
        ).lower()
        if state in {"success", "skipped", "neutral"}:
            summary["success"] += 1
        elif state in {"failure", "failed", "error", "cancelled", "timed_out", "action_required"}:
            summary["failed"] += 1
        elif state in {"pending", "queued", "in_progress", "expected", "waiting"}:
            summary["pending"] += 1
        else:
            summary["unknown"] += 1
    return {**summary, "names": names[:12]}


def format_checks_for_prompt(checks: object) -> str:
    if not isinstance(checks, dict):
        return "-"
    return (
        f"{checks.get('success', 0)} ok, {checks.get('failed', 0)} failed, "
        f"{checks.get('pending', 0)} pending, {checks.get('unknown', 0)} unknown"
    )


def agent_cwd(agent: dict[str, Any]) -> str | None:
    metadata = agent.get("metadata")
    if not isinstance(metadata, dict):
        return None
    cwd = metadata.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        return cwd.strip()
    return None


def env_pull_request_config() -> PullRequestConfig:
    return PullRequestConfig(
        enabled=env_flag(PULL_REQUESTS_ENABLED_ENV),
        merge_enabled=env_flag(PULL_REQUESTS_MERGE_ENABLED_ENV),
        gh_bin=os.getenv(GITHUB_BIN_ENV, "gh").strip() or "gh",
        timeout_seconds=env_float(
            PULL_REQUESTS_TIMEOUT_ENV,
            DEFAULT_PULL_REQUESTS_TIMEOUT_SECONDS,
        ),
        allowed_repos=env_repo_tuple(PULL_REQUESTS_ALLOWED_REPOS_ENV),
        github_remote=os.getenv(GITHUB_REMOTE_ENV, "").strip() or None,
        github_ssh_command=os.getenv(GITHUB_SSH_COMMAND_ENV, "").strip() or None,
        github_ssh_command_overrides=env_json_string_map(
            GITHUB_SSH_COMMAND_OVERRIDES_ENV
        ),
    )


def env_repo_tuple(name: str) -> tuple[str, ...]:
    value = os.getenv(name, "").strip()
    if not value:
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


def env_json_string_map(name: str) -> dict[str, str]:
    value = os.getenv(name, "").strip()
    if not value:
        return {}
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    result: dict[str, str] = {}
    for key, item in data.items():
        repo = str(key).strip().lower()
        command = str(item).strip()
        if repo and command:
            result[repo] = command
    return result


def env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
        "y",
        "enabled",
    }


def env_float(name: str, default: float) -> float:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default
