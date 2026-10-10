from agent_pbx.ui.navigation import NavigationDestination, NavigationHistory


def destination(entity: str, tab: str = "latest-tab") -> NavigationDestination:
    return NavigationDestination(entity, tab)


def test_navigation_history_uses_stable_destinations_and_deduplicates() -> None:
    history = NavigationHistory(limit=3)
    first = destination("agent-a")
    second = destination("operator-0", "campaigns-tab")

    assert history.record(first) is True
    assert history.record(first) is False
    assert history.record(second) is True
    assert history.current == second
    assert history.entries == (first, second)


def test_navigation_history_back_forward_and_branching() -> None:
    history = NavigationHistory()
    first = destination("agent-a")
    second = destination("operator-0")
    third = destination("operator-0", "joplin-tab")
    replacement = destination("agent-b", "files-tab")
    for item in (first, second, third):
        history.record(item)

    assert history.back() == second
    assert history.back() == first
    assert history.forward() == second
    assert history.record(replacement) is True
    assert history.entries == (first, second, replacement)
    assert history.forward() is None


def test_navigation_history_is_bounded() -> None:
    history = NavigationHistory(limit=3)
    for index in range(5):
        history.record(destination(f"agent-{index}"))

    assert history.entries == (
        destination("agent-2"),
        destination("agent-3"),
        destination("agent-4"),
    )
    assert history.index == 2
