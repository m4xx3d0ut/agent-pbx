import ast
from pathlib import Path
from types import SimpleNamespace

from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, DataTable, Input, Select, Static, TextArea

from agent_pbx import operator_delegation, tui
from agent_pbx.ui import preferences
from agent_pbx.ui import presentation_options
from agent_pbx.ui import screens
from agent_pbx.ui.screens._shared import format_timestamp, truncate
from agent_pbx.ui.screens.operator_resources import (
    DurableHelperRegistryScreen,
    DynamicResourceApprovalScreen,
)


EXTRACTED_MODAL_SCREENS = (
    "CustomSlashCommandArgScreen",
    "DurableHelperRegistryScreen",
    "DynamicResourceApprovalScreen",
    "EditorCloseConfirmScreen",
    "FileDeleteConfirmScreen",
    "FilePathActionScreen",
    "FileTrashScreen",
    "GitDiffScreen",
    "GitPushScreen",
    "LinkedGitCommitConfirmScreen",
    "LinkedGitCommitPathsScreen",
    "LinkedGitRecoveryConfirmScreen",
    "LinkedGitReviewArtifactScreen",
    "ManagedProjectPickerScreen",
    "JoplinDeleteConfirmScreen",
    "JoplinNoteTitleScreen",
    "JoplinPasteConfirmScreen",
    "JoplinReferencePromptScreen",
    "IssueClearConfirmScreen",
    "ModelElevationScreen",
    "OperatorAuthorityScreen",
    "OperatorDelegationLeaseConfirmScreen",
    "OperatorHandoffApprovalScreen",
    "OperatorHandoffApprovalConfirmScreen",
    "OperatorHandoffCancelConfirmScreen",
    "OperatorHandoffCompletionScreen",
    "OperatorHistoryScreen",
    "OperatorKillConfirmScreen",
    "OperatorProjectSpawnCancelScreen",
    "OperatorReadyActionScreen",
    "PullRequestMergeConfirmScreen",
    "RuntimeMigrationScreen",
    "SettingsScreen",
    "WorkerBeeAccessScreen",
    "WorkerBeeControlledRunConfirmScreen",
    "WorkerBeeControlledRunLeaseConfirmScreen",
    "WorkerBeeControlledRunLeaseScreen",
    "WorkerBeeControlledRunScreen",
    "WorkerBeeSnapshotScreen",
    "WorkerBeeSwitchConfirmScreen",
    "WorkerBeeWorkspaceScreen",
)


def test_ui_screens_owns_complete_modal_inventory() -> None:
    actual = tuple(
        name
        for name in screens.__all__
        if isinstance(getattr(screens, name), type)
        and issubclass(getattr(screens, name), ModalScreen)
    )

    assert actual == EXTRACTED_MODAL_SCREENS
    assert all(getattr(tui, name) is getattr(screens, name) for name in actual)

    tree = ast.parse(Path(tui.__file__).read_text(encoding="utf-8"))
    modal_classes = [
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and any("ModalScreen" in ast.unparse(base) for base in node.bases)
    ]
    assert modal_classes == []


def test_command_joplin_codex_screen_aliases_are_object_identical() -> None:
    names = (
        "GitDiffScreen",
        "GitPushScreen",
        "CustomSlashCommandArgScreen",
        "EditorCloseConfirmScreen",
        "FileDeleteConfirmScreen",
        "FilePathActionScreen",
        "FileTrashScreen",
        "JoplinReferencePromptScreen",
        "IssueClearConfirmScreen",
        "LinkedGitCommitConfirmScreen",
        "LinkedGitCommitPathsScreen",
        "LinkedGitRecoveryConfirmScreen",
        "LinkedGitReviewArtifactScreen",
        "ManagedProjectPickerScreen",
        "JoplinNoteTitleScreen",
        "JoplinDeleteConfirmScreen",
        "JoplinPasteConfirmScreen",
        "ModelElevationScreen",
        "OperatorAuthorityScreen",
        "OperatorDelegationLeaseConfirmScreen",
        "OperatorHistoryScreen",
        "OperatorKillConfirmScreen",
        "OperatorProjectSpawnCancelScreen",
        "OperatorReadyActionScreen",
        "PullRequestMergeConfirmScreen",
        "RuntimeMigrationScreen",
        "SettingsScreen",
        "WorkerBeeAccessScreen",
        "WorkerBeeControlledRunConfirmScreen",
        "WorkerBeeControlledRunLeaseConfirmScreen",
        "WorkerBeeControlledRunLeaseScreen",
        "WorkerBeeControlledRunScreen",
        "WorkerBeeSnapshotScreen",
        "WorkerBeeSwitchConfirmScreen",
        "WorkerBeeWorkspaceScreen",
        "slugify",
    )

    for name in names:
        assert getattr(tui, name) is getattr(screens, name)

    assert tui.normalize_joplin_copy_mode is preferences.normalize_joplin_copy_mode
    assert (
        tui.operator_ready_action_control
        is screens.operator_ready_action_control
    )
    assert (
        tui.delegation_lease_ineligibility_reason
        is operator_delegation.delegation_lease_ineligibility_reason
    )


async def test_extracted_handoff_approval_confirm_alias_cancel_and_approve_hook() -> None:
    assert (
        tui.OperatorHandoffApprovalConfirmScreen
        is screens.OperatorHandoffApprovalConfirmScreen
    )

    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    approved: list[tuple[str, tuple[str, ...], object]] = []
    approval_screen = SimpleNamespace(app=app)

    async def approve_operator_handoff_ids(
        operator_agent_id: str,
        handoff_ids: tuple[str, ...],
        *,
        approval_screen: object,
    ) -> None:
        approved.append((operator_agent_id, handoff_ids, approval_screen))

    app.approve_operator_handoff_ids = approve_operator_handoff_ids  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        cancel_screen = screens.OperatorHandoffApprovalConfirmScreen(
            operator_agent_id="operator-0",
            handoff_ids=("handoff-1",),
            approval_screen=approval_screen,
        )
        app.push_screen(cancel_screen)
        await pilot.pause()
        await pilot.click("#operator-handoff-confirm-cancel")
        await pilot.pause()

        assert app.screen is not cancel_screen
        assert approved == []

        confirm_screen = screens.OperatorHandoffApprovalConfirmScreen(
            operator_agent_id="operator-0",
            handoff_ids=("handoff-1", "handoff-2"),
            approval_screen=approval_screen,
        )
        app.push_screen(confirm_screen)
        await pilot.pause()
        await pilot.click("#operator-handoff-confirm")
        await pilot.pause()

        assert app.screen is not confirm_screen

    assert approved == [
        ("operator-0", ("handoff-1", "handoff-2"), approval_screen)
    ]


def test_extracted_operator_ready_action_control_preserves_guarded_controls() -> None:
    assert screens.operator_ready_action_control({"next_action": "stop"}) == (
        "/operator stop"
    )
    assert screens.operator_ready_action_control({"next_action": "broker_review"}) == (
        "/operator git commit"
    )
    assert screens.operator_ready_action_control({"next_action": "broker_commit"}) == (
        "finish the open broker confirmation "
        "(/operator git recover after an interrupted transaction)"
    )
    assert screens.operator_ready_action_control({"next_action": "resume"}) == (
        "/operator resume"
    )
    assert screens.operator_ready_action_control({"next_action": "validate"}) == (
        "/operator action validate"
    )
    assert screens.operator_ready_action_control({"next_action": "unknown"}) == (
        "manual review"
    )


async def test_extracted_operator_ready_action_screen_preserves_focus_and_run_hooks() -> (
    None
):
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    calls: list[tuple[str, object]] = []
    action = {
        "handoff_id": "handoff-1",
        "campaign_id": "campaign-1",
        "target_agent_id": "operator-8-fork",
        "state": "stopped_ready_broker",
        "next_action": "broker_review",
        "scope_sha256": "a" * 64,
        "execution_scope": {"exact_paths": ["src/app.py"]},
    }

    async def focus_operator_ready_action(received: dict[str, object]) -> None:
        calls.append(("focus", received))

    async def advance_operator_ready_action(received: dict[str, object]) -> None:
        calls.append(("run", received))

    app.focus_operator_ready_action = focus_operator_ready_action  # type: ignore[method-assign]
    app.advance_operator_ready_action = advance_operator_ready_action  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        focus_screen = screens.OperatorReadyActionScreen(action)
        app.push_screen(focus_screen)
        await pilot.pause()

        summary = focus_screen.query_one(
            "#operator-handoff-confirm-summary", Static
        )
        assert "Guarded control: /operator git commit" in str(summary.render())
        assert await pilot.click("#operator-ready-action-focus")
        await pilot.pause()
        assert app.screen is not focus_screen

        run_screen = screens.OperatorReadyActionScreen(action)
        app.push_screen(run_screen)
        await pilot.pause()
        assert await pilot.click("#operator-ready-action-run")
        await pilot.pause()
        assert app.screen is not run_screen

    assert calls == [("focus", action), ("run", action)]


