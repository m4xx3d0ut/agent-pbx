from agent_pbx.polling import MAX_REPEAT_POLL_SECONDS, bounded_repeat_poll_seconds


def test_repeated_poll_cap_allows_ten_minute_follow_up_window() -> None:
    assert MAX_REPEAT_POLL_SECONDS == 600.0
    assert bounded_repeat_poll_seconds(600, default_seconds=25) == 600
    assert bounded_repeat_poll_seconds(900, default_seconds=25) == 600.0
    assert bounded_repeat_poll_seconds(None, default_seconds=25) == 25
