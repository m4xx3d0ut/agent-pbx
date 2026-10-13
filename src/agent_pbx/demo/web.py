from __future__ import annotations

import asyncio
from contextlib import suppress
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any

from agent_pbx import __version__

from .contract import DEMO_SCENARIO_ID, public_demo_contract
from .policy import (
    build_public_demo_environment,
    ensure_private_directory,
    replaced_environment,
    validate_web_binding,
)


class DemoWebDependencyError(RuntimeError):
    """Raised when the optional browser-demo dependency is unavailable."""


def public_demo_doctor(*, tmux_bin: str = "tmux") -> dict[str, Any]:
    checks = {
        "git": shutil.which("git"),
        "tmux": shutil.which(tmux_bin),
        "textual_serve": importlib.util.find_spec("textual_serve") is not None,
    }
    failures = [name for name, value in checks.items() if not value]
    return {
        "ok": not failures,
        "api_version": "agent-pbx.public-demo-doctor/v1",
        "agent_pbx_version": __version__,
        "scenario": DEMO_SCENARIO_ID,
        "checks": checks,
        "failures": failures,
        "remediation": (
            None
            if not failures
            else "Install agent-pbx[web-demo] plus the host git and tmux packages."
        ),
    }


def render_public_demo_doctor(report: dict[str, Any]) -> str:
    lines = ["# Agent PBX public demo doctor", ""]
    checks = report.get("checks") or {}
    for name in ("textual_serve", "tmux", "git"):
        value = checks.get(name)
        lines.append(f"- {'PASS' if value else 'FAIL'} `{name}`: {value or 'unavailable'}")
    if remediation := report.get("remediation"):
        lines.extend(["", f"Remediation: {remediation}"])
    return "\n".join(lines)


def _load_textual_serve() -> tuple[type[Any], type[Any], Any, Any]:
    try:
        from aiohttp import WSMsgType, web
        from textual_serve.app_service import AppService
        from textual_serve.server import Server, to_int
    except ImportError as exc:  # pragma: no cover - exercised without the extra
        raise DemoWebDependencyError(
            "browser demo support is unavailable; install agent-pbx[web-demo]"
        ) from exc
    return Server, AppService, web, (WSMsgType, to_int)


