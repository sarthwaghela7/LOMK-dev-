from threading import Event, Thread

from utils.ttl_cache import coalesced_read, invalidate_read_caches


def test_read_started_before_import_cannot_repopulate_current_cache():
    invalidate_read_caches()
    started, finish = Event(), Event()
    state = {"value": "old"}

    @coalesced_read("import_regression", ttl_seconds=10)
    def read():
        value = state["value"]
        if value == "old":
            started.set()
            assert finish.wait(5)
        return value

    thread = Thread(target=read)
    thread.start()
    assert started.wait(5)
    state["value"] = "updated"
    invalidate_read_caches()
    assert read() == "updated"
    finish.set()
    thread.join(5)
    assert not thread.is_alive()
    assert read() == "updated"
