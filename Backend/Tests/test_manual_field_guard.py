import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.Database_sync.manual_fields import apply_model_fields


def test_model_output_does_not_erase_agent_name():
    analysis = SimpleNamespace(front_desk_person_name="Priyanka", call_summary=None, fd_m_blocker_comment="typed")
    apply_model_fields(analysis, {"front_desk_person_name": None, "call_summary": "new", "fd_m_blocker_comment": None})
    assert analysis.front_desk_person_name == "Priyanka"
    assert analysis.fd_m_blocker_comment == "typed"
    assert analysis.call_summary == "new"


def test_placeholder_or_blank_manual_value_can_be_filled():
    for existing in (None, "", "  ", "N/A", "-"):
        analysis = SimpleNamespace(front_desk_person_name=existing)
        apply_model_fields(analysis, {"front_desk_person_name": "Priyanka"})
        assert analysis.front_desk_person_name == "Priyanka"


def test_non_manual_fields_are_still_overwritten():
    analysis = SimpleNamespace(call_summary="old")
    apply_model_fields(analysis, {"call_summary": None})
    assert analysis.call_summary is None
