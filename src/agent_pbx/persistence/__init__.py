"""SQLite persistence infrastructure behind the public Store facade."""

from .connection import SQLiteConnectionFactory
from .schema import initialize_schema
from .repositories import (
    AgentRepository,
    DurableHelperRepository,
    DynamicResourceRepository,
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
    "DynamicResourceRepository",
    "EventRepository",
    "FileTrashRepository",
    "JoplinSessionRepository",
    "JoplinSyncRepository",
    "ModelElevationRepository",
    "RuntimeRepository",
    "TerminalEditorRepository",
    "SQLiteConnectionFactory",
    "begin_immediate",
    "initialize_schema",
]
