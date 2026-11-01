from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from middleware.auth import get_current_user
from services import follow_up_dates as fud


def test_param_accepts_only_real_iso_dates():
    assert fud.parse_follow_up_date_param("2026-10-12") == date(2026, 10, 12)
    assert fud.parse_follow_up_date_param(None) is None
    assert fud.parse_follow_up_date_param("  ") is None
    for bad in ("2026-02-30", "10-12-2026", "10/12/2026", "2026-13-01", "abc", "2026-10-12T00:00:00Z"):
        with pytest.raises(HTTPException) as exc:
            fud.parse_follow_up_date_param(bad)
        assert exc.value.status_code == 422


def test_selected_date_is_matched_in_every_stored_format():
    assert fud.stored_forms(date(2026, 10, 2)) == ["10-02-2026", "10/02/2026", "2026-10-02"]


def test_entries_ignore_non_dates_and_dedupe_and_sort():
    raw = ["10-12-2026", "N/A", "2", "NA", "", None, "10/12/2026", "2026-10-03", "02-30-2026", "13-01-2026", "10-12-2026 ", "free text"]
    assert fud.parse_entries(raw) == [date(2026, 10, 3), date(2026, 10, 12)]


def test_predicate_is_a_parameterised_array_overlap_with_no_date_cast():
    sql = str(fud.has_follow_up_date(date(2026, 10, 12)).compile(dialect=postgresql.dialect(), compile_kwargs={"render_postcompile": True}))
    assert "&&" in sql and "regexp_split_to_array" in sql
    assert "10-12-2026" not in sql  # values are bound parameters, never interpolated
    assert "to_date" not in sql.lower() and "::date" not in sql.lower()


def test_analysis_read_accepts_the_new_filters():
    import inspect
    from services.analysis_read_query import analysis_read
    params = inspect.signature(analysis_read).parameters
    assert params["follow_up_date"].default is None and params["follow_up_dates_only"].default is False


def test_routes_require_login_and_validate_date():
    from routes import calls
    by_path = {route.path: route for route in calls.router.routes}
    for path in ("/calls/analysis", "/calls/analysis/follow-up-dates"):
        route = by_path[path]
        assert get_current_user in [dep.call for dep in route.dependant.dependencies]
    names = {param.name for param in by_path["/calls/analysis"].dependant.query_params}
    assert "follow_up_date" in names
    assert "follow_up_date" not in {param.name for param in by_path["/calls/analysis/follow-up-dates"].dependant.query_params}
