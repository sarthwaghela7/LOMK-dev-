"""The release runner bounds every migration with timeouts. transaction_timeout is PostgreSQL 17+, so it is only set there."""
import pytest

from migrations.runner import run_migrations


class _Result:
    def __init__(self, value=None):
        self.value = value

    def scalar(self):
        return self.value

    def scalars(self):
        return iter(())


class _Connection:
    def __init__(self, server_version_num):
        self.version, self.statements = server_version_num, []

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        self.statements.append(sql)
        if "server_version_num" in sql:
            return _Result(self.version)
        if "pg_try_advisory_xact_lock" in sql:
            return _Result(True)
        return _Result()


class _Engine:
    def __init__(self, version):
        self.connection = _Connection(version)

    def begin(self):
        engine = self

        class _Context:
            def __enter__(self):
                return engine.connection

            def __exit__(self, *exc):
                return False
        return _Context()


@pytest.mark.parametrize("version,expected", [(160015, False), (169999, False), (170000, True), (170002, True), (180001, True)])
def test_transaction_timeout_is_only_set_where_postgresql_supports_it(version, expected):
    engine = _Engine(version)
    run_migrations(engine, manifest=())
    sets = [s for s in engine.connection.statements if s.startswith("SET LOCAL")]
    assert ("SET LOCAL transaction_timeout = '120s'" in sets) is expected
    for always in ("SET LOCAL lock_timeout = '2s'", "SET LOCAL statement_timeout = '30s'", "SET LOCAL idle_in_transaction_session_timeout = '60s'"):
        assert always in sets                         # the other bounds never depend on the server version


def test_the_ledger_and_lock_are_still_used_on_every_version():
    engine = _Engine(160015)
    run_migrations(engine, manifest=())
    joined = " ".join(engine.connection.statements)
    assert "pg_try_advisory_xact_lock" in joined and "app_schema_migrations" in joined
