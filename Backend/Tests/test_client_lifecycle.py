from datetime import date
from types import SimpleNamespace

from services.client_lifecycle import caller_phone_number, normalize_phone_number, resolve_client


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value


class _Query:
    def __init__(self, db):
        self.db = db

    def filter(self, *_args):
        return self

    def with_for_update(self):
        return self

    def one(self):
        return self.db.client

    def all(self):
        return self.db.filters


class _LifecycleDB:
    """Small database double for lifecycle unit tests (no live DB required)."""
    def __init__(self, client=None, insert_result=None, filters=None):
        self.client = client
        self.insert_result = insert_result
        self.filters = filters or []
        self.sequence = 0

    def execute(self, statement):
        if "nextval" in str(statement):
            self.sequence += 1
            return _ScalarResult(self.sequence)
        return _ScalarResult(self.insert_result)

    def query(self, _model):
        return _Query(self)


def test_phone_formats_share_one_canonical_value():
    values = ["+91 9876543210", "+919876543210", "91-9876543210", "9876543210"]
    assert {normalize_phone_number(value) for value in values} == {"9876543210"}


def test_us_country_prefix_is_normalized_without_accepting_extensions():
    assert normalize_phone_number("+1 (312) 878-1255") == "3128781255"
    assert normalize_phone_number("101") is None


def test_first_and_repeat_calls_keep_a_permanent_client_id_and_update_metadata():
    first_db = _LifecycleDB(
        client=SimpleNamespace(client_id="CL-000001"),
        insert_result="CL-000001",
    )
    first = resolve_client(first_db, "9876543210", date(2026, 8, 25))
    assert first.client_id == "CL-000001"

    existing = SimpleNamespace(
        client_id="CL-000001",
        phone_number="9876543210",
        first_call_date=date(2026, 8, 25),
        last_call_date=date(2026, 8, 25),
        total_calls=1,
        client_status="Closed",
        updated_at=None,
    )
    repeat_db = _LifecycleDB(client=existing, insert_result=None)
    repeat = resolve_client(repeat_db, "+91 9876543210", date(2026, 8, 26))
    assert repeat.client_id == "CL-000001"
    assert repeat.first_call_date == date(2026, 8, 25)
    assert repeat.last_call_date == date(2026, 8, 26)
    assert repeat.total_calls == 2
    assert repeat.client_status == "Reopened"


def test_external_party_is_selected_as_the_client_number():
    db = _LifecycleDB(filters=[("3128781255",)])
    assert caller_phone_number(db, "+1 312-878-1255", "9876543210") == "9876543210"
