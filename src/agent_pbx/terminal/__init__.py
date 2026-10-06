"""PTY, virtual-terminal, and input adapters for the embedded tmux client."""

from .keys import FunctionKeyPassthroughMap, function_key_sequence
from .widget import (
    EMBEDDED_SCROLL_MODE_CHILD,
    EMBEDDED_SCROLL_MODE_TMUX,
    PbxTerminalSurface,
    TerminalScrollRequested,
    normalize_embedded_scroll_mode,
)
from .pty import PtyProcess
from .screen import TerminalSnapshot, VirtualTerminal

__all__ = [
    "FunctionKeyPassthroughMap",
    "EMBEDDED_SCROLL_MODE_CHILD",
    "EMBEDDED_SCROLL_MODE_TMUX",
    "PbxTerminalSurface",
    "PtyProcess",
    "TerminalSnapshot",
    "VirtualTerminal",
    "function_key_sequence",
    "normalize_embedded_scroll_mode",
    "TerminalScrollRequested",
]
