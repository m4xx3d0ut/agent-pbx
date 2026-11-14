from __future__ import annotations

from collections.abc import Mapping
import json

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static, TextArea

from ...git_broker import LinkedGitCommitReceipt, PendingLinkedGitCommit
from ...git_review import GitReviewArtifact, GitReviewArtifactError
from ._shared import slugify
from ._types import LinkedGitBrokerTuiContextView


class LinkedGitCommitPathsScreen(ModalScreen[None]):
    """Collect an explicit broker subset without offering implicit add-all."""

    BINDINGS = [("escape", "dismiss", "Cancel")]

    def __init__(
        self,
        *,
        context: LinkedGitBrokerTuiContextView,
        suggested_paths: tuple[str, ...] = (),
        scoped_change_summary: str = "",
    ) -> None:
        super().__init__()
        self.context = context
        self.suggested_paths = suggested_paths
        self.scoped_change_summary = scoped_change_summary

    def compose(self) -> ComposeResult:
        with Vertical(id="linked-git-broker-panel"):
            yield Static(
                f"Review linked-worktree commit: {self.context.agent_id}",
                id="linked-git-broker-title",
            )
            yield Static(
                "Enter a JSON array of exact repository-relative leaf paths. "
                "There is no add-all, glob, directory, or implicit staging mode.",
                id="linked-git-broker-help",
            )
            if self.suggested_paths:
                yield Static(
                    "The reviewed handoff supplied the exact suggested subset below. "
                    "Confirm or narrow it before continuing.",
                    id="linked-git-broker-suggested-help",
                )
            yield TextArea(
                json.dumps(list(self.suggested_paths), indent=2),
                id="linked-git-broker-paths",
                soft_wrap=True,
            )
            if self.scoped_change_summary:
                yield Static(
                    self.scoped_change_summary,
                    id="linked-git-broker-change-summary",
                )
            yield Static(
                "Protected/unapproved dirt: excluded and unobserved. Agent PBX "
                "will not enumerate, open, stat, hash, or pass those paths to Git.",
                id="linked-git-broker-protected",
            )
            with Horizontal(id="linked-git-broker-actions"):
                yield Button(
                    "Review Exact Subset",
                    id="linked-git-broker-review",
                    variant="warning",
                )
                yield Button("Cancel", id="linked-git-broker-cancel")

    def submit(self) -> None:
        raw = self.query_one("#linked-git-broker-paths", TextArea).text.strip()
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            self.notify(f"Stage paths must be a JSON array: {exc}", severity="warning")
            return
        if (
            not isinstance(value, list)
            or not value
            or any(not isinstance(path, str) or not path for path in value)
        ):
            self.notify(
                "Enumerate at least one exact leaf path; implicit/all-path staging is disabled.",
                severity="warning",
            )
            return
        if len(value) != len(set(value)):
            self.notify("Duplicate stage paths are not allowed.", severity="warning")
            return
        self.dismiss()
        self.app.run_worker(  # type: ignore[attr-defined]
            self.app.review_linked_git_broker_commit(  # type: ignore[attr-defined]
                self.context,
                tuple(value),
            ),
            name=f"linked-git-broker-review-{slugify(self.context.agent_id)}",
            exclusive=True,
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "linked-git-broker-cancel":
            event.stop()
            self.dismiss()
            return
        if event.button.id == "linked-git-broker-review":
            event.stop()
            self.submit()


class LinkedGitReviewArtifactScreen(ModalScreen[None]):
    """Read-only, bounded full-diff view for one immutable broker receipt."""

    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, *, artifact: GitReviewArtifact) -> None:
        super().__init__()
        self.artifact = artifact

    def compose(self) -> ComposeResult:
        with Vertical(id="linked-git-review-panel"):
            yield Static(
                "Exact approved Git diff\n"
                f"Artifact: {self.artifact.sha256}\n"
                f"Paths: {len(self.artifact.approved_paths)} | "
                f"+{self.artifact.total_additions} "
                f"-{self.artifact.total_deletions} | "
                f"binary={self.artifact.binary_path_count} | "
                f"truncated={self.artifact.truncated_path_count}",
                id="linked-git-review-summary",
            )
            yield TextArea(
                self.artifact.render_full_diff(),
                read_only=True,
                id="linked-git-review-diff",
            )
            yield Button("Close", id="linked-git-review-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "linked-git-review-close":
            event.stop()
            self.dismiss()


class LinkedGitCommitConfirmScreen(ModalScreen[None]):
    """Root/TUI-local confirmation for one immutable broker receipt."""

    BINDINGS = [("escape", "dismiss", "Cancel")]

    def __init__(
        self,
        *,
        context: LinkedGitBrokerTuiContextView,
        receipt: LinkedGitCommitReceipt,
    ) -> None:
        super().__init__()
        self.context = context
        self.receipt = receipt

    @property
    def expected_confirmation(self) -> str:
        return f"COMMIT {self.context.agent_id} {self.receipt.transaction_id[:12]}"

    def compose(self) -> ComposeResult:
        artifact = self._review_artifact()
        approved = json.dumps(
            [
                {
                    "path": identity.path,
                    "state": identity.state,
                    "mode": identity.mode,
                    "raw_sha256": identity.raw_sha256,
                    "raw_blob_oid": identity.raw_blob_oid,
                    "prior_index_mode": identity.index_mode,
                    "prior_index_oid": identity.index_oid,
                }
                for identity in self.receipt.path_identities
            ],
            ensure_ascii=False,
            indent=2,
        )
        review_summary = (
            "Git review artifact: "
            f"{artifact.sha256}\n"
            f"Review summary: {len(artifact.approved_paths)} paths, "
            f"+{artifact.total_additions}/-{artifact.total_deletions}, "
            f"binary={artifact.binary_path_count}, "
            f"truncated={artifact.truncated_path_count}\n"
            if artifact is not None
            else "Git review artifact: legacy receipt unavailable\n"
        )
        with Vertical(id="linked-git-broker-confirm-panel"):
            yield Static(
                "Approve exact linked-worktree commit",
                id="linked-git-broker-title",
            )
            with VerticalScroll(id="linked-git-broker-receipt-scroll"):
                yield Static(
                    f"Stopped edit fork: {self.context.agent_id}\n"
                    f"Worktree: {self.context.work_root}\n"
                    f"Transaction: {self.receipt.transaction_id}\n"
                    f"Authority: {self.context.authority_sha256}\n"
                    f"Branch: {self.receipt.branch}\n"
                    f"HEAD: {self.receipt.head}\n"
                    f"HEAD tree: {self.receipt.head_tree}\n"
                    f"Index tree: {self.receipt.index_tree}\n"
                    f"Upstream: {self.receipt.upstream} @ {self.receipt.upstream_oid}\n"
                    f"Index SHA-256: {self.receipt.index_sha256}\n"
                    f"Ref SHA-256: {self.receipt.branch_ref_sha256}\n"
                    f"Reflog SHA-256: {self.receipt.branch_reflog_sha256}\n"
                    f"{review_summary}"
                    f"Approved raw-byte identities:\n{approved}",
                    id="linked-git-broker-receipt",
                )
            yield Static(
                "Protected/unapproved dirt remains outside broker authority and "
                "is not inspected. The prepared index starts from exact original "
                "bytes and may differ only at the approved entries.",
                id="linked-git-broker-protected",
            )
            yield Static(
                f'Type "{self.expected_confirmation}" to authorize this one local transaction.',
                id="linked-git-broker-help",
            )
            yield Input(
                placeholder=self.expected_confirmation,
                id="linked-git-broker-confirmation",
            )
            with Horizontal(id="linked-git-broker-actions"):
                if artifact is not None:
                    yield Button(
                        "View Exact Diff",
                        id="linked-git-broker-view-diff",
                    )
                yield Button(
                    "Commit Exact Subset",
                    id="linked-git-broker-commit",
                    variant="error",
                )
                yield Button("Cancel", id="linked-git-broker-cancel")

    def _review_artifact(self) -> GitReviewArtifact | None:
        raw = getattr(self.receipt, "git_review_artifact", None)
        if not isinstance(raw, Mapping):
            return None
        try:
            return GitReviewArtifact.from_public_dict(raw)
        except GitReviewArtifactError:
            return None

    def on_mount(self) -> None:
        confirmation = self.query_one("#linked-git-broker-confirmation", Input)
        confirmation.focus()

    def submit(self) -> None:
        confirmation = self.query_one(
            "#linked-git-broker-confirmation",
            Input,
        ).value
        if confirmation != self.expected_confirmation:
            self.notify("Exact root confirmation did not match.", severity="warning")
            return
        self.dismiss()
        self.app.run_worker(  # type: ignore[attr-defined]
            self.app.execute_linked_git_broker_commit(  # type: ignore[attr-defined]
                self.context,
                self.receipt,
                confirmation,
            ),
            name=f"linked-git-broker-execute-{slugify(self.context.agent_id)}",
            exclusive=True,
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "linked-git-broker-cancel":
            event.stop()
            self.dismiss()
            return
        if event.button.id == "linked-git-broker-view-diff":
            event.stop()
            artifact = self._review_artifact()
            if artifact is None:
                self.notify(
                    "Exact Git review artifact is unavailable.", severity="error"
                )
                return
            self.app.push_screen(LinkedGitReviewArtifactScreen(artifact=artifact))
            return
        if event.button.id == "linked-git-broker-commit":
            event.stop()
            self.submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "linked-git-broker-confirmation":
            event.stop()
            self.submit()


class LinkedGitRecoveryConfirmScreen(ModalScreen[None]):
    """Confirm recovery of one exact transaction loaded from the private journal."""

    BINDINGS = [("escape", "dismiss", "Cancel")]

    def __init__(
        self,
        *,
        context: LinkedGitBrokerTuiContextView,
        pending: PendingLinkedGitCommit,
    ) -> None:
        super().__init__()
        self.context = context
        self.pending = pending

    @property
    def expected_confirmation(self) -> str:
        receipt = self.pending.receipt
        return f"RECOVER {self.context.agent_id} {receipt.transaction_id[:12]}"

    def compose(self) -> ComposeResult:
        receipt = self.pending.receipt
        approved = json.dumps(
            [
                {
                    "path": identity.path,
                    "state": identity.state,
                    "mode": identity.mode,
                    "raw_sha256": identity.raw_sha256,
                    "raw_blob_oid": identity.raw_blob_oid,
                    "prior_index_mode": identity.index_mode,
                    "prior_index_oid": identity.index_oid,
                }
                for identity in receipt.path_identities
            ],
            ensure_ascii=False,
            indent=2,
        )
        with Vertical(id="linked-git-broker-panel"):
            yield Static(
                "Recover exact linked-worktree transaction",
                id="linked-git-broker-title",
            )
            yield Static(
                f"Stopped edit fork: {self.context.agent_id}\n"
                f"Journal phase: {self.pending.phase}\n"
                f"Transaction: {receipt.transaction_id}\n"
                f"Authority: {self.context.authority_sha256}\n"
                f"Worktree: {self.context.work_root}\n"
                f"Branch: {receipt.branch}\n"
                f"HEAD: {receipt.head}\n"
                f"HEAD tree: {receipt.head_tree}\n"
                f"Index tree: {receipt.index_tree}\n"
                f"Upstream: {receipt.upstream} @ {receipt.upstream_oid}\n"
                f"Index SHA-256: {receipt.index_sha256}\n"
                f"Ref SHA-256: {receipt.branch_ref_sha256}\n"
                f"Reflog SHA-256: {receipt.branch_reflog_sha256}\n"
                f"Approved raw-byte identities:\n{approved}",
                id="linked-git-broker-receipt",
            )
            yield Static(
                "Recovery can only complete or restore the exact journaled ref/index "
                "transition. Drift fails closed for manual review; the fork remains stopped.",
                id="linked-git-broker-protected",
            )
            yield Static(
                f'Type "{self.expected_confirmation}" to authorize this one recovery.',
                id="linked-git-broker-help",
            )
            yield Input(
                placeholder=self.expected_confirmation,
                id="linked-git-broker-confirmation",
            )
            with Horizontal(id="linked-git-broker-actions"):
                yield Button(
                    "Recover Exact Transaction",
                    id="linked-git-broker-recover",
                    variant="error",
                )
                yield Button("Cancel", id="linked-git-broker-cancel")

    def submit(self) -> None:
        confirmation = self.query_one(
            "#linked-git-broker-confirmation",
            Input,
        ).value
        if confirmation != self.expected_confirmation:
            self.notify(
                "Exact recovery confirmation did not match.", severity="warning"
            )
            return
        self.dismiss()
        self.app.run_worker(  # type: ignore[attr-defined]
            self.app.recover_linked_git_broker_commit(  # type: ignore[attr-defined]
                self.context,
                self.pending,
                confirmation,
            ),
            name=f"linked-git-broker-recover-{slugify(self.context.agent_id)}",
            exclusive=True,
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "linked-git-broker-cancel":
            event.stop()
            self.dismiss()
            return
        if event.button.id == "linked-git-broker-recover":
            event.stop()
            self.submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "linked-git-broker-confirmation":
            event.stop()
            self.submit()
