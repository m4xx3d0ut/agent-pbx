from ._shared import slugify
from .codex import ModelElevationScreen
from .custom_commands import CustomSlashCommandArgScreen
from .files import (
    EditorCloseConfirmScreen,
    FileDeleteConfirmScreen,
    FilePathActionScreen,
    FileTrashScreen,
)
from .git_commands import GitDiffScreen, GitPushScreen
from .git_broker import (
    LinkedGitCommitConfirmScreen,
    LinkedGitCommitPathsScreen,
    LinkedGitRecoveryConfirmScreen,
    LinkedGitReviewArtifactScreen,
)
from .github import IssueClearConfirmScreen, PullRequestMergeConfirmScreen
from .joplin import (
    JoplinDeleteConfirmScreen,
    JoplinNoteTitleScreen,
    JoplinPasteConfirmScreen,
    JoplinReferencePromptScreen,
)
from .operators import (
    OperatorAuthorityScreen,
    OperatorHistoryScreen,
    OperatorKillConfirmScreen,
    OperatorProjectSpawnCancelScreen,
)
from .runtime import ManagedProjectPickerScreen, RuntimeMigrationScreen
from .settings import SettingsScreen


__all__ = [
    "CustomSlashCommandArgScreen",
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
    "OperatorHistoryScreen",
    "OperatorKillConfirmScreen",
    "OperatorProjectSpawnCancelScreen",
    "PullRequestMergeConfirmScreen",
    "RuntimeMigrationScreen",
    "SettingsScreen",
    "slugify",
]
