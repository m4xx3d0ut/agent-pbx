"""SQLite persistence infrastructure behind the public Store facade."""

from .connection import SQLiteConnectionFactory
from .transactions import begin_immediate

__all__ = ["SQLiteConnectionFactory", "begin_immediate"]
