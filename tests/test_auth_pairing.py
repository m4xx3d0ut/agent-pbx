from pathlib import Path

from fastapi.testclient import TestClient

from agent_pbx.api import create_app, create_token_helper_app
from agent_pbx.config import ServerConfig
from agent_pbx.store import Store


def test_local_server_allows_authless_when_no_tokens(tmp_path: Path) -> None:
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite"))
    client = TestClient(app)

    response = client.get("/v1/auth/check")

    assert response.status_code == 200


def test_runtime_token_authenticates(tmp_path: Path) -> None:
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite", token="secret"))
    client = TestClient(app)

    rejected = client.get("/v1/auth/check")
    accepted = client.get("/v1/auth/check", headers={"Authorization": "Bearer secret"})

    assert rejected.status_code == 401
    assert accepted.status_code == 200


def test_lan_binding_requires_token(tmp_path: Path) -> None:
    app = create_app(ServerConfig(host="0.0.0.0", db_path=tmp_path / "pbx.sqlite"))
    client = TestClient(app)

    response = client.get("/v1/auth/check")

    assert response.status_code == 401


def test_pairing_helper_issues_single_use_token(tmp_path: Path) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    app, code = create_token_helper_app(store, pairing_code="123456")
    client = TestClient(app)

    issued = client.post("/pair", json={"code": code, "label": "dev-tui"})
    reused = client.post("/pair", json={"code": code, "label": "dev-tui"})

    assert issued.status_code == 200
    assert issued.json()["token"].startswith("apbx_")
    assert reused.status_code == 403
    assert store.verify_token(issued.json()["token"]) is not None
