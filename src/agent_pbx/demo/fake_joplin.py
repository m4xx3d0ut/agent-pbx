from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import time
from typing import Any
from urllib.parse import urlparse
import uuid


FOLDERS = [
    {"id": "root", "parent_id": "", "title": "Agent PBX"},
    {"id": "project", "parent_id": "root", "title": "platform-console"},
    {"id": "agent", "parent_id": "project", "title": "platform-agent"},
]
NOTES: dict[str, dict[str, Any]] = {
    "architecture": {
        "id": "architecture",
        "parent_id": "project",
        "title": "Runtime Architecture",
        "body": (
            "# Runtime Architecture\n\n"
            "Agent PBX owns durable identity, policy, lifecycle, and audit.\n\n"
            "```mermaid\nflowchart LR\n  PBX --> Codex\n  PBX --> WorkerBee\n```"
        ),
        "created_time": 1791806400000,
        "updated_time": 1791807300000,
    },
    "release-checklist": {
        "id": "release-checklist",
        "parent_id": "project",
        "title": "Release Checklist",
        "body": (
            "# Release Checklist\n\n"
            "- [x] Run tests\n- [x] Verify native tmux\n"
            "- [ ] Review the public product tour"
        ),
        "created_time": 1791806400000,
        "updated_time": 1791807000000,
    },
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    try:
        size = int(handler.headers.get("Content-Length", "0"))
    except ValueError:
        size = 0
    if not size:
        return {}
    value = json.loads(handler.rfile.read(size).decode("utf-8"))
    return value if isinstance(value, dict) else {}


class JoplinFixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        return

    def send_json(self, value: object, status: int = 200) -> None:
        data = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/ping":
            self.send_json({"status": "ok"})
        elif path == "/folders":
            self.send_json({"items": FOLDERS, "has_more": False})
        elif path == "/folders/project/notes":
            self.send_json({"items": list(NOTES.values()), "has_more": False})
        elif path == "/folders/agent/notes":
            self.send_json({"items": [], "has_more": False})
        elif path.startswith("/notes/"):
            note = NOTES.get(path.rsplit("/", 1)[-1])
            self.send_json(note or {"error": "not found"}, 200 if note else 404)
        else:
            self.send_json({"items": [], "has_more": False})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        value = _read_json(self)
        if path == "/folders":
            folder = {
                "id": uuid.uuid4().hex,
                "parent_id": str(value.get("parent_id") or "root"),
                "title": str(value.get("title") or "Demo Notes"),
            }
            FOLDERS.append(folder)
            self.send_json(folder)
            return
        if path == "/notes":
            note_id = uuid.uuid4().hex
            note = {
                "id": note_id,
                "parent_id": str(value.get("parent_id") or "project"),
                "title": str(value.get("title") or "Demo Note"),
                "body": str(value.get("body") or ""),
                "created_time": _now_ms(),
                "updated_time": _now_ms(),
            }
            NOTES[note_id] = note
            self.send_json(note)
            return
        self.send_json({})

    def do_PUT(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/notes/"):
            note_id = path.rsplit("/", 1)[-1]
            note = NOTES.get(note_id)
            if note is None:
                self.send_json({"error": "not found"}, 404)
                return
            note.update(_read_json(self))
            note["updated_time"] = _now_ms()
            self.send_json(note)
            return
        self.send_json({})

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/notes/"):
            NOTES.pop(path.rsplit("/", 1)[-1], None)
        self.send_json({})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args(argv)
    ThreadingHTTPServer((args.host, args.port), JoplinFixtureHandler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
