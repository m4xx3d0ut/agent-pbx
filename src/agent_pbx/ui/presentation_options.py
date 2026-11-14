"""Cycle-free presentation options shared by the TUI and modal screens."""

from __future__ import annotations


OPERATOR_HANDOFF_CANCEL_REASON_CHOICES = (
    ("Superseded by structured handoff", "superseded"),
    ("No longer needed", "no_longer_needed"),
    ("Duplicate request", "duplicate"),
    ("Test or UAT artifact", "test_or_uat_artifact"),
    ("Created in error", "created_in_error"),
    ("Other", "other"),
)


__all__ = ["OPERATOR_HANDOFF_CANCEL_REASON_CHOICES"]