async def test_extracted_delegation_lease_screen_guards_duplicate_submit() -> None:
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    submitted: list[tuple[str, tuple[str, ...], object]] = []
    approval_screen = SimpleNamespace(app=app)

    async def create_operator_delegation_lease_from_handoffs(
        operator_agent_id: str,
        handoff_ids: tuple[str, ...],
        *,
        approval_screen: object,
    ) -> None:
        submitted.append((operator_agent_id, handoff_ids, approval_screen))

    app.create_operator_delegation_lease_from_handoffs = (  # type: ignore[method-assign]
        create_operator_delegation_lease_from_handoffs
    )
    async with app.run_test() as pilot:
        screen = screens.OperatorDelegationLeaseConfirmScreen(
            operator_agent_id="operator-0",
            handoff_ids=("handoff-1", "handoff-2"),
            approval_screen=approval_screen,
        )
        app.push_screen(screen)
        await pilot.pause()
        confirm = screen.query_one("#operator-delegation-confirm", Button)
        cancel = screen.query_one("#operator-delegation-cancel", Button)
        event = SimpleNamespace(button=confirm, stop=lambda: None)

        screen.on_button_pressed(event)  # type: ignore[arg-type]
        screen.on_button_pressed(event)  # type: ignore[arg-type]
        await pilot.pause()

        assert screen.submitted is True
        assert confirm.disabled is True
        assert cancel.disabled is True

    assert submitted == [
        ("operator-0", ("handoff-1", "handoff-2"), approval_screen)
    ]


def test_extracted_operator_handoff_family_aliases_are_object_identical() -> None:
    for name in (
        "OperatorHandoffApprovalScreen",
        "OperatorHandoffCancelConfirmScreen",
        "OperatorHandoffCompletionScreen",
    ):
        assert getattr(tui, name) is getattr(screens, name)

    assert (
        tui.OPERATOR_HANDOFF_CANCEL_REASON_CHOICES
        is presentation_options.OPERATOR_HANDOFF_CANCEL_REASON_CHOICES
    )


def test_extracted_operator_resource_aliases_and_shared_helpers_are_preserved() -> None:
    assert (
        tui.DurableHelperRegistryScreen
        is screens.DurableHelperRegistryScreen
        is DurableHelperRegistryScreen
    )
    assert (
        tui.DynamicResourceApprovalScreen
        is screens.DynamicResourceApprovalScreen
        is DynamicResourceApprovalScreen
    )
    assert format_timestamp(0.0) == "1970-01-01 00:00"
    assert format_timestamp("invalid") == "-"
    assert truncate("abcdef", 4) == "abc…"
    assert truncate("abc", 4) == "abc"
    assert screens.OperatorHandoffApprovalScreen.format_timestamp is format_timestamp
    assert screens.OperatorHandoffApprovalScreen.truncate is truncate


async def test_extracted_durable_helper_screen_preserves_table_and_app_hooks() -> None:
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    calls: list[tuple[str, object, str | None, str | None]] = []
    helper = {
        "helper_id": "helper-1",
        "name": "release-check",
        "status": "reviewed",
        "object_sha256": "1234567890abcdef",
        "scopes": ["project", "campaign"],
    }

    async def refresh(screen: object) -> None:
        calls.append(("refresh", screen, None, None))

    async def apply(
        screen: object,
        *,
        helper_id: str,
        action: str,
    ) -> None:
        calls.append(("apply", screen, helper_id, action))

    app.refresh_durable_helper_screen = refresh  # type: ignore[method-assign]
    app.apply_durable_helper_action = apply  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        screen = DurableHelperRegistryScreen(
            operator_agent_id="operator-0",
            helpers=[helper, {"name": "missing-id"}],
        )
        app.push_screen(screen)
        await pilot.pause()

        table = screen.query_one("#durable-helper-table", DataTable)
        assert screen.BINDINGS == [("escape", "dismiss", "Close")]
        assert [column.label.plain for column in table.columns.values()] == [
            "Name",
            "Status",
            "Digest",
            "Scopes",
            "Helper ID",
        ]
        assert table.get_row_at(0) == [
            "release-check",
            "reviewed",
            "1234567890ab",
            "project, campaign",
            "helper-1",
        ]
        assert screen.selected_helper_id() == "helper-1"
        assert [button.id for button in screen.query(Button)] == [
            "durable-helper-review",
            "durable-helper-promote",
            "durable-helper-reject",
            "durable-helper-retire",
            "durable-helper-quarantine",
            "durable-helper-refresh",
            "durable-helper-close",
        ]

        for button_id in (
            "durable-helper-review",
            "durable-helper-promote",
            "durable-helper-reject",
            "durable-helper-retire",
            "durable-helper-quarantine",
            "durable-helper-refresh",
        ):
            screen.on_button_pressed(  # type: ignore[arg-type]
                SimpleNamespace(
                    button=screen.query_one(f"#{button_id}", Button),
                    stop=lambda: None,
                )
            )
            await pilot.pause()

        screen.on_button_pressed(  # type: ignore[arg-type]
            SimpleNamespace(
                button=screen.query_one("#durable-helper-close", Button),
                stop=lambda: None,
            )
        )
        await pilot.pause()
        assert app.screen is not screen

    assert calls == [
        ("apply", screen, "helper-1", "review"),
        ("apply", screen, "helper-1", "active"),
        ("apply", screen, "helper-1", "rejected"),
        ("apply", screen, "helper-1", "retired"),
        ("apply", screen, "helper-1", "quarantined"),
        ("refresh", screen, None, None),
    ]


async def test_extracted_dynamic_resource_screen_preserves_f8_selection_and_hooks() -> (
    None
):
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    calls: list[tuple[str, object, object | None]] = []
    requests = [
        {
            "request_id": "resource-1",
            "request_sha256": "a" * 64,
            "resource_class": "managed_cache",
            "requester_agent_id": "caller-1",
            "campaign_id": "campaign-1234567890",
            "workspace_set_digest": "b" * 64,
            "spec": {"max_bytes": 4096, "name": "pytest-cache"},
            "approval_confirmation": "APPROVE RESOURCE resource-1",
            "restart_required": False,
            "expires_at": 0.0,
        },
        {
            "request_id": "resource-2",
            "request_sha256": "c" * 64,
            "resource_class": "managed_tmpfs",
            "requester_agent_id": "caller-2",
            "handoff_id": "handoff-1234567890",
            "workspace_set_digest": "d" * 64,
            "spec": {"size_mb": 128},
            "approval_confirmation": "APPROVE RESOURCE resource-2",
            "restart_required": True,
            "expires_at": "invalid",
        },
    ]

    async def approve(screen: object, request: object) -> None:
        calls.append(("approve", screen, request))

    async def refresh(screen: object) -> None:
        calls.append(("refresh", screen, None))

    app.approve_dynamic_resource_request = approve  # type: ignore[method-assign]
    app.refresh_dynamic_resource_screen = refresh  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        screen = DynamicResourceApprovalScreen(
            operator_agent_id="operator-0",
            requests=requests,
        )
        app.push_screen(screen)
        await pilot.pause()

        table = screen.query_one("#dynamic-resource-table", DataTable)
        assert [column.label.plain for column in table.columns.values()] == [
            "Class",
            "Requester",
            "Campaign",
            "Restart",
            "Expires",
            "Request ID",
        ]
        assert table.get_row("resource-1") == [
            "managed_cache",
            "caller-1",
            "campaign-123",
            "no",
            "1970-01-01 00:00",
            "resource-1",
        ]
        assert table.get_row("resource-2") == [
            "managed_tmpfs",
            "caller-2",
            "handoff-1234",
            "yes",
            "-",
            "resource-2",
        ]
        assert screen.current_request() is requests[0]
        assert requests[0]["approval_confirmation"] in str(
            screen.query_one("#dynamic-resource-detail", Static).renderable
        )
        assert [button.id for button in screen.query(Button)] == [
            "dynamic-resource-refresh",
            "dynamic-resource-close",
        ]

        table.move_cursor(row=1, animate=False, scroll=False)
        await pilot.pause()
        assert screen.current_request() is requests[1]
        assert requests[1]["approval_confirmation"] in str(
            screen.query_one("#dynamic-resource-detail", Static).renderable
        )
        await pilot.press("f8")
        await pilot.pause()
        assert await pilot.click("#dynamic-resource-refresh")
        await pilot.pause()
        assert await pilot.click("#dynamic-resource-close")
        await pilot.pause()
        assert app.screen is not screen

    assert calls == [
        ("approve", screen, requests[1]),
        ("refresh", screen, None),
    ]


