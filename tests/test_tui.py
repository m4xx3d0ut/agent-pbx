from agent_pbx.tui import AgentPBXTUI


def test_tui_constructs() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", token="test")

    assert app.server == "http://127.0.0.1:8765"
    assert app.token == "test"
