"""A failed Vitel request must never put the account name or password into a log line, the raised error or its traceback."""
import logging
import traceback
from datetime import date

import pytest
import requests

from services.Database_sync import f1
from services.Database_sync.f1 import F1Processor, redact_vitel_secrets

SECRET = "s3cret-Pa55&word"
URL = "https://billing.example.test/vitelglobal_callrecords.php?username=acct-name&password=s3cret-Pa55&line=All&startdate=2026-10-25&enddate=2026-10-26"


def _processor():
    processor = F1Processor.__new__(F1Processor)
    processor.username, processor.password, processor.line, processor.base_url = "acct-name", "s3cret-Pa55", "All", "https://billing.example.test/x.php"
    return processor


def test_redaction_masks_the_credentials_and_keeps_the_rest():
    masked = redact_vitel_secrets(f"404 Client Error: Not Found for url: {URL}")
    assert "acct-name" not in masked and "s3cret-Pa55" not in masked
    assert "username=***&password=***&line=All&startdate=2026-10-25&enddate=2026-10-26" in masked
    assert redact_vitel_secrets("no secrets here") == "no secrets here"
    assert "s3cret" not in redact_vitel_secrets(f"Max retries exceeded with url: /x.php?password=s3cret-Pa55 (Caused by X)")


def _everything_printed(caplog, error):
    return caplog.text + str(error) + "".join(traceback.format_exception(error)) + repr(error.args)


def test_an_http_error_is_logged_and_raised_without_the_credentials(monkeypatch, caplog):
    def fake_get(*args, **kwargs):
        response = requests.Response()
        response.status_code, response.url, response.reason = 404, URL, "Not Found"
        return response
    monkeypatch.setattr(f1.requests, "get", fake_get)
    with caplog.at_level(logging.ERROR), pytest.raises(requests.exceptions.HTTPError) as raised:
        _processor()._fetch_calls_for_date(date(2026, 10, 25))
    printed = _everything_printed(caplog, raised.value)
    assert "Not Found" in printed and "acct-name" not in printed and "s3cret-Pa55" not in printed


def test_a_chained_connection_error_does_not_leak_through_its_cause(monkeypatch, caplog):
    def fake_get(*args, **kwargs):
        try:
            raise OSError(f"Max retries exceeded with url: /x.php?username=acct-name&password=s3cret-Pa55&line=All")
        except OSError as cause:
            raise requests.exceptions.ConnectionError(f"HTTPSConnectionPool: {cause}") from cause
    monkeypatch.setattr(f1.requests, "get", fake_get)
    with caplog.at_level(logging.ERROR), pytest.raises(requests.exceptions.ConnectionError) as raised:
        _processor()._fetch_calls_for_date(date(2026, 10, 25))
    printed = _everything_printed(caplog, raised.value)
    assert "Max retries exceeded" in printed and "acct-name" not in printed and "s3cret-Pa55" not in printed
