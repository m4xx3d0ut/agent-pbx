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


PUBLIC_DEMO_TERMINAL_RESIZE_SCRIPT_PATH = "/agent-pbx-terminal-resize.js"
PUBLIC_DEMO_TERMINAL_RESIZE_SCRIPT_ID = "agent-pbx-terminal-resize-sync"
PUBLIC_DEMO_LOADING_SCRIPT_PATH = "/agent-pbx-loading.js"
PUBLIC_DEMO_LOADING_SCRIPT_ID = "agent-pbx-loading-controller"
PUBLIC_DEMO_LOADING_STYLE_ID = "agent-pbx-loading-style"
PUBLIC_DEMO_LOADING_OVERLAY_ID = "agent-pbx-demo-loading"
PUBLIC_DEMO_INITIAL_RESIZE_TIMEOUT_SECONDS = 1.0
PUBLIC_DEMO_INITIAL_MESSAGE_LIMIT = 16
PUBLIC_DEMO_LOADING_STYLE = r"""
#agent-pbx-demo-loading {
  position: fixed;
  inset: 0;
  z-index: 40;
  display: grid;
  place-items: center;
  min-width: 100vw;
  min-height: 100vh;
  padding: clamp(18px, 4vw, 48px);
  overflow: hidden;
  color: #d8f7f7;
  background:
    linear-gradient(rgba(0, 229, 255, 0.035) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 43, 214, 0.025) 1px, transparent 1px),
    radial-gradient(circle at 50% 38%, rgba(0, 229, 255, 0.1), transparent 34%),
    #05080d;
  background-size: 28px 28px, 28px 28px, auto, auto;
  opacity: 1;
  visibility: visible;
  transition: opacity 220ms ease, visibility 220ms ease;
  box-sizing: border-box;
  font-family: "Roboto Mono", Menlo, Consolas, monospace;
}

body.-first-byte #agent-pbx-demo-loading {
  opacity: 0;
  visibility: hidden;
  pointer-events: none;
}

.agent-pbx-loader-panel {
  position: relative;
  display: grid;
  justify-items: center;
  width: min(560px, 100%);
  padding: clamp(30px, 6vw, 54px);
  overflow: hidden;
  border: 1px solid rgba(0, 229, 255, 0.48);
  border-radius: 8px;
  background: rgba(8, 14, 22, 0.94);
  box-shadow:
    0 0 0 1px rgba(255, 43, 214, 0.12) inset,
    0 0 48px rgba(0, 229, 255, 0.1);
  text-align: center;
}

.agent-pbx-loader-panel::before,
.agent-pbx-loader-panel::after {
  position: absolute;
  width: 38px;
  height: 2px;
  content: "";
  background: #ff2bd6;
  box-shadow: 0 0 12px rgba(255, 43, 214, 0.7);
}

.agent-pbx-loader-panel::before {
  top: 0;
  left: 18px;
}

.agent-pbx-loader-panel::after {
  right: 18px;
  bottom: 0;
  background: #00e5ff;
  box-shadow: 0 0 12px rgba(0, 229, 255, 0.7);
}

.agent-pbx-loader-mark {
  position: relative;
  width: 104px;
  height: 104px;
  margin-bottom: 28px;
}

.agent-pbx-loader-ring,
.agent-pbx-loader-core {
  position: absolute;
  border-radius: 50%;
}

.agent-pbx-loader-ring {
  inset: 0;
  border: 2px solid transparent;
  border-top-color: #00e5ff;
  border-right-color: rgba(0, 229, 255, 0.34);
  box-shadow: 0 0 18px rgba(0, 229, 255, 0.18);
  animation: agent-pbx-loader-spin 1.35s linear infinite;
}

.agent-pbx-loader-ring--inner {
  inset: 14px;
  border-top-color: #ff2bd6;
  border-right-color: transparent;
  border-bottom-color: rgba(255, 43, 214, 0.4);
  box-shadow: 0 0 14px rgba(255, 43, 214, 0.16);
  animation-direction: reverse;
  animation-duration: 0.95s;
}

.agent-pbx-loader-core {
  inset: 31px;
  display: grid;
  place-items: center;
  border: 1px solid rgba(216, 247, 247, 0.25);
  color: #d8f7f7;
  background: #0c1620;
  box-shadow: 0 0 20px rgba(0, 229, 255, 0.12) inset;
  font-size: 0.7rem;
  font-weight: 800;
  letter-spacing: 0.11em;
}

.agent-pbx-loader-kicker {
  margin: 0 0 11px;
  color: #00e5ff;
  font-size: 0.72rem;
  font-weight: 800;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}

.agent-pbx-loader-title {
  max-width: 440px;
  margin: 0;
  color: #f1ffff;
  background: transparent;
  font-size: clamp(1.02rem, 2.8vw, 1.42rem);
  line-height: 1.35;
}

.agent-pbx-loader-status {
  min-height: 2.8em;
  max-width: 440px;
  margin: 16px 0 0;
  color: #91aeb8;
  font-size: clamp(0.72rem, 2vw, 0.84rem);
  line-height: 1.6;
}

.agent-pbx-loader-dots {
  display: flex;
  gap: 8px;
  margin-top: 22px;
}

.agent-pbx-loader-dots span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #00e5ff;
  box-shadow: 0 0 9px rgba(0, 229, 255, 0.65);
  animation: agent-pbx-loader-pulse 1.2s ease-in-out infinite;
}

.agent-pbx-loader-dots span:nth-child(2) {
  animation-delay: 160ms;
}

.agent-pbx-loader-dots span:nth-child(3) {
  animation-delay: 320ms;
}

.agent-pbx-loader-retry {
  display: none;
  margin-top: 22px;
  padding: 10px 18px;
  border: 1px solid #ffb020;
  border-radius: 4px;
  color: #fff4d4;
  background: #22190d;
  font: 700 0.78rem/1.2 "Roboto Mono", Menlo, Consolas, monospace;
  cursor: pointer;
}

#agent-pbx-demo-loading.is-error .agent-pbx-loader-ring {
  border-top-color: #ffb020;
  border-right-color: rgba(255, 176, 32, 0.35);
  animation-play-state: paused;
}

#agent-pbx-demo-loading.is-error .agent-pbx-loader-dots {
  display: none;
}

#agent-pbx-demo-loading.is-error .agent-pbx-loader-retry {
  display: inline-block;
}

@keyframes agent-pbx-loader-spin {
  to { transform: rotate(360deg); }
}

@keyframes agent-pbx-loader-pulse {
  0%, 100% { opacity: 0.28; transform: translateY(0); }
  50% { opacity: 1; transform: translateY(-3px); }
}

@media (max-width: 560px) {
  .agent-pbx-loader-panel {
    padding: 28px 22px;
  }

  .agent-pbx-loader-mark {
    width: 86px;
    height: 86px;
    margin-bottom: 22px;
  }

  .agent-pbx-loader-core {
    inset: 26px;
    font-size: 0.6rem;
  }
}

@media (prefers-reduced-motion: reduce) {
  #agent-pbx-demo-loading,
  .agent-pbx-loader-ring,
  .agent-pbx-loader-dots span {
    animation: none;
    transition: none;
  }

  .agent-pbx-loader-ring {
    border-color: rgba(0, 229, 255, 0.35);
    border-top-color: #00e5ff;
  }

  .agent-pbx-loader-ring--inner {
    border-color: rgba(255, 43, 214, 0.35);
    border-bottom-color: #ff2bd6;
  }
}
""".strip()
PUBLIC_DEMO_LOADING_MARKUP = r"""
<div id="agent-pbx-demo-loading" role="status" aria-live="polite" aria-atomic="true">
  <div class="agent-pbx-loader-panel">
    <div class="agent-pbx-loader-mark" aria-hidden="true">
      <span class="agent-pbx-loader-ring"></span>
      <span class="agent-pbx-loader-ring agent-pbx-loader-ring--inner"></span>
      <span class="agent-pbx-loader-core">PBX</span>
    </div>
    <p class="agent-pbx-loader-kicker">Initializing Agent PBX</p>
    <h1 class="agent-pbx-loader-title" data-agent-pbx-loader-title>Preparing an isolated engineering console</h1>
    <p class="agent-pbx-loader-status" data-agent-pbx-loader-status>Allocating a private demo session…</p>
    <div class="agent-pbx-loader-dots" aria-hidden="true"><span></span><span></span><span></span></div>
    <button class="agent-pbx-loader-retry" type="button" data-agent-pbx-loader-retry>Retry demo</button>
  </div>
</div>
""".strip()
PUBLIC_DEMO_LOADING_SCRIPT = r"""
(() => {
  "use strict";

  const overlay = document.getElementById("agent-pbx-demo-loading");
  const title = overlay?.querySelector("[data-agent-pbx-loader-title]");
  const status = overlay?.querySelector("[data-agent-pbx-loader-status]");
  const retry = overlay?.querySelector("[data-agent-pbx-loader-retry]");
  if (!overlay || !title || !status || !retry) return;

  const stages = [
    [1800, "Starting the Agent PBX control plane…"],
    [3900, "Binding disposable tmux runtimes…"],
    [6200, "Rendering the engineering operator console…"],
    [10000, "First launch can take a few seconds. Your session remains private and isolated."],
  ];
  const timers = stages.map(([delay, message]) => window.setTimeout(() => {
    if (!document.body.classList.contains("-first-byte") && !overlay.classList.contains("is-error")) {
      status.textContent = message;
    }
  }, delay));

  const stopTimers = () => {
    for (const timer of timers) window.clearTimeout(timer);
  };

  const synchronizeState = () => {
    if (document.body.classList.contains("-first-byte")) {
      stopTimers();
      overlay.setAttribute("aria-hidden", "true");
      observer.disconnect();
      return;
    }
    if (document.body.classList.contains("-closed")) {
      stopTimers();
      overlay.classList.add("is-error");
      title.textContent = "The demo session could not start";
      status.textContent = "Please retry in a moment. No workstation data or credentials were connected.";
    }
  };

  const observer = new MutationObserver(synchronizeState);
  observer.observe(document.body, { attributes: true, attributeFilter: ["class"] });
  retry.addEventListener("click", () => {
    if (typeof window.refresh === "function") {
      window.refresh();
    } else {
      window.location.reload();
    }
  });
  synchronizeState();
})();
""".strip()
PUBLIC_DEMO_TERMINAL_RESIZE_SCRIPT = r"""
(() => {
  "use strict";

  if (window.__agentPbxTerminalResizeSyncInstalled) return;
  window.__agentPbxTerminalResizeSyncInstalled = true;

  const upstreamLoad = window.onload;
  window.onload = null;

  let scheduled = false;
  let lastGeometry = "";
  let terminalObserver = null;

  const nextPaint = () => new Promise((resolve) => requestAnimationFrame(resolve));

  const synchronize = () => {
    scheduled = false;
    const terminal = document.querySelector(".textual-terminal");
    if (!terminal || typeof window.onresize !== "function") return;
    const bounds = terminal.getBoundingClientRect();
    if (bounds.width <= 0 || bounds.height <= 0) return;
    window.dispatchEvent(new Event("resize"));
  };

  const schedule = () => {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => requestAnimationFrame(synchronize));
  };

  const startResizeSync = () => {
    const terminal = document.querySelector(".textual-terminal");
    if (!terminal) return;

    if (typeof ResizeObserver === "function") {
      terminalObserver = new ResizeObserver((entries) => {
        const bounds = entries[0]?.contentRect;
        if (!bounds || bounds.width <= 0 || bounds.height <= 0) return;
        const geometry = `${Math.round(bounds.width)}x${Math.round(bounds.height)}`;
        if (geometry === lastGeometry) return;
        lastGeometry = geometry;
        schedule();
      });
      terminalObserver.observe(terminal);
    }

    if (window.visualViewport) {
      window.visualViewport.addEventListener("resize", schedule, { passive: true });
    }
    window.addEventListener("pageshow", schedule, { passive: true });
    schedule();
  };

  const startAfterLayoutSettles = async (event) => {
    if (document.fonts?.ready) {
      try {
        await document.fonts.ready;
      } catch (_error) {
        // Font loading failure must not prevent the isolated demo from starting.
      }
    }
    await nextPaint();
    await nextPaint();
    if (typeof upstreamLoad === "function") {
      upstreamLoad.call(window, event);
    }
    startResizeSync();
  };

  window.addEventListener(
    "load",
    (event) => void startAfterLayoutSettles(event),
    { once: true },
  );
})();
""".strip()


