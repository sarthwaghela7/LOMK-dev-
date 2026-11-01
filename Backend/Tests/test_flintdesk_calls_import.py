"""/calls/import: Call_ID is the only truly required column (it's unique per
call). Date and Client Phone Number are optional verification-only columns --
if a row includes either, it must match the target call's actual recorded
date/number or the row is skipped and reported, guarding against pasting
into the wrong row from a wide Excel sheet. Neither is ever written anywhere.
See routes/flintdesk/calls.py's IMPORT_VERIFICATION_FIELDS.
"""
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from routes.flintdesk.calls import CallImport, import_calls

AGENT_USER = {"role": "agent", "fd_user_id": "agent-1", "name": "Agent"}


def _fake_call(
    call_date=date(2026, 9, 18), src="3125550100", dst="6304452050",
    call_id="11111111-1111-1111-1111-111111111111", call_reference="AF#147",
):
    return SimpleNamespace(
        call_id=call_id,
        call_reference=call_reference,
        call_date=call_date,
        src=src,
        dst=dst,
    )


def _run_import(records, call=None):
    call = call or _fake_call()
    with patch("routes.flintdesk.calls._get_call_or_404", return_value=call), \
         patch("routes.flintdesk.calls.update_flintdesk_manual_fields") as updater:
        result = import_calls(CallImport(records=records), user=AGENT_USER, db=None)
    return result, updater


def test_row_with_no_date_or_phone_imports_normally():
    result, updater = _run_import([{"call_id": "AF#147", "fd_a_case_type": "Divorce"}])
    assert result["imported"] == 1
    assert result["invalid_fields"] == []
    assert result["updated_records"] == [{
        "call_id": "11111111-1111-1111-1111-111111111111",
        "call_reference": "AF#147",
        "updated_fields": ["fd_a_case_type"],
    }]
    updater.assert_called_once()


def test_row_with_matching_date_and_phone_imports():
    records = [{
        "call_id": "AF#147", "import_call_date": "09-18-2026",
        "import_client_phone": "3125550100", "fd_a_case_type": "Divorce",
    }]
    result, updater = _run_import(records)
    assert result["imported"] == 1
    assert result["invalid_fields"] == []
    updater.assert_called_once()


def test_matching_date_accepts_excel_style_slashes():
    # Excel/Sheets commonly copies dates with slashes -- a straight paste
    # shouldn't fail the Date safety-check on formatting alone.
    records = [{"call_id": "AF#147", "import_call_date": "09/18/2026", "fd_a_case_type": "Divorce"}]
    result, updater = _run_import(records)
    assert result["imported"] == 1
    assert result["invalid_fields"] == []
    updater.assert_called_once()


def test_matching_date_accepts_the_calls_export_datetime_format():
    result, updater = _run_import([
        {"call_id": "AF#147", "import_call_date": "09/18/26 12:00 AM", "fd_a_case_type": "Divorce"}
    ])
    assert result["imported"] == 1
    updater.assert_called_once()


@pytest.mark.parametrize("phone", ["3125550100", "+13125550100", "6304452050"])
def test_matching_phone_accepts_either_call_direction_and_country_code(phone):
    records = [{"call_id": "AF#147", "import_client_phone": phone, "fd_a_case_type": "Divorce"}]
    result, updater = _run_import(records)
    assert result["imported"] == 1
    updater.assert_called_once()


def test_mismatched_date_skips_row_and_does_not_write_anything():
    records = [{
        "call_id": "AF#147", "import_call_date": "01-01-2020", "fd_a_case_type": "Divorce",
    }]
    result, updater = _run_import(records)
    assert result["imported"] == 0
    assert len(result["invalid_fields"]) == 1
    assert result["invalid_fields"][0]["field"] == "Date"
    updater.assert_not_called()


def test_mismatched_phone_skips_row_and_does_not_write_anything():
    records = [{
        "call_id": "AF#147", "import_client_phone": "9998887777", "fd_a_case_type": "Divorce",
    }]
    result, updater = _run_import(records)
    assert result["imported"] == 0
    assert len(result["invalid_fields"]) == 1
    assert result["invalid_fields"][0]["field"] == "Client Phone Number"
    updater.assert_not_called()