async def test_extracted_handoff_cancel_preserves_validation_and_payload() -> None:
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    notices: list[tuple[str, str]] = []
    submitted: list[dict[str, object]] = []
    approval_screen = SimpleNamespace(app=app)
    handoffs = (
        {
            "handoff_id": "handoff-1",
            "target_operator_agent_id": "operator-7",
            "objective": "Replace the legacy request with a structured handoff.",
        },
        {
            "handoff_id": "handoff-2",
            "target_operator_agent_id": "operator-8",
            "objective": "Withdraw the duplicate request.",
        },
    )

    def notify(message: str, *, severity: str = "information", **_kwargs: object) -> None:
        notices.append((message, severity))

    async def cancel_operator_handoff_ids(
        operator_agent_id: str,
        handoff_ids: tuple[str, ...],
        *,
        reason: str,
        detail: str,
        superseded_by_handoff_ids: tuple[str, ...],
        approval_screen: object,
    ) -> None:
        submitted.append(
            {
                "operator_agent_id": operator_agent_id,
                "handoff_ids": handoff_ids,
                "reason": reason,
                "detail": detail,
                "superseded_by_handoff_ids": superseded_by_handoff_ids,
                "approval_screen": approval_screen,
            }
        )

    app.notify = notify  # type: ignore[method-assign]
    app.cancel_operator_handoff_ids = cancel_operator_handoff_ids  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        screen = screens.OperatorHandoffCancelConfirmScreen(
            operator_agent_id="operator-0",
            handoffs=handoffs,
            approval_screen=approval_screen,  # type: ignore[arg-type]
        )
        app.push_screen(screen)
        await pilot.pause()

        reason = screen.query_one("#operator-handoff-cancel-reason", Select)
        confirm = screen.query_one("#operator-handoff-cancel-confirm", Button)
        confirm_event = SimpleNamespace(button=confirm, stop=lambda: None)
        reason.value = "other"
        screen.on_button_pressed(confirm_event)  # type: ignore[arg-type]
        await pilot.pause()

        assert app.screen is screen
        assert submitted == []
        assert notices[-1] == (
            "Cancellation detail is required when the reason is Other.",
            "warning",
        )

        screen.query_one("#operator-handoff-cancel-detail", Input).value = (
            "Operator requested withdrawal."
        )
        screen.query_one("#operator-handoff-cancel-replacements", Input).value = (
            "replacement-2 replacement-1,replacement-2"
        )
        screen.on_button_pressed(confirm_event)  # type: ignore[arg-type]
        await pilot.pause()

        assert app.screen is not screen

    assert submitted == [
        {
            "operator_agent_id": "operator-0",
            "handoff_ids": ("handoff-1", "handoff-2"),
            "reason": "other",
            "detail": "Operator requested withdrawal.",
            "superseded_by_handoff_ids": ("replacement-2", "replacement-1"),
            "approval_screen": approval_screen,
        }
    ]


async def test_extracted_handoff_completion_preserves_table_and_workers() -> None:
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    calls: list[tuple[str, object, object | None]] = []
    delivery = {
        "completion_delivery_id": "receipt-1",
        "target_operator_agent_id": "operator-7",
        "terminal_status": "complete",
        "delivery_status": "sent",
        "summary": "Checkpoint completed and validated.",
        "created_at": 0.0,
    }

    async def focus(screen: object, selected: object) -> None:
        calls.append(("focus", screen, selected))

    async def acknowledge(screen: object, selected: object) -> None:
        calls.append(("ack", screen, selected))

    async def retry(screen: object, selected: object) -> None:
        calls.append(("retry", screen, selected))

    async def refresh(screen: object) -> None:
        calls.append(("refresh", screen, None))

    app.focus_operator_completion_receipt = focus  # type: ignore[method-assign]
    app.acknowledge_operator_completion_receipt = acknowledge  # type: ignore[method-assign]
    app.retry_operator_completion_receipt = retry  # type: ignore[method-assign]
    app.refresh_operator_completion_screen = refresh  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        screen = screens.OperatorHandoffCompletionScreen(
            recipient_agent_id="operator-0",
            deliveries=[delivery],
        )
        app.push_screen(screen)
        await pilot.pause()

        table = screen.query_one("#operator-completion-table", DataTable)
        assert [column.label.plain for column in table.columns.values()] == [
            "Created",
            "From",
            "Result",
            "Delivery",
            "Summary",
            "ID",
        ]
        assert table.get_row("receipt-1") == [
            "1970-01-01 00:00",
            "operator-7",
            "complete",
            "sent",
            "Checkpoint completed and validated.",
            "receipt-1",
        ]
        assert screen.current_delivery() is delivery

        for button_id in (
            "operator-completion-focus",
            "operator-completion-ack",
            "operator-completion-retry",
            "operator-completion-refresh",
        ):
            assert await pilot.click(f"#{button_id}")
            await pilot.pause()

        assert await pilot.click("#operator-completion-close")
        await pilot.pause()
        assert app.screen is not screen

    assert calls == [
        ("focus", screen, delivery),
        ("ack", screen, delivery),
        ("retry", screen, delivery),
        ("refresh", screen, None),
    ]


async def test_extracted_handoff_approval_preserves_selection_and_nested_screens() -> None:
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    calls: list[tuple[str, object]] = []
    handoffs = [
        {
            "handoff_id": "handoff-1",
            "target_operator_agent_id": "operator-7",
            "status": "proposed",
            "objective": "Validate the release.",
            "created_at": 0.0,
            "execution_scope": {"action_class": "test_validation"},
        },
        {
            "handoff_id": "handoff-2",
            "target_operator_agent_id": "operator-8",
            "status": "proposed",
            "objective": "Review the implementation.",
            "created_at": None,
            "execution_scope": {},
        },
    ]

    async def preflight(
        operator_agent_id: str,
        handoff_ids: tuple[str, ...],
    ) -> None:
        calls.append(("preflight", (operator_agent_id, handoff_ids)))

    async def refresh(screen: object) -> None:
        calls.append(("refresh", screen))

    app.preflight_operator_handoff_ids = preflight  # type: ignore[method-assign]
    app.refresh_operator_handoff_approval_screen = refresh  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        screen = screens.OperatorHandoffApprovalScreen(
            operator_agent_id="operator-0",
            handoffs=handoffs,
        )
        app.push_screen(screen)
        await pilot.pause()

        table = screen.query_one("#operator-handoff-approval-table", DataTable)
        assert [column.label.plain for column in table.columns.values()] == [
            "Mark",
            "Created",
            "Target",
            "Class",
            "Status",
            "Objective",
            "ID",
        ]
        assert table.get_row("handoff-1") == [
            "○",
            "1970-01-01 00:00",
            "operator-7",
            "test_validation",
            "proposed",
            "Validate the release.",
            "handoff-1",
        ]
        assert screen.format_timestamp("invalid") == "-"
        assert screen.truncate("abcdef", 4) == "abc…"
        assert screen.selected_handoff_ids() == ("handoff-1",)

        screen.action_toggle_selected()
        table.move_cursor(row=1, animate=False, scroll=False)
        screen.action_toggle_selected()
        assert screen.selected_handoff_ids() == ("handoff-1", "handoff-2")

        assert await pilot.click("#operator-handoff-approve")
        await pilot.pause()
        confirm = app.screen
        assert isinstance(confirm, screens.OperatorHandoffApprovalConfirmScreen)
        assert confirm.handoff_ids == ("handoff-1", "handoff-2")
        assert confirm.approval_screen is screen
        assert await pilot.click("#operator-handoff-confirm-cancel")
        await pilot.pause()
        assert app.screen is screen

        assert await pilot.click("#operator-handoff-cancel")
        await pilot.pause()
        cancel = app.screen
        assert isinstance(cancel, screens.OperatorHandoffCancelConfirmScreen)
        assert cancel.handoffs == tuple(handoffs)
        assert cancel.approval_screen is screen
        assert await pilot.click("#operator-handoff-cancel-back")
        await pilot.pause()
        assert app.screen is screen

        assert await pilot.click("#operator-handoff-preflight")
        await pilot.pause()
        refresh_button = screen.query_one("#operator-handoff-refresh", Button)
        screen.on_button_pressed(  # type: ignore[arg-type]
            SimpleNamespace(button=refresh_button, stop=lambda: None)
        )
        await pilot.pause()

    assert calls == [
        ("preflight", ("operator-0", ("handoff-1", "handoff-2"))),
        ("refresh", screen),
    ]