def _bounded_public_demo_terminal_size(
    data: dict[str, Any],
    *,
    fallback_width: int,
    fallback_height: int,
) -> tuple[int, int]:
    def bounded(value: Any, fallback: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError, OverflowError):
            parsed = fallback
        return min(maximum, max(minimum, parsed))

    return (
        bounded(data.get("width"), fallback_width, 40, 300),
        bounded(data.get("height"), fallback_height, 12, 120),
    )


def _public_demo_message_envelope(message: Any, ws_msg_type: Any) -> list[Any] | None:
    if message.type != ws_msg_type.TEXT:
        return None
    try:
        envelope = message.json()
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(envelope, list) or len(envelope) != 2:
        return None
    return envelope


async def _receive_initial_public_demo_terminal_size(
    websocket: Any,
    ws_msg_type: Any,
    *,
    fallback_width: int,
    fallback_height: int,
    timeout_seconds: float = PUBLIC_DEMO_INITIAL_RESIZE_TIMEOUT_SECONDS,
) -> tuple[int, int, tuple[Any, ...], bool]:
    """Wait for the browser's authoritative post-open terminal dimensions.

    Textual Serve includes provisional dimensions in the WebSocket URL, then
    calls ``fit()`` and sends a resize envelope after the socket opens. Starting
    Textual from the URL dimensions can paint a small framebuffer before the
    authoritative resize arrives. Buffer unrelated initial input until the app
    process starts and fall back to the sanitized URL dimensions on timeout.
    """

    fallback_width, fallback_height = _bounded_public_demo_terminal_size(
        {"width": fallback_width, "height": fallback_height},
        fallback_width=120,
        fallback_height=40,
    )
    buffered_messages: list[Any] = []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, timeout_seconds)
    closed_types = {
        ws_msg_type.CLOSE,
        ws_msg_type.CLOSED,
        ws_msg_type.ERROR,
    }
    for _ in range(PUBLIC_DEMO_INITIAL_MESSAGE_LIMIT):
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        try:
            message = await asyncio.wait_for(websocket.receive(), timeout=remaining)
        except asyncio.TimeoutError:
            break
        if message.type in closed_types:
            return fallback_width, fallback_height, tuple(buffered_messages), False
        envelope = _public_demo_message_envelope(message, ws_msg_type)
        if envelope is not None:
            type_, data = envelope
            if type_ == "resize" and isinstance(data, dict):
                width, height = _bounded_public_demo_terminal_size(
                    data,
                    fallback_width=fallback_width,
                    fallback_height=fallback_height,
                )
                return width, height, tuple(buffered_messages), True
        buffered_messages.append(message)
    return fallback_width, fallback_height, tuple(buffered_messages), True


