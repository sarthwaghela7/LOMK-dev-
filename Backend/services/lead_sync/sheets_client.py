"""Read-only Google Sheets client with timeouts and bounded retries."""
import json
import logging
import random
import time
from urllib.parse import quote

logger = logging.getLogger("autoflint.lead_sync")

SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
API = "https://sheets.googleapis.com/v4/spreadsheets"
RETRY_STATUSES = {429, 500, 502, 503, 504}


class SheetsError(Exception):
    """A sheet read failed; the message never contains credentials."""


class SheetsClient:
    def __init__(self, service_account_json, sheet_id, worksheet, *, timeout=30, max_attempts=4, sleep=time.sleep):
        self._info_json, self.sheet_id, self.worksheet = service_account_json, sheet_id, worksheet
        self.timeout, self.max_attempts, self._sleep = timeout, max_attempts, sleep
        self._session = None

    def _authorised_session(self):
        if self._session is None:
            from google.auth.transport.requests import AuthorizedSession
            from google.oauth2.service_account import Credentials
            try:
                info = json.loads(self._info_json)
                creds = Credentials.from_service_account_info(info, scopes=[SCOPE])
            except Exception as exc:  # never echo the key material
                raise SheetsError(f"GOOGLE_SERVICE_ACCOUNT_JSON is not a valid service-account key ({type(exc).__name__})") from None
            self._session = AuthorizedSession(creds)
        return self._session

    def fetch_rows(self):
        """Return every used row of the worksheet as displayed text (header first)."""
        # A1 notation: quote the tab name (a space needs it) and double any
        # apostrophe; the URL path then percent-encodes it ("All Leads" ->
        # %27All%20Leads%27). Only this one tab is requested.
        a1_tab = "'" + self.worksheet.replace("'", "''") + "'"
        url = f"{API}/{quote(self.sheet_id, safe='')}/values/{quote(a1_tab, safe='')}"
        params = {"majorDimension": "ROWS", "valueRenderOption": "FORMATTED_VALUE"}
        last = "unknown error"
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self._authorised_session().get(url, params=params, timeout=self.timeout)
            except SheetsError:
                raise
            except Exception as exc:  # timeouts, connection resets, token refresh failures
                last = type(exc).__name__
            else:
                if response.status_code == 200:
                    return response.json().get("values", [])
                last = f"HTTP {response.status_code}"
                if response.status_code not in RETRY_STATUSES:
                    hint = " (check the worksheet name and that the sheet is shared with the service account)" if response.status_code in (400, 403, 404) else ""
                    raise SheetsError(f"Google Sheets read failed: {last}{hint}")
                retry_after = response.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    self._sleep(min(int(retry_after), 60))
                    continue
            if attempt < self.max_attempts:
                delay = min(2 ** (attempt - 1), 30) + random.uniform(0, 0.5)
                logger.warning("[lead_sync] transient Google error (%s); retry %s/%s in %.1fs", last, attempt, self.max_attempts - 1, delay)
                self._sleep(delay)
        raise SheetsError(f"Google Sheets read failed after {self.max_attempts} attempts: {last}")