def test_row_with_only_date_and_phone_and_no_real_field_is_skipped_empty():
    records = [{"call_id": "AF#147", "import_call_date": "09-18-2026", "import_client_phone": "3125550100"}]
    result, updater = _run_import(records)
    assert result["imported"] == 0
    assert result["skipped_empty"] == 1
    assert result["invalid_fields"] == []
    updater.assert_not_called()


def test_invalid_date_format_is_reported_and_row_skipped():
    records = [{"call_id": "AF#147", "import_call_date": "not-a-date", "fd_a_case_type": "Divorce"}]
    result, updater = _run_import(records)
    assert result["imported"] == 0
    assert result["invalid_fields"][0]["field"] == "Date"
    updater.assert_not_called()


def _run_import_with_creation(records, created_call=None):
    """Like _run_import, but also mocks _create_manual_call so a row with no
    Call ID can be exercised without a real database.
    """
    created_call = created_call or _fake_call(call_id="22222222-2222-2222-2222-222222222222", call_reference="AF#500")
    with patch("routes.flintdesk.calls._create_manual_call", return_value=created_call) as creator, \
         patch("routes.flintdesk.calls.update_flintdesk_manual_fields") as updater:
        result = import_calls(CallImport(records=records), user=AGENT_USER, db=None)
    return result, creator, updater


def test_row_with_no_call_id_creates_a_new_call():
    records = [{
        "import_call_date": "09-18-2026", "import_client_phone": "3125550100",
        "fd_a_case_type": "Divorce",
    }]
    result, creator, updater = _run_import_with_creation(records)
    creator.assert_called_once_with(None, date(2026, 9, 18), "3125550100")
    updater.assert_called_once()
    assert updater.call_args.kwargs["call_id"] == "22222222-2222-2222-2222-222222222222"
    assert result["imported"] == 1
    assert result["invalid_fields"] == []
    assert result["updated_records"] == [{
        "call_id": "22222222-2222-2222-2222-222222222222",
        "call_reference": "AF#500",
        "updated_fields": ["fd_a_case_type"],
    }]


def test_row_with_no_call_id_and_no_phone_or_date_still_creates_a_new_call():
    # A client with no phone number on file: the pasted row has neither a
    # Call ID nor a Client Phone Number, only real Stage 1-4 field data.
    records = [{"fd_a_case_type": "Divorce"}]
    result, creator, updater = _run_import_with_creation(records)
    creator.assert_called_once_with(None, None, None)
    updater.assert_called_once()
    assert result["imported"] == 1


def test_row_with_no_call_id_and_only_verification_columns_is_skipped_empty():
    records = [{"import_call_date": "09-18-2026", "import_client_phone": "3125550100"}]
    result, creator, updater = _run_import_with_creation(records)
    assert result["imported"] == 0
    assert result["skipped_empty"] == 1
    assert result["invalid_fields"] == []
    creator.assert_not_called()
    updater.assert_not_called()


def test_row_with_no_call_id_and_invalid_field_does_not_create_a_call():
    # A row that fails validation before the point of creation must not
    # leave behind an empty, dataless Call row -- see _create_manual_call's
    # call site in import_calls for why creation is deferred this late.
    records = [{"fd_m_follow_up_attempts": "not-a-number"}]
    result, creator, updater = _run_import_with_creation(records)
    assert result["imported"] == 0
    assert result["invalid_fields"][0]["field"] == "fd_m_follow_up_attempts"
    creator.assert_not_called()
    updater.assert_not_called()


def test_row_with_no_call_id_and_invalid_pasted_date_does_not_create_a_call():
    records = [{"import_call_date": "not-a-date", "fd_a_case_type": "Divorce"}]
    result, creator, updater = _run_import_with_creation(records)
    assert result["imported"] == 0
    assert result["invalid_fields"][0]["field"] == "Date"
    creator.assert_not_called()
    updater.assert_not_called()
