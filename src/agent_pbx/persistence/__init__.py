"""SQLite persistence infrastructure behind the public Store facade."""

from .connection import SQLiteConnectionFactory
from .repositories import (
    AgentRepository,
    DurableHelperRepository,
    EventRepository,
    FileTrashRepository,
    JoplinSessionRepository,
    JoplinSyncRepository,
    ModelElevationRepository,
    RuntimeRepository,
    TerminalEditorRepository,
)
from .transactions import begin_immediate

__all__ = [
    "AgentRepository",
    "DurableHelperRepository",
    "EventRepository",
    "FileTrashRepository",
    "JoplinSessionRepository",
    "JoplinSyncRepository",
    "ModelElevationRepository",
    "RuntimeRepository",
    "TerminalEditorRepository",
    "SQLiteConnectionFactory",
    "begin_immediate",
]
