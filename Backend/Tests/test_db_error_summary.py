from types import SimpleNamespace

from services.analysis_jobs import db_error_summary


class IntegrityError(Exception):
    def __init__(self, orig):
        super().__init__("could not insert: (secret client value)")
        self.orig = orig


def test_reports_where_the_database_rejected_the_write_and_nothing_else():
    orig = SimpleNamespace(pgcode="23505", diag=SimpleNamespace(
        table_name="call_analysis_results", constraint_name="call_analysis_results_call_id_key", column_name=None,
        message_detail="Key (call_id)=(secret) already exists."))
    text = db_error_summary(IntegrityError(orig))
    assert text == "IntegrityError [23505] table=call_analysis_results constraint=call_analysis_results_call_id_key"
    assert "secret" not in text


def test_plain_errors_show_only_their_class():
    assert db_error_summary(ValueError("boom with data")) == "ValueError"


def test_missing_diag_still_gives_the_code():
    assert db_error_summary(IntegrityError(SimpleNamespace(pgcode="23502", diag=None))) == "IntegrityError [23502]"