def inject_public_demo_terminal_resize_bootstrap(html: str) -> str:
    """Add the Agent PBX geometry synchronizer to Textual Serve's index."""

    if f'id="{PUBLIC_DEMO_TERMINAL_RESIZE_SCRIPT_ID}"' in html:
        return html
    closing_head = "</head>"
    if closing_head not in html:
        return html
    script_tag = (
        f'<script id="{PUBLIC_DEMO_TERMINAL_RESIZE_SCRIPT_ID}" defer '
        f'src="{PUBLIC_DEMO_TERMINAL_RESIZE_SCRIPT_PATH}"></script>'
    )
    return html.replace(closing_head, f"    {script_tag}\n  {closing_head}", 1)


def inject_public_demo_loading_experience(html: str) -> str:
    """Add an accessible Agent PBX loader until Textual paints its first frame."""

    if f'id="{PUBLIC_DEMO_LOADING_OVERLAY_ID}"' in html:
        return html
    closing_head = "</head>"
    closing_body = "</body>"
    if closing_head not in html or closing_body not in html:
        return html
    style_tag = (
        f'<style id="{PUBLIC_DEMO_LOADING_STYLE_ID}">\n'
        f"{PUBLIC_DEMO_LOADING_STYLE}\n"
        "</style>"
    )
    script_tag = (
        f'<script id="{PUBLIC_DEMO_LOADING_SCRIPT_ID}" defer '
        f'src="{PUBLIC_DEMO_LOADING_SCRIPT_PATH}"></script>'
    )
    html = html.replace(
        closing_head,
        f"    {style_tag}\n    {script_tag}\n  {closing_head}",
        1,
    )
    return html.replace(
        closing_body,
        f"    {PUBLIC_DEMO_LOADING_MARKUP}\n  {closing_body}",
        1,
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
                if (
                    request.path == "/"
                    and isinstance(response, web.Response)
                    and response.content_type == "text/html"
                    and response.text
                ):
                    response.text = inject_public_demo_loading_experience(
                        inject_public_demo_terminal_resize_bootstrap(response.text)
                    )
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
                        PUBLIC_DEMO_TERMINAL_RESIZE_SCRIPT_PATH,
                        self.handle_terminal_resize_script,
                        name="agent-pbx-terminal-resize-script",
                    ),
                    web.get(
                        PUBLIC_DEMO_LOADING_SCRIPT_PATH,
                        self.handle_loading_script,
                        name="agent-pbx-loading-script",
                    ),
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

        async def handle_terminal_resize_script(self, _request: Any) -> Any:
            return web.Response(
                text=PUBLIC_DEMO_TERMINAL_RESIZE_SCRIPT,
                content_type="application/javascript",
                headers={"Cache-Control": "no-cache"},
            )

        async def handle_loading_script(self, _request: Any) -> Any:
            return web.Response(
                text=PUBLIC_DEMO_LOADING_SCRIPT,
                content_type="application/javascript",
                headers={"Cache-Control": "no-cache"},
            )

        async def handle_contract(self, request: Any) -> Any:
            response = web.json_response(public_demo_contract())
            response.headers["Vary"] = "Origin"
            origin = str(request.headers.get("Origin") or "").rstrip("/")
            if origin in self.allowed_origins:
                response.headers["Access-Control-Allow-Origin"] = origin
            return response

        async def _process_message(
            self, websocket: Any, app_service: Any, message: Any
        ) -> None:
            envelope = _public_demo_message_envelope(message, WSMsgType)
            if envelope is None:
                return
            type_, data = envelope
            if type_ == "stdin" and isinstance(data, str):
                if len(data.encode("utf-8")) <= 16_384:
                    await app_service.send_bytes(data.encode("utf-8"))
            elif type_ == "resize" and isinstance(data, dict):
                width, height = _bounded_public_demo_terminal_size(
                    data,
                    fallback_width=80,
                    fallback_height=24,
                )
                await app_service.set_terminal_size(width, height)
            elif type_ == "ping":
                await websocket.send_json(["pong", data])
            elif type_ == "blur":
                await app_service.blur()
            elif type_ == "focus":
                await app_service.focus()

        async def _process_messages(
            self,
            websocket: Any,
            app_service: Any,
            *,
            initial_messages: tuple[Any, ...] = (),
        ) -> None:
            for message in initial_messages:
                await self._process_message(websocket, app_service, message)
            async for message in websocket:
                await self._process_message(websocket, app_service, message)

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
                width, height, initial_messages, connected = (
                    await _receive_initial_public_demo_terminal_size(
                        websocket,
                        WSMsgType,
                        fallback_width=width,
                        fallback_height=height,
                    )
                )
                if not connected:
                    return websocket
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
                await self._process_messages(
                    websocket,
                    app_service,
                    initial_messages=initial_messages,
                )
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
