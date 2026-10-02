# 0006 — Legacy polling and terminal compatibility

Status: accepted for Agent PBX v2

The capture-based terminal proxy, Thread surface, HTTP refresh endpoints, and
polling mode remain available through v2.0. V2 services launch behind feature
gates and degrade independently. A failed embedded terminal must not terminate
Codex; a failed structured-state source must not make the runtime unusable.

Removal requires measured feature parity, a migration path, published criteria,
and a release after v2.0. Calendar age alone is not sufficient.

