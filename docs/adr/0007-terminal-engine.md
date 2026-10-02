# 0007 — Embedded terminal engine

Status: accepted for Agent PBX v2

Agent PBX keeps Textual below 1.0 for the v2 cycle and uses Pyte 0.8 as the
virtual-terminal parser behind an internal `PbxTerminalSurface`. Pyte is mature,
small, and compatible with the current Textual generation. Agent PBX owns the
PTY, subprocess, resize, keyboard, paste, mouse policy, and rendering layers.

`textual-terminal` is retained as a behavior reference; its unresolved focus,
Alt-key, decode, and descriptor cleanup paths make direct adoption risky.
`textual-tty` is also a protocol reference, but its pre-alpha status, Textual 8
requirement, and license require a separate future decision.

The internal adapter prevents Pyte-specific objects from entering daemon, store,
or controller contracts. If conformance testing later requires a different VT
engine, only the terminal package and widget adapter should change.