async def test_extracted_managed_project_picker_preserves_launch_order_and_payload() -> (
    None
):
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    launched: list[dict[str, object]] = []
    projects = [
        {
            "path": "/tmp/demo",
            "name": "demo",
            "branch": "dev",
            "available": True,
            "owned_by": ["agent-existing"],
        },
        {
            "path": "/tmp/next",
            "name": "next",
            "branch": "main",
            "available": True,
            "owned_by": ["agent-next"],
        },
    ]

    async def launch(**request: object) -> bool:
        assert app.screen is not screen
        assert app.query_one("#agent-id", Input) is not None
        launched.append(request)
        return True

    app.launch_managed_workspace_from_picker = launch  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        screen = screens.ManagedProjectPickerScreen(projects, ["/tmp"])
        app.push_screen(screen)
        await pilot.pause()

        table = screen.query_one("#managed-projects", DataTable)
        assert table.row_count == 2
        assert screen.selected_path == "/tmp/demo"
        assert screen.selected_owners == ["agent-existing"]
        table.focus()
        await pilot.press("down", "enter")
        await pilot.pause()
        assert screen.selected_path == "/tmp/next"
        assert screen.selected_owners == ["agent-next"]
        screen.query_one("#managed-agent-id", Input).value = "agent-demo"
        screen.query_one("#managed-launch-mode", Select).value = "agent_operator"
        screen.query_one("#managed-agent-profile", Select).value = "sol-6.2-xhigh"

        screen.query_one("#managed-project-launch", Button).press()
        await pilot.pause()

    assert launched == [
        {
            "project_path": "/tmp/next",
            "agent_id": "agent-demo",
            "profile_id": "sol-6.2-xhigh",
            "runtime_server_mode": "dedicated",
            "launch_mode": "agent_operator",
            "existing_owner_ids": ["agent-next"],
        }
    ]


async def test_extracted_runtime_migration_preserves_apply_and_rollback_hooks() -> None:
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    applied: list[dict[str, object]] = []
    rolled_back: list[str] = []
    batch = {
        "batch_id": "batch-1",
        "eligible_count": 1,
        "blocked_count": 0,
        "retention_until": "2026-11-21T00:00:00Z",
        "candidates": [
            {
                "agent_id": "agent-demo",
                "agent_type": "caller",
                "session_id": "session-demo",
                "eligible": True,
                "blockers": [],
            }
        ],
    }

    async def apply(received: dict[str, object]) -> list[dict[str, object]]:
        assert app.screen is apply_screen
        applied.append(received)
        return []

    async def rollback(batch_id: str) -> None:
        assert app.screen is rollback_screen
        rolled_back.append(batch_id)

    app.apply_runtime_migration_batch = apply  # type: ignore[method-assign]
    app.rollback_runtime_migration_batch = rollback  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        apply_screen = screens.RuntimeMigrationScreen(batch)
        app.push_screen(apply_screen)
        await pilot.pause()

        assert (
            apply_screen.query_one("#runtime-migration-candidates", DataTable).row_count
            == 1
        )
        assert "Eligible 1 · Blocked 0" in str(
            apply_screen.query_one("#runtime-migration-summary", Static).renderable
        )
        apply_screen.query_one("#runtime-migration-apply", Button).press()
        await pilot.pause()
        assert app.screen is not apply_screen

        rollback_screen = screens.RuntimeMigrationScreen(batch)
        app.push_screen(rollback_screen)
        await pilot.pause()
        rollback_screen.query_one("#runtime-migration-rollback", Button).press()
        await pilot.pause()
        assert app.screen is not rollback_screen

    assert applied == [batch]
    assert rolled_back == ["batch-1"]


def test_extracted_settings_normalizes_constructor_values() -> None:
    screen = screens.SettingsScreen(
        visual_flash_enabled=True,
        terminal_bell_enabled=False,
        agent_blink_enabled=True,
        low_power_enabled=False,
        layout_mode="adaptive",
        split_percent=42,
        joplin_copy_mode="legacy-clipboard",
        tmux_direct_enabled=True,
        tmux_features_available=False,
        tmux_runtime_server_mode="outer-if-present",
        embedded_terminal_enabled=True,
        embedded_terminal_scroll_mode="invalid",
        tmux_popout_mode="auto",
        custom_theme_name="1337",
        theme_name="cyberpunk",
    )

    assert screen.joplin_copy_mode == "copy_first"
    assert screen.tmux_runtime_server_mode == "outer_if_present"
    assert screen.embedded_terminal_scroll_mode == "tmux"


async def test_extracted_settings_preserves_controls_and_app_hooks() -> None:
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    calls: list[tuple[str, object]] = []

    def adjust_split_percent(delta: int) -> None:
        calls.append(("split", delta))
        app.split_percent += delta

    def reset_split_percent() -> None:
        calls.append(("split_reset", None))
        app.split_percent = 42

    app.adjust_split_percent = adjust_split_percent  # type: ignore[method-assign]
    app.reset_split_percent = reset_split_percent  # type: ignore[method-assign]
    app.set_layout_mode = lambda value: calls.append(("layout", value))  # type: ignore[method-assign]
    app.set_ui_theme = lambda value: calls.append(("theme", value))  # type: ignore[method-assign]
    app.set_joplin_copy_mode = lambda value: calls.append(("joplin", value))  # type: ignore[method-assign]
    app.set_tmux_runtime_server_mode = lambda value: calls.append(("runtime", value))  # type: ignore[method-assign]
    app.set_embedded_terminal_scroll_mode = lambda value: calls.append(
        ("scroll", value)
    )  # type: ignore[method-assign]
    app.set_tmux_popout_mode = lambda value: calls.append(("popout", value))  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        screen = screens.SettingsScreen(
            visual_flash_enabled=True,
            terminal_bell_enabled=False,
            agent_blink_enabled=True,
            low_power_enabled=False,
            layout_mode="adaptive",
            split_percent=42,
            joplin_copy_mode="copy_first",
            tmux_direct_enabled=False,
            tmux_features_available=False,
            tmux_runtime_server_mode="dedicated",
            embedded_terminal_enabled=False,
            embedded_terminal_scroll_mode="tmux",
            tmux_popout_mode="auto",
            custom_theme_name="1337",
            theme_name="cyberpunk",
        )
        app.push_screen(screen)
        await pilot.pause()

        assert screen.query_one("#visual-flash", Checkbox).value is True
        assert screen.query_one("#terminal-bell", Checkbox).value is False
        assert screen.query_one("#tmux-direct", Checkbox).disabled is True
        assert screen.query_one("#embedded-terminal-v2", Checkbox).disabled is True
        assert (
            screen.query_one("#embedded-terminal-scroll-mode", Select).disabled is True
        )
        assert screen.query_one("#layout-mode", Select).value == "adaptive"
        assert screen.query_one("#joplin-copy-mode", Select).value == "copy_first"
        assert screen.query_one("#tmux-runtime-mode", Select).value == "dedicated"
        assert screen.query_one("#theme-mode", Select).value == "cyberpunk"
        assert screen.query_one("#settings-close", Button).label.plain == "Close"

        await pilot.click("#split-widen")
        await pilot.pause()
        assert calls[-1] == ("split", 5)
        assert str(screen.query_one("#split-percent-label", Static).renderable) == (
            "Agents width: 47%"
        )

        await pilot.click("#split-reset")
        await pilot.pause()
        assert calls[-1] == ("split_reset", None)
        assert str(screen.query_one("#split-percent-label", Static).renderable) == (
            "Agents width: 42%"
        )

        screen.query_one("#layout-mode", Select).value = "compact"
        screen.query_one("#theme-mode", Select).value = "minimal"
        screen.query_one("#joplin-copy-mode", Select).value = "transcript_first"
        screen.query_one("#tmux-runtime-mode", Select).value = "outer_if_present"
        screen.query_one("#embedded-terminal-scroll-mode", Select).value = "child"
        screen.query_one("#tmux-popout-mode", Select).value = "suspend_attach"
        await pilot.pause()

        assert ("layout", "compact") in calls
        assert ("theme", "minimal") in calls
        assert ("joplin", "transcript_first") in calls
        assert ("runtime", "outer_if_present") in calls
        assert ("scroll", "child") in calls
        assert ("popout", "suspend_attach") in calls

        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is not screen


async def test_extracted_git_diff_cancel_dismisses_without_submit() -> None:
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    submitted: list[tuple[str, str]] = []

    async def submit(agent_id: str, target: str = "") -> None:
        submitted.append((agent_id, target))

    app.palette_git_diff_target = submit  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        screen = screens.GitDiffScreen(agent_id="agent-1")
        app.push_screen(screen)
        await pilot.pause()

        await pilot.click("#palette-gitdiff-cancel")
        await pilot.pause()

        assert app.screen is not screen

    assert submitted == []


