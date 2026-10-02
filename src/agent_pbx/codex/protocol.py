from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any


@dataclass(frozen=True)
class CodexProtocolMessage:
    method: str
    params: dict[str, Any]
    message_id: str | int | None = None


def parse_protocol_message(raw: str | bytes) -> CodexProtocolMessage | None:
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    method = payload.get("method")
    if not isinstance(method, str) or not method:
        return None
    params = payload.get("params")
    return CodexProtocolMessage(
        method=method,
        params=params if isinstance(params, dict) else {},
        message_id=payload.get("id"),
    )

