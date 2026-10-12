from __future__ import annotations

import json
import sys
from typing import Any


REPOSITORY = {
    "nameWithOwner": "agent-pbx/demo",
    "url": "https://github.com/agent-pbx/demo",
}
PULL_REQUESTS: list[dict[str, Any]] = [
    {
        "number": 12,
        "title": "Add native Operator workflow surfaces",
        "state": "OPEN",
        "isDraft": False,
        "author": {"login": "demo-agent"},
        "headRefName": "feature/operator-workflows",
        "baseRefName": "dev",
        "updatedAt": "2026-10-12T12:00:00Z",
        "createdAt": "2026-10-12T11:30:00Z",
        "url": "https://github.com/agent-pbx/demo/pull/12",
        "body": "Adds durable Operator, campaign, and native runtime surfaces.",
        "labels": [{"name": "tui"}, {"name": "runtime"}],
        "reviewDecision": "REVIEW_REQUIRED",
        "mergeStateStatus": "CLEAN",
        "mergeable": "MERGEABLE",
        "statusCheckRollup": [
            {"name": "pytest", "conclusion": "SUCCESS"},
            {"name": "workerbee-smoke", "status": "IN_PROGRESS"},
        ],
        "files": [
            {"path": "src/agent_pbx/tui.py", "additions": 120, "deletions": 12},
            {"path": "tests/test_tui.py", "additions": 80, "deletions": 3},
        ],
        "commits": [{"oid": "abc123", "messageHeadline": "Add workflows"}],
    },
    {
        "number": 13,
        "title": "Document the public interactive demo",
        "state": "OPEN",
        "isDraft": False,
        "author": {"login": "demo-docs"},
        "headRefName": "docs/public-demo",
        "baseRefName": "dev",
        "updatedAt": "2026-10-12T11:45:00Z",
        "createdAt": "2026-10-12T11:00:00Z",
        "url": "https://github.com/agent-pbx/demo/pull/13",
        "body": "Documents the isolated interactive product tour.",
        "labels": [{"name": "docs"}],
        "reviewDecision": "APPROVED",
        "mergeStateStatus": "CLEAN",
        "mergeable": "MERGEABLE",
        "statusCheckRollup": [{"name": "docs", "conclusion": "SUCCESS"}],
        "files": [{"path": "README.md", "additions": 24, "deletions": 4}],
        "commits": [{"oid": "def456", "messageHeadline": "Document demo"}],
    },
]
ISSUES: list[dict[str, Any]] = [
    {
        "number": 42,
        "title": "Qualify browser keyboard navigation",
        "state": "OPEN",
        "author": {"login": "demo-operator"},
        "labels": [{"name": "tui"}, {"name": "accessibility"}],
        "assignees": [{"login": "demo-agent"}],
        "milestone": {"title": "web-demo"},
        "updatedAt": "2026-10-12T12:10:00Z",
        "createdAt": "2026-10-12T11:10:00Z",
        "url": "https://github.com/agent-pbx/demo/issues/42",
        "closed": False,
        "closedAt": None,
        "body": "Verify desktop and mobile navigation in the public fixture.",
        "comments": [
            {
                "author": {"login": "demo-agent"},
                "body": "The fixture is isolated and deterministic.",
                "createdAt": "2026-10-12T12:15:00Z",
                "updatedAt": "2026-10-12T12:15:00Z",
                "url": "https://github.com/agent-pbx/demo/issues/42#issuecomment-1",
            }
        ],
    },
    {
        "number": 43,
        "title": "Embed the product tour on the business site",
        "state": "OPEN",
        "author": {"login": "demo-docs"},
        "labels": [{"name": "docs"}, {"name": "website"}],
        "assignees": [],
        "milestone": None,
        "updatedAt": "2026-10-12T12:05:00Z",
        "createdAt": "2026-10-12T11:05:00Z",
        "url": "https://github.com/agent-pbx/demo/issues/43",
        "closed": False,
        "closedAt": None,
        "body": "Consume the versioned Agent PBX demo contract.",
        "comments": [],
    },
]


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:2] == ["repo", "view"]:
        value: object = REPOSITORY
    elif args[:2] == ["pr", "list"]:
        value = PULL_REQUESTS
    elif len(args) >= 3 and args[:2] == ["pr", "view"]:
        number = int(args[2])
        value = next(
            (item for item in PULL_REQUESTS if item["number"] == number),
            PULL_REQUESTS[0],
        )
    elif args[:2] == ["issue", "list"]:
        value = ISSUES
    elif len(args) >= 3 and args[:2] == ["issue", "view"]:
        number = int(args[2])
        value = next(
            (item for item in ISSUES if item["number"] == number), ISSUES[0]
        )
    elif args[:2] in (
        ["pr", "merge"],
        ["issue", "comment"],
        ["issue", "close"],
    ):
        value = {"ok": True}
    else:
        value = {}
    print(json.dumps(value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
