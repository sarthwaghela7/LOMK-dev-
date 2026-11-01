from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, case, cast, func, or_
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Session

from middleware.auth import get_current_user
from storage.db import get_db
from storage.models import Call, CallAnalysisResult
from services.read_scope import assignment_agent_id, dashboard_scope, lower
from utils.ttl_cache import coalesced_read

router = APIRouter(prefix="/api/dashboard", tags=["FlintDesk Dashboard"])


@router.get("/summary")
@coalesced_read("dashboard_summary_v1", ttl_seconds=45, exclude={"db", "user"})
def get_dashboard_summary(start_date: date, end_date: date, agent: str = "all",
                          number: str = "", extension: str = "",
                          user=Depends(get_current_user), db: Session = Depends(get_db)):
    if end_date < start_date or (end_date - start_date).days > 92:
        raise HTTPException(400, "Date range must be ordered and 92 days or fewer")
    from services.dashboard_summary import build_summary
    return build_summary(db, user, start_date, end_date, agent, number, extension)


@router.get("/open-call-aging")
@coalesced_read("dashboard_open_call_aging_v1", ttl_seconds=45, exclude={"db", "user"})
def get_open_call_aging(start_date: date, end_date: date, agent: str = "all",
                        number: str = "", extension: str = "",
                        user=Depends(get_current_user), db: Session = Depends(get_db)):
    """The four Open Call Aging cards (+ the per-agent context lines). Separate from /summary so a slow read here never
    delays the rest of the Dashboard."""
    if end_date < start_date or (end_date - start_date).days > 92:
        raise HTTPException(400, "Date range must be ordered and 92 days or fewer")
    from services.dashboard_summary import open_call_aging_payload
    return open_call_aging_payload(db, start=start_date, end=end_date, agent=agent, number=number, extension=extension)


@router.get("/case-lifecycle")
@coalesced_read("dashboard_case_lifecycle_v1", ttl_seconds=15, exclude={"db", "user"})
def get_case_lifecycle(start_date: date, end_date: date, agent: str = "all",
                       number: str = "", extension: str = "",
                       user=Depends(get_current_user), db: Session = Depends(get_db)):
    """The five Case Lifecycle Stages cards: every call record counted once, same filters as the Calls page."""
    if end_date < start_date or (end_date - start_date).days > 92:
        raise HTTPException(400, "Date range must be ordered and 92 days or fewer")
    from services.case_lifecycle import lifecycle_counts
    return lifecycle_counts(db, start=start_date, end=end_date, agent=agent, number=number, extension=extension)


@router.get("/case-types-eq")
@coalesced_read("dashboard_case_types_eq_v1", ttl_seconds=45, exclude={"db", "user"})
def get_case_types_eq(start_date: date, end_date: date,
                      user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Case Types + EQ rows only; avoids the full dashboard summary's reads."""
    if end_date < start_date or (end_date - start_date).days > 92:
        raise HTTPException(400, "Date range must be ordered and 92 days or fewer")
    from services.dashboard_summary import build_case_types_eq
    return build_case_types_eq(db, user, start_date, end_date)


@router.get("/case-types-trends")
@coalesced_read("dashboard_case_types_trends_v1", ttl_seconds=30, exclude={"db", "user"})
def get_case_types_trends(period: str = "daily", anchor: date | None = None,
                          start_date: date | None = None, end_date: date | None = None,
                          user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Verified Case Types + EQ trend report for a Daily / Weekly / Monthly / Custom Central-time period.

    `anchor` is any date inside the wanted period; omitted = today in America/Chicago.
    `start_date`/`end_date` apply to period=custom only.
    """
    from services.case_type_trends import PeriodError, cached_report
    try:
        return cached_report(db, period, anchor, (start_date, end_date) if period == "custom" else None)
    except PeriodError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/case-types-trends/questions")
@coalesced_read("dashboard_case_types_questions_v1", ttl_seconds=30, exclude={"db", "user"})
def get_case_types_trend_questions(period: str = "daily", anchor: date | None = None,
                                   start_date: date | None = None, end_date: date | None = None,
                                   case_type: str | None = None, question: str | None = None,
                                   user=Depends(get_current_user), db: Session = Depends(get_db)):
    """The actual enquiry questions (one per call: date, call reference, case type, text) behind a Trends figure.

    Same period, records and rules as `/case-types-trends`. `case_type` / `question` narrow it to one bar or one
    repeated question; omit both for every enquiry in the period (used by the PDF). No transcripts are returned.
    """
    from services.case_type_trends import PeriodError, list_questions
    try:
        return list_questions(db, period, anchor, (start_date, end_date) if period == "custom" else None,
                              case_type=case_type, question=question)
    except PeriodError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/case-types-trends/insights")
def get_case_types_trend_insights(period: str = "daily", anchor: date | None = None,
                                  start_date: date | None = None, end_date: date | None = None,
                                  user=Depends(get_current_user), db: Session = Depends(get_db)):
    """AI wording of the verified report for the same period; never blocks the charts (always HTTP 200)."""
    from services.case_type_trends import InsightsUnavailable, PeriodError, cached_report, generate_insights
    try:
        report = cached_report(db, period, anchor, (start_date, end_date) if period == "custom" else None)
    except PeriodError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.rollback()  # release the connection before any model request
    try:
        return {"available": True, **generate_insights(report)}
    except InsightsUnavailable as exc:
        return {"available": False, "snapshot_hash": report["snapshot_hash"], "message": str(exc)}


@router.get("/time-duration")
def get_time_duration(
    start_date: date,
    end_date: date,
    agent_ids: list[UUID] | None = Query(None),
    _user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return one aggregate row, never materialize calls or analysis payloads."""
    if end_date < start_date:
        raise HTTPException(422, "end_date must be on or after start_date")
    if (end_date - start_date).days > 92:
        raise HTTPException(400, "Date range must be 92 days or fewer")
    voicemail = or_(
        lower(CallAnalysisResult.voicemail_left_if_no_answer).in_(("yes", "true", "1")),
        lower(CallAnalysisResult.fd_a_call_outcome).contains("voicemail"),
    )
    duration = func.greatest(func.coalesce(Call.billsec, 0), 0)
    query = db.query(
        func.coalesce(func.sum(case((~voicemail, duration), else_=0)), 0),
        func.coalesce(func.sum(case((voicemail, duration), else_=0)), 0),
        func.count(case((and_(~voicemail, Call.billsec >= 0), 1))),
    ).select_from(Call).outerjoin(
        CallAnalysisResult, Call.call_id == cast(CallAnalysisResult.call_id, PGUUID)
    ).filter(Call.call_date.between(start_date, end_date), dashboard_scope())
    if agent_ids:
        query = query.filter(assignment_agent_id().in_(agent_ids))
    total, voicemail_total, count = query.one()
    return {
        "totalCallDurationSeconds": int(total),
        "voicemailCallDurationSeconds": int(voicemail_total),
        "qualifyingCallCount": int(count),
    }
