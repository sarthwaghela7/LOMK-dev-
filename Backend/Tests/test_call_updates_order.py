from services.call_updates import AGENT_DISPLAY_ORDER, order_agents


def _agents(*names):
    return [{"id": str(i), "name": name} for i, name in enumerate(names)]


def test_agents_follow_the_business_order_and_everyone_else_keeps_their_place_after_them():
    shuffled = _agents("Edgin", "Zed", "Gracy", "Shrishti", "Anna", "Priyanka K", "Christobel", "Gokul")
    assert [a["name"] for a in order_agents(shuffled)] == ["Shrishti", "Priyanka K", "Gokul", "Gracy", "Christobel", "Edgin", "Zed", "Anna"]
    assert AGENT_DISPLAY_ORDER[0] == "shrishti"


def test_ordering_is_case_insensitive_and_does_not_change_the_input():
    original = _agents("edgin", "SHRISHTI")
    assert [a["name"] for a in order_agents(original)] == ["SHRISHTI", "edgin"]
    assert [a["name"] for a in original] == ["edgin", "SHRISHTI"]
