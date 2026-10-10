# Models, Profiles, and Codex

> **Applies to:** Agent PBX 2.2.x
> **Last reviewed:** 2026-10-10

Agent PBX validates model choices against the live `codex debug models`
catalog. A configured slug that is missing from the current CLI produces a
warning and is not silently replaced. The Codex posture surface distinguishes
the shell CLI, app-server, and latest stable versions so version skew is
visible before UAT.

## Profile policy

Profiles separate root model, reasoning effort, output verbosity, reasoning
summary, terminal behavior, skills, and tool policy. The current engineering
defaults use Sol 5.6 with high reasoning for Agents and xhigh reasoning for
Operators, high output verbosity, and detailed reasoning summaries. Terra and
other catalog entries remain deliberate alternatives rather than automatic
upgrades.

Model selectors are catalog-driven. Running-session changes use a verified
restart/resume because model and reasoning fields are session-static. The UI
must not present them as an in-turn hot reload.

An elevated Sol max profile requires a human-approved justification and a
bounded lease. The Operator requesting it cannot self-approve. Failure restores
the prior profile and leaves an audit record.

## Managed global configuration

The Codex tab and `/codex config` edit a reviewed set of supported scalar
settings. Agent PBX uses atomic backup/restore and preserves unrelated values.
Authentication credentials, provider secrets, model provider tables, telemetry
keys, arbitrary nested tables, and administrator-enforced requirements remain
hidden or read-only.

Opaque secret fields can be replaced without revealing the stored value. The
UI displays state such as present, absent, or managed and never reads a secret
back into the editor after save.

## Themes and terminal mode

Install or inspect the generated Codex syntax theme with:

```bash
agent-pbx codex theme status
agent-pbx codex theme install
```

The PBX theme controls the outer semantic state; the Codex theme controls code
and diff syntax supported by the installed CLI. Managed profiles keep tmux as
scrollback owner. `/codex terminal scrollback`, `/codex terminal raw`, and
`/codex terminal default` expose the supported posture from the TUI.

## Skills and personality

The committed personality is a generic Roses Architect profile: direct,
professional, technically precise, and architect-first. Host-specific identity
or relationship text belongs in a private local overlay. The TUI may show that
an overlay is active and its hash/version, but shared diagnostics and project
files must not expose its text.

Managed skill packs use manifests, hashes, preview/apply/rollback, drift
detection, and context-budget checks. Project-authored files outside managed
markers are not overwritten.

