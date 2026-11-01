"""A Lead number filter must include the lead's own ring route (601/603/605), for every agent.

Calls to a lead line that an agent extension answers are logged with dst = the ring route. Without it,
"Lead number + Extension" undercounted every agent compared with the Agent filter.
"""
import pytest

from routes.calls import with_lead_ring_route
from services.hannah_ai_routing import LEAD_RING_ROUTES


def test_each_lead_has_its_documented_ring_route():
    # Migration 060: "605 routes 2243939098, the same way 601 routes 3128781255 and 603 routes 2243374447".
    assert LEAD_RING_ROUTES == {"3128781255": "601", "2243374447": "603", "2243939098": "605"}


@pytest.mark.parametrize("lead,route", list(LEAD_RING_ROUTES.items()))
def test_filter_mapping_includes_the_ring_route(lead, route):
    base = {lead: {"201", "202"}}
    merged = with_lead_ring_route(base, lead)
    assert route in merged[lead] and {"201", "202"} <= merged[lead]
    assert route not in base[lead]                                   # the shared cached mapping is never mutated


@pytest.mark.parametrize("variant", ["2243374447", "12243374447", "+1 (224) 337-4447"])
def test_number_formats_all_resolve_to_the_same_lead(variant):
    assert "603" in with_lead_ring_route({}, variant)["2243374447"]


def test_other_numbers_and_empty_input_are_unchanged():
    base = {"3128781255": {"101"}}
    assert with_lead_ring_route(base, "9999999999") is base
    assert with_lead_ring_route(base, None) is base
    assert with_lead_ring_route(base, "") is base


def test_the_calls_page_and_dashboard_filter_uses_it():
    import inspect
    from services import analysis_read_query
    source = inspect.getsource(analysis_read_query.analysis_read)
    assert "with_lead_ring_route" in source
