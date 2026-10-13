from __future__ import annotations

import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import subprocess
import threading
import urllib.request

from agent_pbx.demo.fake_joplin import JoplinFixtureHandler, NOTES
from agent_pbx.demo.fixtures import prepare_demo_fixtures


def test_prepare_demo_fixtures_creates_private_repositories_and_tools(
    tmp_path: Path,
) -> None:
    fixtures = prepare_demo_fixtures(tmp_path / "fixture")

    assert fixtures.root.stat().st_mode & 0o777 == 0o700
    assert (fixtures.platform_project / ".git").is_dir()
    assert (fixtures.release_project / ".git").is_dir()
    assert (fixtures.platform_project / "ops/runtime.yaml").read_text().startswith(
        "profile: public-demo"
    )
    for executable in (
        fixtures.codex_bin,
        fixtures.workerbee_bin,
        fixtures.github_bin,
    ):
        assert executable.stat().st_mode & 0o100

    version = subprocess.run(
        [str(fixtures.codex_bin), "--version"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert version.stdout.strip() == "codex-cli 0.159.3"

    workerbee = subprocess.run(
        [str(fixtures.workerbee_bin), "project", "status", "platform-console"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(workerbee.stdout)["project_status"]["app_status"]["ready"]

    github = subprocess.run(
        [str(fixtures.github_bin), "pr", "list", "--json", "number,title"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(github.stdout)[0]["number"] == 12


def test_fake_joplin_serves_and_updates_notes() -> None:
    original_note = dict(NOTES["architecture"])
    server = ThreadingHTTPServer(("127.0.0.1", 0), JoplinFixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = int(server.server_address[1])
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/folders", timeout=5
        ) as response:
            folders = json.loads(response.read())
        assert folders["items"][0]["title"] == "Agent PBX"

        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/notes/architecture",
            data=json.dumps({"body": "# Updated"}).encode(),
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        with urllib.request.urlopen(request, timeout=1) as response:
            note = json.loads(response.read())
        assert note["body"] == "# Updated"
    finally:
        NOTES["architecture"] = original_note
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