async def test_extracted_linked_git_paths_submit_and_cancel() -> None:
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    reviewed: list[tuple[object, tuple[str, ...]]] = []
    context = SimpleNamespace(
        agent_id="operator-7-fork-linked",
        work_root="/tmp/linked",
        authority_sha256="7" * 64,
    )

    async def review(received_context: object, paths: tuple[str, ...]) -> None:
        reviewed.append((received_context, paths))

    app.review_linked_git_broker_commit = review  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        submit_screen = screens.LinkedGitCommitPathsScreen(
            context=context,
            suggested_paths=("src/app.py", "tests/test_app.py"),
        )
        app.push_screen(submit_screen)
        await pilot.pause()

        await pilot.click("#linked-git-broker-review")
        await pilot.pause()

        assert app.screen is not submit_screen
        assert reviewed == [
            (context, ("src/app.py", "tests/test_app.py")),
        ]

        cancel_screen = screens.LinkedGitCommitPathsScreen(context=context)
        app.push_screen(cancel_screen)
        await pilot.pause()

        await pilot.click("#linked-git-broker-cancel")
        await pilot.pause()

        assert app.screen is not cancel_screen
        assert len(reviewed) == 1


async def test_extracted_linked_git_recovery_requires_exact_confirmation() -> None:
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    recovered: list[tuple[object, object, str]] = []
    context = SimpleNamespace(
        agent_id="operator-7-fork-linked",
        work_root="/tmp/linked",
        authority_sha256="7" * 64,
    )
    identity = SimpleNamespace(
        path="src/app.py",
        state="file",
        mode="100644",
        raw_sha256="1" * 64,
        raw_blob_oid="2" * 40,
        index_mode="100644",
        index_oid="3" * 40,
    )
    receipt = SimpleNamespace(
        transaction_id="e" * 64,
        branch="pbx/operator-7",
        head="a" * 40,
        head_tree="b" * 40,
        index_tree="b" * 40,
        upstream="origin/dev",
        upstream_oid="c" * 40,
        index_sha256="d" * 64,
        branch_ref_sha256="5" * 64,
        branch_reflog_sha256="6" * 64,
        path_identities=(identity,),
    )
    pending = SimpleNamespace(phase="prepared", receipt=receipt)

    async def recover(
        received_context: object,
        received_pending: object,
        confirmation: str,
    ) -> None:
        recovered.append((received_context, received_pending, confirmation))

    app.recover_linked_git_broker_commit = recover  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        screen = screens.LinkedGitRecoveryConfirmScreen(
            context=context,
            pending=pending,
        )
        app.push_screen(screen)
        await pilot.pause()
        confirmation = screen.query_one("#linked-git-broker-confirmation", Input)

        confirmation.value = "wrong"
        screen.submit()
        await pilot.pause()
        assert app.screen is screen
        assert recovered == []

        confirmation.value = screen.expected_confirmation
        screen.submit()
        await pilot.pause()

        assert app.screen is not screen
        assert recovered == [
            (context, pending, screen.expected_confirmation),
        ]


async def test_extracted_model_elevation_unmount_clears_modal_agent() -> None:
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    preset = tui.codex_model_preset_for("sol-max")
    assert preset is not None

    async with app.run_test() as pilot:
        screen = screens.ModelElevationScreen(
            agent_id="operator-5",
            preset=preset,
        )
        app.model_elevation_modal_agent_ids.add("operator-5")
        app.push_screen(screen)
        await pilot.pause()

        screen.dismiss()
        await pilot.pause()

    assert "operator-5" not in app.model_elevation_modal_agent_ids


async def test_extracted_file_path_submit_and_cancel() -> None:
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    submitted: list[tuple[str, str, str, str | None]] = []

    async def apply(
        agent_id: str,
        action: str,
        path: str,
        *,
        source: str | None = None,
    ) -> None:
        submitted.append((agent_id, action, path, source))

    app.apply_file_path_action = apply  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        submit_screen = screens.FilePathActionScreen(
            agent_id="agent-1",
            action="move",
            source="src/old.py",
        )
        app.push_screen(submit_screen)
        await pilot.pause()
        submit_screen.query_one("#file-path-action-input", Input).value = "src/new.py"

        await pilot.click("#file-path-action-apply")
        await pilot.pause()

        assert app.screen is not submit_screen
        assert submitted == [("agent-1", "move", "src/new.py", "src/old.py")]

        cancel_screen = screens.FilePathActionScreen(
            agent_id="agent-1",
            action="create_file",
        )
        app.push_screen(cancel_screen)
        await pilot.pause()

        await pilot.click("#file-path-action-cancel")
        await pilot.pause()

        assert app.screen is not cancel_screen
        assert submitted == [("agent-1", "move", "src/new.py", "src/old.py")]


async def test_extracted_file_delete_submit_and_cancel() -> None:
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    deleted: list[tuple[str, dict[str, object]]] = []

    async def delete(agent_id: str, preview: dict[str, object]) -> None:
        deleted.append((agent_id, preview))

    app.apply_file_delete = delete  # type: ignore[method-assign]
    preview = {
        "path": "src/old.py",
        "kind": "file",
        "item_count": 1,
        "size": 12,
        "delete_mode": "trash",
    }
    async with app.run_test() as pilot:
        submit_screen = screens.FileDeleteConfirmScreen(
            agent_id="agent-1",
            preview=preview,
        )
        app.push_screen(submit_screen)
        await pilot.pause()

        await pilot.click("#file-delete-confirm")
        await pilot.pause()

        assert app.screen is not submit_screen
        assert deleted == [("agent-1", preview)]

        cancel_screen = screens.FileDeleteConfirmScreen(
            agent_id="agent-1",
            preview=preview,
        )
        app.push_screen(cancel_screen)
        await pilot.pause()

        await pilot.click("#file-delete-cancel")
        await pilot.pause()

        assert app.screen is not cancel_screen
        assert deleted == [("agent-1", preview)]


async def test_extracted_file_trash_restore_and_close() -> None:
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    restored: list[tuple[str, str]] = []

    async def restore(agent_id: str, trash_id: str) -> None:
        restored.append((agent_id, trash_id))

    app.restore_file_trash = restore  # type: ignore[method-assign]
    entries = [
        {
            "trash_id": "trash-1",
            "created_at": 1_700_000_000,
            "original_path": "src/old.py",
            "git_state": "untracked",
        }
    ]
    async with app.run_test() as pilot:
        restore_screen = screens.FileTrashScreen(
            agent_id="agent-1",
            entries=entries,
        )
        app.push_screen(restore_screen)
        await pilot.pause()

        await pilot.click("#file-trash-restore")
        await pilot.pause()

        assert app.screen is not restore_screen
        assert restored == [("agent-1", "trash-1")]

        close_screen = screens.FileTrashScreen(
            agent_id="agent-1",
            entries=entries,
        )
        app.push_screen(close_screen)
        await pilot.pause()

        await pilot.click("#file-trash-close")
        await pilot.pause()

        assert app.screen is not close_screen
        assert restored == [("agent-1", "trash-1")]


async def test_extracted_editor_close_discard_and_cancel() -> None:
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    closed: list[tuple[str, bool, bool]] = []

    def close(
        agent_id: str,
        *,
        notify: bool = False,
        discarded: bool = False,
    ) -> bool:
        closed.append((agent_id, notify, discarded))
        return True

    app.close_editor_document = close  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        discard_screen = screens.EditorCloseConfirmScreen(
            agent_id="agent-1",
            path="src/app.py",
        )
        app.push_screen(discard_screen)
        await pilot.pause()

        await pilot.click("#editor-close-discard")
        await pilot.pause()

        assert app.screen is not discard_screen
        assert closed == [("agent-1", True, True)]

        cancel_screen = screens.EditorCloseConfirmScreen(
            agent_id="agent-1",
            path="src/app.py",
        )
        app.push_screen(cancel_screen)
        await pilot.pause()

        await pilot.click("#editor-close-cancel")
        await pilot.pause()

        assert app.screen is not cancel_screen
        assert closed == [("agent-1", True, True)]


async def test_extracted_pull_request_merge_submit_and_cancel() -> None:
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    merged: list[tuple[str, int, str, str]] = []

    async def merge(
        agent_id: str,
        number: int,
        *,
        method: str,
        confirm: str,
    ) -> None:
        merged.append((agent_id, number, method, confirm))

    app.merge_pull_request = merge  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        submit_screen = screens.PullRequestMergeConfirmScreen(
            agent_id="agent-1",
            number=7,
            title="Ship extraction",
            method="merge",
        )
        app.push_screen(submit_screen)
        await pilot.pause()
        submit_screen.query_one("#pr-merge-confirm", Input).value = "merge PR #7"

        await pilot.click("#pr-merge-run")
        await pilot.pause()

        assert app.screen is not submit_screen
        assert merged == [("agent-1", 7, "merge", "merge PR #7")]

        cancel_screen = screens.PullRequestMergeConfirmScreen(
            agent_id="agent-1",
            number=8,
            title="Do not merge",
        )
        app.push_screen(cancel_screen)
        await pilot.pause()

        await pilot.click("#pr-merge-cancel")
        await pilot.pause()

        assert app.screen is not cancel_screen
        assert merged == [("agent-1", 7, "merge", "merge PR #7")]


