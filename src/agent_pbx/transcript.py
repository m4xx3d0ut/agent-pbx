from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class TranscriptRecorder:
    def __init__(self, path: Path | str | None, *, actor: str) -> None:
        self.path = Path(path) if path else None
        self.actor = actor

    def record(self, action: str, **data: Any) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "ts": time.time(),
            "actor": self.actor,
            "action": action,
            "data": data,
        }
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, sort_keys=True) + "\n")


def emit(message: str, transcript: TranscriptRecorder, action: str, **data: Any) -> None:
    print(message, flush=True)
    transcript.record(action, message=message, **data)
