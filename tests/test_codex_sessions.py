import json

from agent_pbx.codex_sessions import (
    CodexSessionPathCache,
    CodexTranscriptTailCache,
    codex_session_transcript_boundary,
    find_codex_session_file,
    latest_assistant_output_for_session,
    latest_assistant_output_from_session_file,
    latest_assistant_transcript_for_session,
    latest_assistant_transcript_from_session_file,
)


def write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )


def assistant_message(text: str, *, phase: str = "final_answer") -> dict[str, object]:
    return {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "assistant",
            "phase": phase,
            "content": [{"type": "output_text", "text": text}],
        },
    }


def test_latest_assistant_output_prefers_latest_final_answer(tmp_path) -> None:
    session_file = tmp_path / "rollout-session-1.jsonl"
    write_jsonl(
        session_file,
        [
            {"type": "session_meta", "payload": {"id": "session-1"}},
            assistant_message("commentary text", phase="commentary"),
            assistant_message("first final"),
            assistant_message("latest final"),
        ],
    )

    assert latest_assistant_output_from_session_file(session_file) == "latest final"
    result = latest_assistant_transcript_from_session_file(session_file)
    assert result is not None
    assert result.text == "latest final"
    assert result.phase == "final_answer"
    assert result.line_index == 3
    assert result.path == session_file
    assert result.mtime == session_file.stat().st_mtime


def test_latest_assistant_output_falls_back_to_any_assistant_text(tmp_path) -> None:
    session_file = tmp_path / "rollout-session-1.jsonl"
    write_jsonl(
        session_file,
        [
            {"type": "session_meta", "payload": {"id": "session-1"}},
            assistant_message("only visible response", phase="commentary"),
        ],
    )

    assert latest_assistant_output_from_session_file(session_file) == "only visible response"


def test_latest_assistant_output_for_session_finds_rollout_file(tmp_path) -> None:
    sessions_dir = tmp_path / "sessions" / "2026" / "09" / "26"
    session_file = sessions_dir / "rollout-2026-09-26T00-00-00-session-abc.jsonl"
    write_jsonl(
        session_file,
        [
            {"type": "session_meta", "payload": {"id": "session-abc"}},
            assistant_message("final from transcript"),
        ],
    )

    assert find_codex_session_file("session-abc", codex_home=tmp_path) == session_file
    found = latest_assistant_output_for_session("session-abc", codex_home=tmp_path)
    assert found is not None
    assert found[0] == "final from transcript"
    assert found[1] == session_file
    structured = latest_assistant_transcript_for_session(
        "session-abc",
        codex_home=tmp_path,
    )
    assert structured is not None
    assert structured.session_id == "session-abc"
    assert structured.text == "final from transcript"


def test_codex_session_path_cache_reuses_index(tmp_path, monkeypatch) -> None:
    sessions_dir = tmp_path / "sessions" / "2026" / "09" / "26"
    session_file = sessions_dir / "rollout-2026-09-26T00-00-00-session-abc.jsonl"
    write_jsonl(
        session_file,
        [
            {"type": "session_meta", "payload": {"id": "session-abc"}},
            assistant_message("final from transcript"),
        ],
    )
    cache = CodexSessionPathCache(ttl_seconds=60)
    calls = []
    original_rglob = type(tmp_path / "sessions").rglob

    def counting_rglob(path, pattern):
        calls.append(pattern)
        return original_rglob(path, pattern)

    monkeypatch.setattr(type(tmp_path / "sessions"), "rglob", counting_rglob)

    assert find_codex_session_file(
        "session-abc",
        codex_home=tmp_path,
        path_cache=cache,
    ) == session_file
    assert find_codex_session_file(
        "session-abc",
        codex_home=tmp_path,
        path_cache=cache,
    ) == session_file
    assert calls == ["*.jsonl"]


def test_codex_session_transcript_boundary_counts_last_line(tmp_path) -> None:
    sessions_dir = tmp_path / "sessions" / "2026" / "09" / "26"
    session_file = sessions_dir / "rollout-2026-09-26T00-00-00-session-abc.jsonl"
    write_jsonl(
        session_file,
        [
            {"type": "session_meta", "payload": {"id": "session-abc"}},
            assistant_message("first"),
            assistant_message("second"),
        ],
    )

    boundary = codex_session_transcript_boundary(
        "session-abc",
        codex_home=tmp_path,
    )

    assert boundary is not None
    assert boundary.session_id == "session-abc"
    assert boundary.path == session_file
    assert boundary.line_index == 2


def test_transcript_tail_cache_reads_only_appended_assistant_records(tmp_path) -> None:
    session_file = tmp_path / "rollout-session-1.jsonl"
    write_jsonl(
        session_file,
        [
            {"type": "session_meta", "payload": {"id": "session-1"}},
            assistant_message("first final"),
        ],
    )
    cache = CodexTranscriptTailCache()

    first = latest_assistant_transcript_from_session_file(
        session_file,
        tail_cache=cache,
    )
    with session_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(assistant_message("second final")) + "\n")
    second = latest_assistant_transcript_from_session_file(
        session_file,
        tail_cache=cache,
    )

    assert first is not None
    assert first.text == "first final"
    assert first.line_index == 1
    assert second is not None
    assert second.text == "second final"
    assert second.session_id == "session-1"
    assert second.line_index == 2


def test_transcript_tail_cache_defers_partial_jsonl_record_until_completed(tmp_path) -> None:
    session_file = tmp_path / "rollout-session-1.jsonl"
    write_jsonl(
        session_file,
        [
            {"type": "session_meta", "payload": {"id": "session-1"}},
            assistant_message("completed final"),
        ],
    )
    cache = CodexTranscriptTailCache()
    assert latest_assistant_transcript_from_session_file(
        session_file,
        tail_cache=cache,
    ) is not None

    partial = json.dumps(assistant_message("partial final"))
    with session_file.open("a", encoding="utf-8") as handle:
        handle.write(partial)
    before_newline = latest_assistant_transcript_from_session_file(
        session_file,
        tail_cache=cache,
    )
    with session_file.open("a", encoding="utf-8") as handle:
        handle.write("\n")
    after_newline = latest_assistant_transcript_from_session_file(
        session_file,
        tail_cache=cache,
    )

    assert before_newline is not None
    assert before_newline.text == "completed final"
    assert after_newline is not None
    assert after_newline.text == "partial final"
    assert after_newline.line_index == 2


def test_transcript_tail_cache_rescans_replaced_or_truncated_rollout(tmp_path) -> None:
    session_file = tmp_path / "rollout-session-1.jsonl"
    write_jsonl(
        session_file,
        [
            {"type": "session_meta", "payload": {"id": "session-1"}},
            assistant_message("a long old response that will be removed"),
        ],
    )
    cache = CodexTranscriptTailCache()
    original = latest_assistant_transcript_from_session_file(
        session_file,
        tail_cache=cache,
    )
    write_jsonl(
        session_file,
        [
            {"type": "session_meta", "payload": {"id": "session-1"}},
            assistant_message("new"),
        ],
    )
    replaced = latest_assistant_transcript_from_session_file(
        session_file,
        tail_cache=cache,
    )

    assert original is not None
    assert original.text.startswith("a long old")
    assert replaced is not None
    assert replaced.text == "new"
    assert replaced.line_index == 1


def test_transcript_tail_cache_returns_none_for_removed_rollout(tmp_path) -> None:
    cache = CodexTranscriptTailCache()

    assert (
        latest_assistant_transcript_from_session_file(
            tmp_path / "missing.jsonl",
            tail_cache=cache,
        )
        is None
    )