async def test_extracted_issue_clear_submit_and_cancel() -> None:
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    cleared: list[tuple[str, int, str, str]] = []

    async def clear(
        agent_id: str,
        number: int,
        *,
        comment: str,
        confirm: str,
    ) -> None:
        cleared.append((agent_id, number, comment, confirm))

    app.clear_issue = clear  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        submit_screen = screens.IssueClearConfirmScreen(
            agent_id="agent-1",
            number=9,
            title="Resolved issue",
        )
        app.push_screen(submit_screen)
        await pilot.pause()
        submit_screen.query_one("#issue-clear-comment", TextArea).load_text(
            "Resolved by the focused extraction."
        )
        submit_screen.query_one("#issue-clear-confirm", Input).value = "clear issue #9"

        await pilot.click("#issue-clear-run")
        await pilot.pause()

        assert app.screen is not submit_screen
        assert cleared == [
            (
                "agent-1",
                9,
                "Resolved by the focused extraction.",
                "clear issue #9",
            )
        ]

        cancel_screen = screens.IssueClearConfirmScreen(
            agent_id="agent-1",
            number=10,
            title="Keep open",
        )
        app.push_screen(cancel_screen)
        await pilot.pause()

        await pilot.click("#issue-clear-cancel")
        await pilot.pause()

        assert app.screen is not cancel_screen
        assert len(cleared) == 1


async def test_extracted_operator_kill_confirm_and_cancel() -> None:
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    dismissed: list[dict[str, object]] = []

    async def dismiss_selected_agent(**request: object) -> None:
        dismissed.append(request)

    app.dismiss_selected_agent = dismiss_selected_agent  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        confirm_screen = screens.OperatorKillConfirmScreen(
            agent_id="operator-6",
            action="purge",
            delete_thread=True,
        )
        app.push_screen(confirm_screen)
        await pilot.pause()

        assert (
            str(confirm_screen.query_one("#operator-kill-message", Static).renderable)
            == "Purge operator-6 and kill its tmux pane?"
        )
        await pilot.click("#operator-kill-confirm")
        await pilot.pause()

        assert app.screen is not confirm_screen
        assert dismissed == [
            {
                "agent_id": "operator-6",
                "delete_thread": True,
                "confirmed_operator_kill": True,
            }
        ]

        cancel_screen = screens.OperatorKillConfirmScreen(
            agent_id="operator-6",
            action="hide",
            delete_thread=False,
        )
        app.push_screen(cancel_screen)
        await pilot.pause()
        await pilot.click("#operator-kill-cancel")
        await pilot.pause()

        assert app.screen is not cancel_screen
        assert len(dismissed) == 1


async def test_extracted_project_spawn_cancel_confirm_and_keep_pending() -> None:
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    canceled: list[str] = []

    async def cancel_project_spawn_request(spawn_request_id: str) -> None:
        canceled.append(spawn_request_id)

    app.cancel_project_spawn_request = cancel_project_spawn_request  # type: ignore[method-assign]
    request = {
        "spawn_request_id": "spawn-7",
        "project_name": "workerbee",
        "target_path": "/tmp/workerbee",
    }
    async with app.run_test() as pilot:
        confirm_screen = screens.OperatorProjectSpawnCancelScreen(request)
        app.push_screen(confirm_screen)
        await pilot.pause()

        summary = str(
            confirm_screen.query_one("#operator-kill-message", Static).renderable
        )
        assert "Request: spawn-7" in summary
        assert "Project: workerbee" in summary
        assert "Target: /tmp/workerbee" in summary
        await pilot.click("#project-spawn-cancel-confirm")
        await pilot.pause()

        assert app.screen is not confirm_screen
        assert canceled == ["spawn-7"]

        keep_screen = screens.OperatorProjectSpawnCancelScreen(request)
        app.push_screen(keep_screen)
        await pilot.pause()
        await pilot.click("#project-spawn-cancel-close")
        await pilot.pause()

        assert app.screen is not keep_screen
        assert canceled == ["spawn-7"]


async def test_extracted_operator_history_preserves_rows_refresh_and_resume() -> None:
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    candidates = [
        tui.OperatorSessionCandidate(
            session_id="new-session",
            timestamp=3600.0,
            source="codex.sessions",
            summary="new prompt",
        ),
        tui.OperatorSessionCandidate(
            session_id="old-session",
            timestamp=0.0,
            source="metadata.operator_session_history",
            summary="old prompt",
        ),
    ]
    refreshed: list[object] = []
    resumed: list[tuple[str, str]] = []

    async def refresh_operator_history_screen(screen: object) -> None:
        refreshed.append(screen)

    async def resume_selected_operator(
        *,
        agent_id: str,
        resume_candidate: tui.OperatorSessionCandidate,
    ) -> None:
        resumed.append((agent_id, resume_candidate.session_id))

    app.refresh_operator_history_screen = refresh_operator_history_screen  # type: ignore[method-assign]
    app.resume_selected_operator = resume_selected_operator  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        screen = screens.OperatorHistoryScreen(
            agent_id="operator-7",
            candidates=candidates,
            resume_target=candidates[1],
        )
        app.push_screen(screen)
        await pilot.pause()

        table = screen.query_one("#operator-history-table", DataTable)
        assert table.columns[list(table.columns)[0]].label.plain == "Pick"
        assert table.get_row("new-session") == [
            "1",
            "1970-01-01 01:00",
            "codex.sessions",
            "new-session",
            "new prompt",
        ]
        assert table.get_row("old-session") == [
            "*",
            "-",
            "metadata.operator_session_history",
            "old-session",
            "old prompt",
        ]
        assert table.cursor_row == 1

        await pilot.click("#operator-history-refresh")
        await pilot.pause()
        assert refreshed == [screen]

        table.move_cursor(row=0, animate=False, scroll=False)
        await pilot.click("#operator-history-resume")
        await pilot.pause()

        assert app.screen is not screen
        assert resumed == [("operator-7", "new-session")]


async def test_extracted_operator_authority_preserves_payloads_and_hooks() -> None:
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    calls: list[tuple[str, object]] = []

    async def preview(screen: object) -> None:
        calls.append(("preview", screen))

    async def save(screen: object) -> None:
        calls.append(("save", screen))

    async def apply(screen: object) -> None:
        calls.append(("apply", screen))

    app.preview_operator_workspace_set = preview  # type: ignore[method-assign]
    app.save_operator_workspace_set = save  # type: ignore[method-assign]
    app.apply_operator_authority = apply  # type: ignore[method-assign]
    authority_view = {
        "admin": {
            "control_mode": "editable",
            "profile_ceiling": "full-access",
        },
        "requested_profile": "scoped-edit",
        "effective_profile": "scoped-edit",
        "profile_label": "Rosie Workbench",
        "pending_activation": True,
        "workspace_set": {
            "set_id": "rosie-stackops",
            "label": "Rosie StackOps",
            "member_count": 1,
            "members": [{"path": "/home/test/git/agent-pbx"}],
            "collections": ["/home/test/git/k1s-wt"],
        },
    }
    workspace_sets = [
        {"set_id": "rosie-stackops", "label": "Rosie StackOps", "version": 3}
    ]

    async with app.run_test() as pilot:
        screen = screens.OperatorAuthorityScreen(
            agent_id="operator-0",
            authority_view=authority_view,
            workspace_sets=workspace_sets,
        )
        app.push_screen(screen)
        await pilot.pause()

        assert screen.query_one("#operator-authority-profile", Select).value == (
            "scoped-edit"
        )
        assert screen.query_one("#operator-authority-set", Select).value == (
            "rosie-stackops"
        )
        assert "pending safe restart/resume" in str(
            screen.query_one("#operator-authority-summary", Static).renderable
        )

        screen.query_one(
            "#operator-authority-profile", Select
        ).value = "trusted-workbench"
        screen.query_one("#operator-authority-label", Input).value = "Trusted Rosie"
        screen.query_one("#operator-workspace-set-id", Input).value = "trusted-set"
        screen.query_one("#operator-workspace-set-label", Input).value = "Trusted Set"
        screen.query_one(
            "#operator-workspace-set-members", Input
        ).value = "/repo/one, /repo/two"
        screen.query_one(
            "#operator-workspace-set-collections", Input
        ).value = "/collection/a, /collection/b"

        assert screen.assignment_payload() == {
            "profile": "trusted-workbench",
            "workspace_set_id": "rosie-stackops",
            "profile_label": "Trusted Rosie",
        }
        assert screen.workspace_set_payload() == (
            "trusted-set",
            {
                "label": "Trusted Set",
                "members": ["/repo/one", "/repo/two"],
                "collections": ["/collection/a", "/collection/b"],
                "collection_depth": 1,
            },
        )

        screen.query_one("#operator-authority-preview", Button).press()
        await pilot.pause()
        screen.query_one("#operator-authority-save-set", Button).press()
        await pilot.pause()
        screen.query_one("#operator-authority-apply", Button).press()
        await pilot.pause()

        assert calls == [("preview", screen), ("save", screen), ("apply", screen)]

        screen.show_preview(
            {
                "label": "Trusted Set",
                "member_count": 2,
                "member_digest": "abcdef0123456789",
            }
        )
        assert (
            str(screen.query_one("#operator-authority-summary", Static).renderable)
            == "Preview: Trusted Set · members=2 · digest=abcdef012345"
        )


