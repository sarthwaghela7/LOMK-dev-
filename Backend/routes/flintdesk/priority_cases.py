"""Priority Cases API: stored RFE / I-130 / I-485 / H4 / H4EAD classifications for the Calls-page records in a date range.

Read-only and database-backed. It never calls the model and has no way to start an analysis; the background worker and
ops/backfill_priority_cases.py do that. Access: Admin, Team Lead and Subject Matter Expert (checked here, not only in the UI).
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from middleware.auth import require_fd_priority_cases
from routes.calls import _reject_oversized_date_range
from services.priority_cases import FORMS, HIGH_PRIORITY_FORMS, OTHER_PRIORITY_FORMS
from services.priority_cases_read import RESULT_STATUSES, ROW_LIMIT_PER_FORM, coverage_summary, priority_cases
from storage.db import get_db

router = APIRouter(prefix="/flintdesk/priority-cases", tags=["FlintDesk Priority Cases"])


def _forms_for(form_type: Optional[str]) -> tuple[str, ...]:
    value = (form_type or "all").strip()
    if value.lower() in {"", "all"}:
        return FORMS
    by_key = {name.lower().replace("-", ""): name for name in FORMS}
    form = by_key.get(value.lower().replace("-", "").replace(" ", ""))
    if form is None:
        raise HTTPException(status_code=422, detail=f"form_type must be 'all' or one of: {', '.join(FORMS)}")
    return (form,)


@router.get("")
def get_priority_cases(
    start_date: date = Query(...),
    end_date: date = Query(...),
    form_type: Optional[str] = Query("all"),
    result: str = Query("confirmed", description="confirmed (default) or uncertain, for reviewers"),
    user=Depends(require_fd_priority_cases),
    db: Session = Depends(get_db),
):
    _reject_oversized_date_range(start_date, end_date)
    forms = _forms_for(form_type)
    if result not in RESULT_STATUSES:
        raise HTTPException(status_code=422, detail="result must be 'confirmed' or 'uncertain'")
    found = priority_cases(db, start=start_date, end=end_date, form_types=forms, status=result)
    return {
        "filters": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
                    "form_type": forms[0] if len(forms) == 1 else "all", "result": result},
        "groups": {"high": [f for f in HIGH_PRIORITY_FORMS if f in forms],
                   "other": [f for f in OTHER_PRIORITY_FORMS if f in forms]},
        "limit_per_form": ROW_LIMIT_PER_FORM,
        "forms": found,
        "coverage": coverage_summary(db, start=start_date, end=end_date),
    }