def build_public_demo_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    public_url: str | None = None,
    allowed_origins: tuple[str, ...] = (),
    allow_public_bind: bool = False,
    max_sessions: int = 8,
    session_ttl_seconds: int = 900,
    tmux_bin: str = "tmux",
) -> tuple[Any, Path]:
    """Build a hardened Textual Serve instance and its private server root."""

    if max_sessions < 1 or max_sessions > 64:
        raise ValueError("max sessions must be between 1 and 64")
    if session_ttl_seconds < 30 or session_ttl_seconds > 3600:
        raise ValueError("session TTL must be between 30 and 3600 seconds")
    if port < 1 or port > 65535:
        raise ValueError("port must be between 1 and 65535")
    effective_public_url = public_url
    if effective_public_url is None and host in {"127.0.0.1", "localhost", "::1"}:
        display_host = "127.0.0.1" if host == "::1" else host
        effective_public_url = f"http://{display_host}:{port}"
    effective_public_url, effective_origins = validate_web_binding(
        host=host,
        public_url=effective_public_url,
        allowed_origins=allowed_origins,
        allow_public_bind=allow_public_bind,
    )
    doctor = public_demo_doctor(tmux_bin=tmux_bin)
    if not doctor["ok"]:
        raise DemoWebDependencyError(str(doctor["remediation"]))

    Server, AppService, web, protocol = _load_textual_serve()
    WSMsgType, to_int = protocol
    web_root = ensure_private_directory(
        Path(tempfile.mkdtemp(prefix="agent-pbx-demo-web-"))
    )
    home = ensure_private_directory(web_root / "home")
    runtime_dir = ensure_private_directory(web_root / "runtime")
    child_environment = build_public_demo_environment(
        os.environ,
        home=home,
        runtime_dir=runtime_dir,
    )
    child_argv = [
        sys.executable,
        "-m",
        "agent_pbx",
        "demo",
        "tui-session",
        "--scenario",
        DEMO_SCENARIO_ID,
        "--ttl-seconds",
        str(session_ttl_seconds),
        "--tmux-bin",
        tmux_bin,
    ]

    class PublicDemoAppService(AppService):
        def _build_environment(
            self, width: int = 80, height: int = 24
        ) -> dict[str, str]:
            environment = dict(child_environment)
            environment.update(
                {
                    "TEXTUAL_DRIVER": "textual.drivers.web_driver:WebDriver",
                    "TEXTUAL_FPS": "60",
                    "TEXTUAL_COLOR_SYSTEM": "truecolor",
                    "TERM_PROGRAM": "textual",
                    "TERM_PROGRAM_VERSION": "agent-pbx-public-demo",
                    "COLUMNS": str(width),
                    "ROWS": str(height),
                }
            )
            if self.debug:
                environment["TEXTUAL"] = "debug"
            return environment

        async def _open_app_process(self, width: int = 80, height: int = 24) -> Any:
            environment = self._build_environment(width=width, height=height)
            self._process = process = await asyncio.create_subprocess_exec(
                *child_argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=environment,
                cwd=web_root,
                start_new_session=True,
            )
            assert process.stdin is not None
            self._stdin = process.stdin
            return process

        async def stop(self) -> None:
            if self._task is not None:
                await self._download_manager.cancel_app_downloads(
                    app_service_id=self.app_service_id
                )
                await self.send_meta({"type": "quit"})
                try:
                    await asyncio.wait_for(asyncio.shield(self._task), timeout=10)
                except asyncio.TimeoutError:
                    self._task.cancel()
                    with suppress(asyncio.CancelledError):
                        await self._task
                self._task = None
            process = self._process
            if process is not None and process.returncode is None:
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    with suppress(ProcessLookupError):
                        process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        with suppress(ProcessLookupError):
                            process.kill()
                        await process.wait()
            if self._stdin is not None:
                self._stdin.close()
                with suppress(Exception):
                    await self._stdin.wait_closed()
            self._process = None

    class PublicDemoServer(Server):
        def __init__(self) -> None:
            super().__init__(
                command="agent-pbx demo tui-session",
                host=host,
                port=port,
                title="Agent PBX interactive product tour",
                public_url=effective_public_url,
            )
            self.allowed_origins = frozenset(effective_origins)
            self.max_sessions = max_sessions
            self.session_ttl_seconds = session_ttl_seconds
            self.active_sessions = 0
            self._session_lock: asyncio.Lock | None = None
            self.public_demo_environment = dict(child_environment)

        async def _make_app(self) -> Any:
            app = await super()._make_app()

            @web.middleware
            async def security_headers(request: Any, handler: Any) -> Any:
                response = await handler(request)
                response.headers["X-Content-Type-Options"] = "nosniff"
                response.headers["Referrer-Policy"] = "no-referrer"
                response.headers["Permissions-Policy"] = (
                    "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
                )
                frame_ancestors = " ".join(
                    ["'self'", *(origin for origin in self.allowed_origins)]
                )
                response.headers["Content-Security-Policy"] = (
                    f"frame-ancestors {frame_ancestors}"
                )
                return response

            app.middlewares.append(security_headers)
            app.add_routes(
                [
                    web.get("/healthz", self.handle_health, name="health"),
                    web.get(
                        "/demo-contract.json",
                        self.handle_contract,
                        name="demo-contract",
                    ),
                ]
            )
            return app

        async def handle_health(self, _request: Any) -> Any:
            return web.json_response(
                {
                    "ok": True,
                    "service": "agent-pbx-public-demo",
                    "version": __version__,
                    "scenario": DEMO_SCENARIO_ID,
                    "active_sessions": self.active_sessions,
                    "max_sessions": self.max_sessions,
                }
            )

        async def handle_contract(self, request: Any) -> Any:
            response = web.json_response(public_demo_contract())
            response.headers["Vary"] = "Origin"
            origin = str(request.headers.get("Origin") or "").rstrip("/")
            if origin in self.allowed_origins:
                response.headers["Access-Control-Allow-Origin"] = origin
            return response

        async def _process_messages(self, websocket: Any, app_service: Any) -> None:
            async for message in websocket:
                if message.type != WSMsgType.TEXT:
                    continue
                try:
                    envelope = message.json()
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
                if not isinstance(envelope, list) or len(envelope) != 2:
                    continue
                type_, data = envelope
                if type_ == "stdin" and isinstance(data, str):
                    if len(data.encode("utf-8")) <= 16_384:
                        await app_service.send_bytes(data.encode("utf-8"))
                elif type_ == "resize" and isinstance(data, dict):
                    width = min(300, max(40, int(data.get("width", 80))))
                    height = min(120, max(12, int(data.get("height", 24))))
                    await app_service.set_terminal_size(width, height)
                elif type_ == "ping":
                    await websocket.send_json(["pong", data])
                elif type_ == "blur":
                    await app_service.blur()
                elif type_ == "focus":
                    await app_service.focus()

        async def handle_websocket(self, request: Any) -> Any:
            origin = str(request.headers.get("Origin") or "").rstrip("/")
            if origin not in self.allowed_origins:
                raise web.HTTPForbidden(text="browser origin is not allowed")
            if self._session_lock is None:
                self._session_lock = asyncio.Lock()
            async with self._session_lock:
                if self.active_sessions >= self.max_sessions:
                    raise web.HTTPServiceUnavailable(
                        text="all public demo sessions are currently in use",
                        headers={"Retry-After": "30"},
                    )
                self.active_sessions += 1

            websocket = web.WebSocketResponse(heartbeat=15, max_msg_size=65_536)
            width = min(300, max(40, to_int(request.query.get("width", "120"), 120)))
            height = min(120, max(12, to_int(request.query.get("height", "40"), 40)))
            app_service: Any | None = None
            expiry_task: asyncio.Task[None] | None = None
            try:
                await websocket.prepare(request)
                app_service = PublicDemoAppService(
                    "agent-pbx demo tui-session",
                    write_bytes=websocket.send_bytes,
                    write_str=websocket.send_str,
                    close=websocket.close,
                    download_manager=self.download_manager,
                    debug=self.debug,
                )
                await app_service.start(width, height)

                async def expire_session() -> None:
                    await asyncio.sleep(self.session_ttl_seconds)
                    await websocket.close(
                        code=1000, message=b"public demo session expired"
                    )

                expiry_task = asyncio.create_task(expire_session())
                await self._process_messages(websocket, app_service)
            except asyncio.CancelledError:
                await websocket.close()
            finally:
                if expiry_task is not None:
                    expiry_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await expiry_task
                if app_service is not None:
                    await app_service.stop()
                async with self._session_lock:
                    self.active_sessions -= 1
            return websocket

    return PublicDemoServer(), web_root


def serve_public_demo(**kwargs: Any) -> int:
    """Serve the public browser demo until interrupted, then remove server state."""

    debug = bool(kwargs.pop("debug", False))
    server, web_root = build_public_demo_server(**kwargs)
    try:
        with replaced_environment(server.public_demo_environment):
            server.serve(debug=debug)
        return 0
    finally:
        shutil.rmtree(web_root, ignore_errors=True)