async def test_extracted_workerbee_switch_preserves_confirmation_and_worker_hook() -> (
    None
):
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    transaction = {
        "transaction_id": "a" * 24,
        "target": "platform_debug",
        "before": {"backend": "oss", "generation": 4, "instance_id": "oss-instance"},
        "target_runtime": {
            "backend": "platform_debug",
            "generation": 5,
            "instance_id": "debug-instance",
            "source_mode": "snapshot-import",
            "fingerprint": "b" * 64,
        },
    }
    workers: list[tuple[object, str, bool]] = []

    app.apply_workerbee_switch = (  # type: ignore[method-assign]
        lambda received, *, confirmation: ("apply", received, confirmation)
    )
    async with app.run_test() as pilot:
        screen = screens.WorkerBeeSwitchConfirmScreen(transaction=transaction)
        app.push_screen(screen)
        await pilot.pause()

        confirmation = screen.query_one("#workerbee-switch-confirm", Input)
        assert screen.BINDINGS == [("escape", "dismiss", "Close")]
        assert screen.focused is confirmation
        assert confirmation.placeholder == "switch platform_debug"
        assert (
            str(screen.query_one("#workerbee-switch-title", Static).renderable)
            == "Switch WorkerBee Backend"
        )
        assert str(
            screen.query_one("#workerbee-switch-summary", Static).renderable
        ) == (
            f"Transaction: {'a' * 24}\n"
            "Current: oss g4 instance oss-instance\n"
            "Target: platform_debug g5 instance debug-instance · snapshot-import\n"
            f"Target fingerprint: {'b' * 64}\n"
            "Existing leases for an older generation require rebind.\n"
            'Type "switch platform_debug" to confirm.'
        )
        assert (
            screen.query_one("#workerbee-switch-run", Button).label.plain
            == "Apply Switch"
        )

        original_run_worker = app.run_worker
        app.run_worker = (  # type: ignore[method-assign]
            lambda work, *, name, exclusive: workers.append((work, name, exclusive))
        )
        confirmation.value = "switch platform_debug"
        await pilot.press("enter")
        await pilot.pause()
        app.run_worker = original_run_worker  # type: ignore[method-assign]

        assert app.screen is not screen
        assert workers == [
            (
                ("apply", transaction, "switch platform_debug"),
                "workerbee-switch-apply",
                True,
            )
        ]

        rollback_screen = screens.WorkerBeeSwitchConfirmScreen(
            transaction=transaction,
            action="rollback",
        )
        app.push_screen(rollback_screen)
        await pilot.pause()
        assert rollback_screen.expected_confirmation() == f"rollback {'a' * 24}"
        assert (
            str(rollback_screen.query_one("#workerbee-switch-title", Static).renderable)
            == "Rollback WorkerBee Backend"
        )
        assert (
            rollback_screen.query_one("#workerbee-switch-run", Button).label.plain
            == "Rollback"
        )
        await pilot.click("#workerbee-switch-cancel")
        await pilot.pause()

        assert app.screen is not rollback_screen
        assert len(workers) == 1


async def test_extracted_workerbee_workspace_preserves_payloads_and_app_hooks() -> None:
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    policy = {
        "effective": {
            "policy": "scoped-workbench",
            "request_mode": "automatic",
            "allowed_roots": ["/repo/one", "/repo/two"],
            "managed_scratch_root": "/scratch",
            "profile_label": "Rosie Workbench",
        },
        "admin": {
            "control_mode": "editable",
            "policy_ceiling": "scoped-workbench",
        },
    }
    transitions = [
        {
            "status": "applied",
            "runtime_project": "demo",
            "working_subpath": "service",
        }
    ]
    workers: list[tuple[object, str, bool]] = []

    app.save_workerbee_workspace_policy = (  # type: ignore[method-assign]
        lambda received, global_policy=False: ("save", received, global_policy)
    )
    app.inherit_workerbee_workspace_policy = (  # type: ignore[method-assign]
        lambda received: ("inherit", received)
    )
    app.preview_workerbee_workspace_target = (  # type: ignore[method-assign]
        lambda received: ("preview", received)
    )
    app.apply_workerbee_workspace_target = (  # type: ignore[method-assign]
        lambda received: ("bind", received)
    )
    app.approve_workerbee_workspace_transition = (  # type: ignore[method-assign]
        lambda received: ("approve", received)
    )
    app.deny_workerbee_workspace_transition = (  # type: ignore[method-assign]
        lambda received: ("deny", received)
    )
    app.revert_workerbee_workspace_transition = (  # type: ignore[method-assign]
        lambda received: ("revert", received)
    )

    async with app.run_test() as pilot:
        screen = screens.WorkerBeeWorkspaceScreen(
            agent_id="agent-1",
            policy_view=policy,
            transitions=transitions,
        )
        app.push_screen(screen)
        await pilot.pause()

        assert screen.BINDINGS == [("escape", "dismiss", "Close")]
        assert screen.focused is screen.query_one("#workerbee-workspace-policy", Select)
        assert screen.query_one("#workerbee-workspace-policy", Select).value == (
            "scoped-workbench"
        )
        assert (
            screen.query_one("#workerbee-workspace-request-mode", Select).value
            == "automatic"
        )
        assert screen.query_one("#workerbee-workspace-roots", Input).value == (
            "/repo/one, /repo/two"
        )
        assert (
            str(screen.query_one("#workerbee-workspace-summary", Static).renderable)
            == "Latest: applied · demo · service"
        )
        assert screen.query_one("#workerbee-workspace-bind", Button).disabled is True

        screen.query_one(
            "#workerbee-workspace-roots", Input
        ).value = " /repo/three, , /repo/four "
        screen.query_one("#workerbee-workspace-scratch", Input).value = "  "
        screen.query_one("#workerbee-workspace-label", Input).value = "New profile"
        screen.query_one("#workerbee-workspace-target", Input).value = " /repo/three "
        screen.query_one("#workerbee-workspace-scenario", Input).value = " demo-next "

        assert screen.policy_payload() == {
            "policy": "scoped-workbench",
            "request_mode": "automatic",
            "allowed_roots": ["/repo/three", "/repo/four"],
            "managed_scratch_root": None,
            "profile_label": "New profile",
        }
        assert screen.target_payload() == {
            "cwd": "/repo/three",
            "project": "demo-next",
        }

        preview = {
            "target": {
                "source_root": "/repo/three",
                "working_subpath": "service",
                "runtime_project": "demo-next",
                "request_mode": "automatic",
            }
        }
        screen.show_preview(preview)
        assert screen.preview_payload == {
            "request": {"cwd": "/repo/three", "project": "demo-next"},
            "preview": preview,
        }
        assert (
            str(screen.query_one("#workerbee-workspace-summary", Static).renderable)
            == "Preview: /repo/three · service · demo-next · automatic"
        )
        assert screen.query_one("#workerbee-workspace-bind", Button).disabled is False

        original_run_worker = app.run_worker
        app.run_worker = (  # type: ignore[method-assign]
            lambda work, *, name, exclusive: workers.append((work, name, exclusive))
        )
        for button_id in (
            "workerbee-workspace-save",
            "workerbee-workspace-save-global",
            "workerbee-workspace-inherit",
            "workerbee-workspace-preview",
            "workerbee-workspace-bind",
            "workerbee-workspace-approve",
            "workerbee-workspace-deny",
            "workerbee-workspace-revert",
        ):
            screen.query_one(f"#{button_id}", Button).press()
            await pilot.pause()
        app.run_worker = original_run_worker  # type: ignore[method-assign]

        assert workers == [
            (("save", screen, False), "workerbee-workspace-policy-save", True),
            (("save", screen, True), "workerbee-workspace-global-policy-save", True),
            (("inherit", screen), "workerbee-workspace-policy-inherit", True),
            (("preview", screen), "workerbee-workspace-preview", True),
            (("bind", screen), "workerbee-workspace-bind", True),
            (("approve", screen), "workerbee-workspace-approve", True),
            (("deny", screen), "workerbee-workspace-deny", True),
            (("revert", screen), "workerbee-workspace-revert", True),
        ]

        screen.query_one("#workerbee-workspace-close", Button).press()
        await pilot.pause()
        assert app.screen is not screen


