"""PTY, virtual-terminal, and input adapters for the embedded tmux client."""

from .keys import FunctionKeyPassthroughMap, function_key_sequence
from .pty import PtyProcess
from .screen import TerminalSnapshot, VirtualTerminal

__all__ = [
    "FunctionKeyPassthroughMap",
    "PtyProcess",
    "TerminalSnapshot",
    "VirtualTerminal",
    "function_key_sequence",
]

