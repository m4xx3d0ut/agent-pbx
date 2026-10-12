from __future__ import annotations

import json
import sys


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "project" in args and "status" in args:
        value: object = {
            "project": "platform-console",
            "mode": "local",
            "running": True,
            "project_status": {
                "project": "platform-console",
                "running": True,
                "app_status": {
                    "ready": True,
                    "workloads": [
                        {
                            "name": "agent-pbx-api",
                            "ready": "1/1",
                            "status": "running",
                        },
                        {
                            "name": "platform-console",
                            "ready": "2/2",
                            "status": "running",
                        },
                    ],
                    "ingress_urls": ["https://platform.demo.invalid"],
                },
                "latest_deployment": {
                    "name": "platform-console-demo",
                    "namespace": "agent-pbx-demo",
                    "image": "platform-console:demo",
                    "created_at": "2026-10-12T12:00:00Z",
                },
            },
        }
    elif "projects" in args:
        value = {
            "projects": [
                {
                    "project": "platform-console",
                    "cwd_hint": "platform-console",
                    "running": True,
                    "dashboard_url": "https://platform.demo.invalid",
                    "state_dir": "/demo/workerbee/platform-console",
                }
            ],
            "global_dashboard": {
                "ready": True,
                "url": "https://workerbee.demo.invalid",
            },
        }
    elif "capabilities" in args:
        value = {
            "api_version": "workerbee.v1",
            "version": "0.1.7-demo",
            "capabilities": ["projects", "project_status", "session_start"],
        }
    else:
        value = {}
    print(json.dumps(value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