async def test_extracted_workerbee_snapshot_screen_preserves_payload_preview_and_hooks() -> (
    None
):
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    policy = {
        "applicable": True,
        "backend": "platform_debug",
        "workflow_label": "Trusted Platform Workbench",
        "effective": {
            "source_root": "/repo/demo",
            "policy": "safe-working-tree",
            "include_paths": ["site-output", "reports"],
            "max_bytes": 1024,
            "max_files": 20,
            "max_file_bytes": 512,
            "profile_label": "Project output",
        },
        "admin": {
            "control_mode": "editable",
            "policy_ceiling": "safe-working-tree",
        },
    }
    workers: list[tuple[object, str, bool]] = []

    app.save_workerbee_snapshot_policy = (  # type: ignore[method-assign]
        lambda received, global_policy=False: ("save", received, global_policy)
    )
    app.inherit_workerbee_snapshot_policy = (  # type: ignore[method-assign]
        lambda received: ("inherit", received)
    )
    app.preview_workerbee_snapshot_policy = (  # type: ignore[method-assign]
        lambda received: ("preview", received)
    )

    async with app.run_test() as pilot:
        screen = screens.WorkerBeeSnapshotScreen(agent_id="agent-1", policy_view=policy)
        app.push_screen(screen)
        await pilot.pause()

        policy_select = screen.query_one("#workerbee-snapshot-policy", Select)
        assert screen.BINDINGS == [("escape", "dismiss", "Close")]
        assert screen.focused is policy_select
        assert policy_select.value == "safe-working-tree"
        assert policy_select._options == [  # type: ignore[attr-defined]
            ("", Select.BLANK),
            ("Tracked content", "tracked"),
            ("Tracked + approved generated paths", "approved-paths"),
            ("Safe working tree", "safe-working-tree"),
        ]
        assert (
            str(screen.query_one("#workerbee-snapshot-posture", Static).renderable)
            == "Trusted Platform Workbench · backend=platform_debug · "
            "control=editable · ceiling=safe-working-tree"
        )
        assert (
            str(screen.query_one("#workerbee-snapshot-summary", Static).renderable)
            == "Preview resolves generated roots without changing the active lease or workspace."
        )
        assert screen.query_one("#workerbee-snapshot-save", Button).label.plain == (
            "Save Agent"
        )
        assert (
            screen.query_one("#workerbee-snapshot-save-global", Button).label.plain
            == "Save Root Default"
        )

        screen.query_one(
            "#workerbee-snapshot-includes", Input
        ).value = " site-output, , reports, coverage "
        screen.query_one("#workerbee-snapshot-max-bytes", Input).value = " 2048 "
        screen.query_one("#workerbee-snapshot-max-files", Input).value = ""
        screen.query_one("#workerbee-snapshot-max-file-bytes", Input).value = "768"
        screen.query_one("#workerbee-snapshot-label", Input).value = "  "
        assert screen.policy_payload() == {
            "source_root": "/repo/demo",
            "policy": "safe-working-tree",
            "include_paths": ["site-output", "reports", "coverage"],
            "max_bytes": 2048,
            "max_files": None,
            "max_file_bytes": 768,
            "profile_label": None,
        }

        screen.show_preview(
            {
                "selection": {
                    "policy": "safe-working-tree",
                    "file_count": 4,
                    "total_bytes": 120,
                    "include_paths": ["site-output", "reports"],
                    "excluded_paths": [".env (sensitive-name)"],
                }
            }
        )
        assert (
            str(screen.query_one("#workerbee-snapshot-summary", Static).renderable)
            == "Preview: safe-working-tree · 4 files · 120 bytes · "
            "2 generated roots · 1 excluded"
        )

        original_run_worker = app.run_worker
        app.run_worker = (  # type: ignore[method-assign]
            lambda work, *, name, exclusive: workers.append((work, name, exclusive))
        )
        for button_id in (
            "workerbee-snapshot-save",
            "workerbee-snapshot-save-global",
            "workerbee-snapshot-inherit",
            "workerbee-snapshot-preview",
        ):
            screen.query_one(f"#{button_id}", Button).press()
            await pilot.pause()
        app.run_worker = original_run_worker  # type: ignore[method-assign]

        assert workers == [
            (("save", screen, False), "workerbee-snapshot-save", True),
            (("save", screen, True), "workerbee-snapshot-save-global", True),
            (("inherit", screen), "workerbee-snapshot-inherit", True),
            (("preview", screen), "workerbee-snapshot-preview", True),
        ]

        screen.query_one("#workerbee-snapshot-close", Button).press()
        await pilot.pause()
        assert app.screen is not screen

        direct_screen = screens.WorkerBeeSnapshotScreen(
            agent_id="agent-1",
            policy_view={
                **policy,
                "applicable": False,
                "backend": "oss",
            },
        )
        app.push_screen(direct_screen)
        await pilot.pause()
        assert (
            str(
                direct_screen.query_one(
                    "#workerbee-snapshot-direct-host", Static
                ).renderable
            )
            == "The selected OSS backend uses the host checkout directly; no appliance snapshot is created."
        )
        assert (
            direct_screen.query_one("#workerbee-snapshot-policy", Select).disabled
            is True
        )
        assert (
            direct_screen.query_one("#workerbee-snapshot-preview", Button).disabled
            is True
        )
        direct_screen.query_one("#workerbee-snapshot-close", Button).press()
        await pilot.pause()


async def test_extracted_workerbee_access_preserves_choices_payload_and_hooks() -> None:
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    view = {
        "selected_backend": "platform_debug",
        "effective_profile": "local-deploy",
        "control_mode": "editable",
        "profile_ceiling": "trusted-workbench",
        "trusted_workbench_label": "Rosie Workbench",
        "source_mode": "snapshot-import",
        "source_mode_control": "editable",
        "source_mode_ceiling": [
            "native-direct",
            "snapshot-import",
            "managed-clone",
        ],
        "execution_lane": "interactive-gateway",
        "execution_lane_control": "read_only",
        "review_readonly": False,
        "tool_surface_contract": {
            "required_tools": ["workerbee_v1_session_start", "workerbee_v1_status"],
            "reviewed_optional_tools": ["workerbee_v1_logs"],
        },
    }
    workers: list[tuple[object, str, bool]] = []

    app.save_workerbee_access_profile = (  # type: ignore[method-assign]
        lambda received: ("save", received)
    )
    async with app.run_test() as pilot:
        screen = screens.WorkerBeeAccessScreen(agent_id="agent-1", access_view=view)
        app.push_screen(screen)
        await pilot.pause()

        access = screen.query_one("#workerbee-access-profile", Select)
        source = screen.query_one("#workerbee-source-mode", Select)
        lane = screen.query_one("#workerbee-execution-lane", Select)
        assert screen.BINDINGS == [("escape", "dismiss", "Close")]
        assert screen.focused is access
        assert access.value == "local-deploy"
        assert access._options == [  # type: ignore[attr-defined]
            ("", Select.BLANK),
            ("Inspect runtime state", "observe-local"),
            ("Build and deploy in this workspace", "local-deploy"),
            ("Rosie Workbench", "trusted-workbench"),
        ]
        assert source._options == [  # type: ignore[attr-defined]
            ("", Select.BLANK),
            ("Native direct checkout", "native-direct"),
            ("Content-addressed snapshot import", "snapshot-import"),
            ("Managed appliance clone", "managed-clone"),
        ]
        assert lane.disabled is True
        assert lane._options == [  # type: ignore[attr-defined]
            ("", Select.BLANK),
            ("Ordinary generation-bound gateway", "interactive-gateway"),
            ("Controlled run (root-owned approval)", "controlled-run"),
        ]
        assert (
            str(screen.query_one("#workerbee-tool-surface-summary", Static).renderable)
            == "requested=2 · backend optional=1 · unknown=quarantined/denied · "
            "effective catalog: run doctor"
        )
        assert (
            str(screen.query_one("#workerbee-access-summary", Static).renderable)
            == "Access and source changes apply after a safe Agent/Operator restart or resume. "
            "Controlled runs require separate root approval."
        )

        access.value = "trusted-workbench"
        source.value = "managed-clone"
        await pilot.pause()
        assert screen.selected_profile() == "trusted-workbench"
        assert screen.policy_payload() == {
            "access_profile": "trusted-workbench",
            "source_mode": "managed-clone",
        }

        original_run_worker = app.run_worker
        app.run_worker = (  # type: ignore[method-assign]
            lambda work, *, name, exclusive: workers.append((work, name, exclusive))
        )
        screen.query_one("#workerbee-access-save", Button).press()
        await pilot.pause()
        app.run_worker = original_run_worker  # type: ignore[method-assign]
        assert workers == [(("save", screen), "workerbee-access-save", True)]

        screen.query_one("#workerbee-access-close", Button).press()
        await pilot.pause()
        assert app.screen is not screen
