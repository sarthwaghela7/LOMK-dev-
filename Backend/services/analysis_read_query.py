"""SQL-side Analysis filters, canonical-call selection, counts and pagination."""
import re

from sqlalchemy import String, and_, case, cast, func, literal, or_, select
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import aliased, defer, load_only

from storage.models import Call, CallAnalysisResult as Analysis, FDCaseClosureLink, FDLead, FDUser
from services.follow_up_dates import DATE_LIKE_REGEX, date_like_entries, has_follow_up_date, parse_entries
from services.read_scope import (
    analysis_date, assignment_agent_id, dashboard_scope, digits, inbound_agent_answered, is_inbound_call, lower,
    normalized_number, unanswered_disposition,
)


# Case Status Change values that close a case (stored spellings, lowercased).
# Open Call Aging cards (3+/5+/10+/15+): a call counts when its Case Status Change is blank or one of these (lower-case).
AGING_STATUSES = ("open", "client side delay", "blocker", "dropped", "same client context")
OPEN_CASE_TERMINAL_STATUSES = ("dropped", "success ge", "success pc", "success - ge", "success - pc")   # new and legacy spellings


MAX_CALL_ID_TERMS = 50


def call_id_terms(value):
    """The call-id filter as lowercase search terms. A comma separates several ids (e.g. all calls changed by one
    closure confirmation); a single id behaves exactly as before. Ids never contain commas."""
    terms = [part.strip().lower() for part in str(value or "").split(",")]
    return [term for term in terms if term][:MAX_CALL_ID_TERMS]


def analysis_read(db, *, start, end, dashboard=False, number=None, extension=None,
                  agent=None, call_direction=None, client_id=None, client_number=None,
                  call_id=None, lead_id=None, call_kind=None, review_status=None,
                  target_page="process_calls", page=None, page_size=None,
                  call_source=False, only_unanalyzed=False, cursor=None, cursor_mode=False, cursor_output=None, summary_rows=False,
                  open_cases=False, aging_open=False, aging_dependency=None, aging_days=None, priority_form=None, action_status=None, case_status=None, lifecycle_stage=None, follow_up_date=None, follow_up_dates_only=False, ids_only=False, lifecycle_counts=False):
    from utils.page_cursor import scope_key, encode_cursor, decode_cursor
    scope = scope_key({key: value for key, value in locals().copy().items()
                       if key not in {"db", "cursor", "cursor_output", "page", "scope_key", "encode_cursor", "decode_cursor"}})
    if lifecycle_stage == "open":
        # The "Open Cases" button / card is the status-based population (blank, Open, Blocker, Same Client Context, Client Side
        # Delay, Dropped), the same one the Open Call Aging cards use - not the stored lifecycle stage.
        aging_open, lifecycle_stage = True, None
    from routes.calls import (
        _MANUAL_CALL_FIELDS, _canonical_agent_names, _extensions_by_parent_number,
        _hannah_ai_routing_leg_ids, _missed_call_numbers, _normalize_routing_number,
        _resolve_canonical_agent_name, _system_numbers, with_lead_ring_route,
    )
    effective_date = Call.call_date if call_source else analysis_date(Analysis)
    join = Call.call_id == cast(Analysis.call_id, PGUUID)
    identity = Call.call_id if call_source else Analysis.id
    base = db.query(identity.label("id"), effective_date.label("effective_date"), Call.call_id.label("call_id"))
    base = base.select_from(Call).outerjoin(Analysis, join) if call_source else base.select_from(Analysis).outerjoin(Call, join)
    base = base.filter(effective_date.between(start, end))
    if not call_source:
        base = base.filter(or_(Analysis.call_recording_date_normalized.between(start, end), Analysis.call_recording_date_normalized.is_(None)))
    needles = call_id_terms(call_id)
    if needles:
        base = base.filter(Call.call_date.between(start, end), or_(*[or_(
            func.lower(Call.call_reference).contains(needle, autoescape=True),
            cast(Call.call_number, String).contains(needle.removeprefix("af#"), autoescape=True),
            cast(Call.call_id, String).contains(needle, autoescape=True)) for needle in needles]))

    # Session correlation remains the established shared algorithm. Only six
    # small CDR fields cross the wire; transcripts/JSON are never selected.
    # All final filtering/counting/pagination below still happens in SQL.
    if target_page != "missed_calls":
        routing_rows = db.query(Call.call_id, Call.call_date, Call.call_time,
                                Call.src, Call.dst, Call.disposition).outerjoin(Analysis, join).filter(
            effective_date.between(start, end),
            # 605 added: its unanswered ring legs are also hidden (see
            # routes.calls._hannah_ai_routing_leg_ids).
            normalized_number(Call.dst).in_(("601", "602", "603", "604", "605", "2243860601")),
        ).all()
        excluded = _hannah_ai_routing_leg_ids(routing_rows)
        if excluded:
            base = base.filter(or_(Call.call_id.is_(None), Call.call_id.notin_(excluded)))
        missed = _missed_call_numbers(db)
        if missed:
            base = base.filter(~normalized_number(Call.dst).in_(missed))
        # Vitel's disposition is authoritative: an INBOUND leg nobody answered
        # (NO ANSWER / BUSY / FAILED) is not a normal call, on any destination
        # (ring route or direct line). The interaction, if it reached the AI
        # route, is shown once on Missed Calls. Outbound unanswered attempts are
        # deliberately untouched and keep showing as before.
        base = base.filter(or_(Call.call_id.is_(None), ~and_(is_inbound_call(Call), unanswered_disposition(Call))))
    if dashboard:
        base = base.filter(dashboard_scope())
    if number:
        expected = _normalize_routing_number(number)
        # The lead's own ring route (e.g. 603 for 2243374447) belongs to it, or answered calls disappear.
        mappings = with_lead_ring_route(_extensions_by_parent_number(db), number)
        base = base.filter(or_(normalized_number(Call.src) == expected,
            normalized_number(Call.dst) == expected,
            normalized_number(Call.dst).in_(mappings.get(expected, set()))))
    if extension:
        # Same ownership rule as the Agent filter: an inbound call belongs to
        # an extension only when that agent extension ANSWERED it.
        base = base.filter(lower(Call.extension) == extension.strip().lower(),
                           or_(~is_inbound_call(Call), inbound_agent_answered(Call)))
    if client_id:
        base = base.filter(lower(Call.client_id) == client_id.strip().lower())
    if client_number:
        needle = re.sub(r"\D", "", client_number)[-10:]
        base = base.filter(or_(func.right(digits(Call.src), 10) == needle,
                              func.right(digits(Call.dst), 10) == needle) if needle else literal(False))
    if lead_id and lead_id.strip():
        linked = select(FDLead.fd_a_leads_call_id).where(
            FDLead.fd_a_leads_call_id.isnot(None),
            func.lower(FDLead.fd_m_leads_lead_id).contains(lead_id.strip().lower(), autoescape=True))
        base = base.filter(Call.call_id.in_(linked))
    if call_kind:
        outcome = func.coalesce(Analysis.fd_a_call_outcome, "")
        # Vitel's own VOICEMAIL disposition is authoritative: it classifies the
        # call as a Voicemail Call directly from metadata, with no dependence
        # on the AI analysis. The analysis-based tests are kept for every
        # other record (e.g. outbound voicemail left).
        voicemail = or_(lower(Analysis.voicemail_left_if_no_answer).in_(("yes", "true", "1")),
                        lower(outcome).contains("voicemail"),
                        func.upper(func.regexp_replace(func.coalesce(Call.disposition, ""), "[\\s_-]+", "", "g")) == "VOICEMAIL")
        base = base.filter(voicemail if call_kind.lower() == "voicemail" else ~voicemail)
    if review_status:
        base = base.filter(func.coalesce(Analysis.case_closing_completed, False) == (review_status.lower() == "completed"))
    if call_direction:
        system = _system_numbers(db)
        outbound = normalized_number(Call.src).in_(system)
        inbound = and_(~outbound, normalized_number(Call.dst).in_(system))
        base = base.filter(outbound if call_direction.lower() == "outbound" else inbound)
    if agent:
        canonical = _canonical_agent_names(db)
        # Resolve only distinct short names, never full analysis records.
        names_query = db.query(Analysis.front_desk_person_name)
        if call_source:
            names_query = names_query.join(Call, join)
        names = names_query.filter(effective_date.between(start, end)).distinct().all()
        resolved = [(name, _resolve_canonical_agent_name(name or "", canonical)) for (name,) in names]
        resolved = [(name, value) for name, value in resolved if value]
        assignment = assignment_agent_id()
        registered = aliased(FDUser)
        assigned_name = select(registered.name).where(registered.id == assignment).correlate(Call).scalar_subquery()
        manual = case(*[(Analysis.front_desk_person_name == name, value) for name, value in resolved], else_=None) if resolved else literal(None)
        # Assigned extension owner first; typed names only claim unassigned
        # calls. Inbound has no typed-name override: the agent is whoever's
        # extension ANSWERED, straight from Vitel metadata.
        name = case((is_inbound_call(Call), func.coalesce(assigned_name, "-")),
                    else_=func.coalesce(assigned_name, manual, "-"))
        base = base.filter(lower(name) == agent.strip().lower())

    manual = or_(*(func.trim(func.coalesce(getattr(Analysis, field), "")) != "" for field in _MANUAL_CALL_FIELDS))
    has_audio = or_(func.coalesce(Call.audio_s3_uri, "") != "", func.coalesce(Call.audio_s3_key, "") != "")
    rank = func.row_number().over(
        partition_by=(Call.call_date, Call.call_time, func.trim(Call.src), func.trim(Call.dst),
                      case((Call.call_id.is_(None), Analysis.id))),
        order_by=(manual.desc(), has_audio.desc(), (lower(Call.processing_status) == "processed").desc(),
                  func.coalesce(Call.billsec, 0).desc(), func.coalesce(Call.call_number, 2**31).asc(), Analysis.id.asc()),
    ).label("rank")
    ranked = base.add_columns(rank).subquery()
    selected = db.query(ranked.c.id, ranked.c.effective_date).filter(ranked.c.rank == 1)
    if only_unanalyzed:
        # Audio, transcription and AI analysis arrive asynchronously. Keep
        # the source CDR visible immediately so a new extension never looks
        # like it made no calls while that work is still pending.
        # This must run AFTER the canonical-call ranking above: a duplicate
        # CDR leg of a call that already has an analysis would otherwise
        # become its own rank-1 "pending" row and be listed (and counted) a
        # second time.
        selected = selected.filter(~select(1).select_from(Analysis).where(
            cast(Analysis.call_id, PGUUID) == ranked.c.id,
        ).exists())
    # Stage filters decide on the canonical record chosen above (like
    # only_unanalyzed), so a call whose chosen record is closed is not counted
    # as open just because a duplicate record of it is not.
    if open_cases or aging_open or aging_dependency or priority_form or action_status or case_status or lifecycle_stage or follow_up_date or follow_up_dates_only:
        record = (cast(Analysis.call_id, PGUUID) == ranked.c.id) if call_source else (Analysis.id == ranked.c.id)
        if open_cases:
            # Open Cases: Stage 3 "Case Status Change" has not reached a terminal
            # value. Exclusion logic, so any other (or future) status is open, and
            # a call with no status yet (NULL/blank, or no analysis) is open too.
            # Compared trimmed and case-insensitively, like the app's other open/closed checks.
            # A log that confirmed another agent's closure of the same matter is closed too.
            inherited_closure = select(1).select_from(FDCaseClosureLink).where(
                FDCaseClosureLink.analysis_id == Analysis.id, FDCaseClosureLink.reverted_at.is_(None),
            ).correlate(Analysis).exists()
            selected = selected.filter(~select(1).select_from(Analysis).where(
                record,
                or_(func.lower(func.trim(func.coalesce(Analysis.fd_m_case_status_change, ""))).in_(OPEN_CASE_TERMINAL_STATUSES),
                    inherited_closure),
            ).exists())
        if priority_form:
            # Priority Cases page logic, unchanged: the call (any of its duplicate legs - same date, time, caller and callee) has a
            # COMPLETED priority analysis that confirmed this form (services/priority_cases_read.py). Stored results only; no model call.
            from sqlalchemy import any_, column as sa_column, table as sa_table
            from sqlalchemy.dialects.postgresql import ARRAY
            analysis_table = sa_table("call_priority_analysis", sa_column("call_id", PGUUID), sa_column("analysis_status", String),
                                      sa_column("confirmed_forms", ARRAY(String)))
            canon, member = aliased(Call), aliased(Call)
            owner = aliased(Analysis)
            canon_id = ranked.c.id if call_source else select(cast(owner.call_id, PGUUID)).where(owner.id == ranked.c.id).correlate(ranked).scalar_subquery()
            same_call = and_(member.call_date == canon.call_date, member.call_time == canon.call_time,
                             func.trim(func.coalesce(member.src, "")) == func.trim(func.coalesce(canon.src, "")),
                             func.trim(func.coalesce(member.dst, "")) == func.trim(func.coalesce(canon.dst, "")))
            selected = selected.filter(select(1).select_from(canon).join(member, same_call).join(analysis_table, analysis_table.c.call_id == member.call_id).where(
                canon.call_id == canon_id, analysis_table.c.analysis_status == "completed", literal(priority_form) == any_(analysis_table.c.confirmed_forms)).correlate(ranked).exists())
        if aging_dependency:
            # Dashboard Open Call Aging popup: the same population as the aging cards (aging_open), narrowed to the calls whose
            # saved AI answer (services/dependency_ai.py, never re-run here) says they wait on the selected agent ("agent") or on
            # other agents ("other_agent"), and aged at least `aging_days` Chicago calendar days. The Agent filter above is the
            # selected agent, so the rows are exactly what the card's figure counts.
            from datetime import timedelta
            from services.dependency_ai import META_KEY
            from services.open_call_aging import central_today
            aging_open = True
            selected = selected.filter(select(1).select_from(Analysis).where(
                record, func.jsonb_extract_path_text(Analysis.fd_stage2_ai_meta, META_KEY, "depends_on") == aging_dependency).exists())
            if aging_days:
                selected = selected.filter(ranked.c.effective_date <= central_today() - timedelta(days=int(aging_days)))
        if aging_open:
            # Open Call Aging population: Case Status Change blank (or no analysis yet) or one of AGING_STATUSES, and not a
            # log that confirmed another agent's closure. Any other status (In-Transition, Success, Wrong Number Dialed,
            # Lost, unknown) is excluded. One canonical record per call, so a call is never counted twice.
            closure_link = select(1).select_from(FDCaseClosureLink).where(
                FDCaseClosureLink.analysis_id == Analysis.id, FDCaseClosureLink.reverted_at.is_(None),
            ).correlate(Analysis).exists()
            status_key = func.lower(func.trim(func.coalesce(Analysis.fd_m_case_status_change, "")))
            selected = selected.filter(~select(1).select_from(Analysis).where(
                record, or_(and_(status_key != "", status_key.notin_(AGING_STATUSES)), closure_link)).exists())
        if case_status:
            # Stage 3 "Case Status Change": the saved value of the record chosen above, compared trimmed and
            # case-insensitively like the Open Cases rule. "__none__" = no status chosen yet (blank, or a call with no
            # analysis record at all), so it is "no record with a non-blank status".
            stored = func.trim(func.coalesce(Analysis.fd_m_case_status_change, ""))
            if case_status == "__none__":
                selected = selected.filter(~select(1).select_from(Analysis).where(record, stored != "").exists())
            else:
                from services.case_lifecycle import spellings_of
                selected = selected.filter(select(1).select_from(Analysis).where(
                    record, func.lower(stored).in_(spellings_of(case_status))).exists())
        if lifecycle_stage:
            # Case lifecycle stage of the record chosen above (the backend-persisted classification, migration 073).
            # A call with no analysis record, or one not categorised yet, has no stored outcome and counts as Open.
            stored_stage = select(1).select_from(Analysis).where(record, Analysis.case_lifecycle_stage == lifecycle_stage).exists()
            if lifecycle_stage == "open":
                has_other_stage = select(1).select_from(Analysis).where(
                    record, Analysis.case_lifecycle_stage.isnot(None), Analysis.case_lifecycle_stage != "open").exists()
                selected = selected.filter(~has_other_stage)
            else:
                selected = selected.filter(stored_stage)
        if action_status:
            # Stage 2 "Action Status": exact stored value (e.g. "Pending").
            selected = selected.filter(select(1).select_from(Analysis).where(
                record,
                func.trim(func.coalesce(Analysis.fd_m_manual_action_status, "")) == action_status,
            ).exists())
        if follow_up_date:
            # Stage 2 "Follow Up Dates": exact calendar-date match on any entry (see services.follow_up_dates).
            selected = selected.filter(select(1).select_from(Analysis).where(
                record, has_follow_up_date(follow_up_date),
            ).exists())
        if follow_up_dates_only:
            # Distinct follow-up entries of exactly the records the filters above selected, so the
            # Follow Up Dates popover can never disagree with the table. Dates are parsed in Python.
            chosen = selected.subquery()
            join_on = (cast(Analysis.call_id, PGUUID) == chosen.c.id) if call_source else (Analysis.id == chosen.c.id)
            entries = db.query(date_like_entries().label("entry")).select_from(Analysis).join(chosen, join_on).subquery()
            raw = db.query(entries.c.entry).filter(entries.c.entry.op("~")(DATE_LIKE_REGEX)).distinct().all()
            return parse_entries(row[0] for row in raw)
    if lifecycle_counts:
        # {stage: calls} over exactly the calls selected above, aggregated in SQL (one row per call; nothing is grouped by client,
        # context or lead). A call with no analysis record, or one not categorised yet, counts as Open.
        chosen = selected.subquery()
        stage = func.coalesce(Analysis.case_lifecycle_stage, "open")
        join_on = (cast(Analysis.call_id, PGUUID) == chosen.c.id) if call_source else (Analysis.id == chosen.c.id)
        rows = db.query(stage, func.count()).select_from(chosen).outerjoin(Analysis, join_on).group_by(stage).all()
        counts = {name: int(count) for name, count in rows}
        counts["_total"] = sum(counts.values())          # distinct calls; the five cards overlap now, so it is not their sum
        # Open = the status-based population of the Open Cases button (it overlaps Dropped Cases for Client Side Delay / Dropped).
        status_key = func.lower(func.trim(func.coalesce(Analysis.fd_m_case_status_change, "")))
        closure_link = select(1).select_from(FDCaseClosureLink).where(
            FDCaseClosureLink.analysis_id == Analysis.id, FDCaseClosureLink.reverted_at.is_(None)).correlate(Analysis).exists()
        counts["open"] = int(db.query(func.count()).select_from(chosen).outerjoin(Analysis, join_on).filter(
            or_(status_key == "", status_key.in_(AGING_STATUSES)), ~closure_link).scalar() or 0)
        return counts
    if ids_only:
        # Just the canonical selection: [(analysis id, effective date)]. Lets reports aggregate in SQL over exactly
        # the records the Calls/Dashboard pages count (same duplicate handling, same scope) without loading them.
        return [(row[0], row[1]) for row in selected.all()]
    total = selected.count() if page and page_size else None
    selected = selected.order_by(ranked.c.effective_date.desc(), ranked.c.id.desc())
    if cursor:
        day, last_id = decode_cursor(cursor, scope)
        from uuid import UUID
        try:
            last_id = UUID(last_id) if call_source else int(last_id)
        except (ValueError, TypeError):
            from fastapi import HTTPException
            raise HTTPException(400, "Invalid cursor identity") from None
        selected = selected.filter(or_(ranked.c.effective_date < day,
            and_(ranked.c.effective_date == day, ranked.c.id < last_id)))
    if page and page_size:
        selected = selected.limit(page_size + 1 if cursor_mode else page_size)
        if not cursor_mode:
            selected = selected.offset((page - 1) * page_size)
    selected = selected.subquery()
    if call_source:
        rows = db.query(Call).options(defer(Call.transcript, raiseload=True)).join(
            selected, selected.c.id == Call.call_id).order_by(selected.c.effective_date.desc(), Call.call_id.desc()).all()
    else:
        query = db.query(Analysis).options(defer(Analysis.raw_llm_json, raiseload=True))
        if summary_rows:
            for field in ("call_summary", "fd_a_call_summary", "enquiry_details", "evaluator_notes"):
                query = query.options(defer(getattr(Analysis, field), raiseload=True))
        rows = query.join(selected, selected.c.id == Analysis.id).order_by(selected.c.effective_date.desc(), Analysis.id.desc()).all()
    if cursor_mode and page_size:
        has_more = len(rows) > page_size
        rows = rows[:page_size]
        if cursor_output is not None:
            from routes.calls import _effective_analysis_date
            last = rows[-1] if rows else None
            cursor_output["next_cursor"] = encode_cursor(
                last.call_date if call_source else _effective_analysis_date(last),
                last.call_id if call_source else last.id, scope) if has_more and last else None
    if call_source:
        return rows, {str(row.call_id): row for row in rows}, total
    # Paginated readers enrich only their selected page. Legacy dashboard
    # readers retain their date-bounded full-range contract. Explicitly forbid
    # lazy transcript loading so serializers cannot resurrect over-fetching.
    ids = [str(row.call_id) for row in rows if row.call_id]
    calls = db.query(Call).options(load_only(
        Call.call_id, Call.call_date, Call.call_time, Call.src, Call.dst, Call.extension,
        Call.client_id, Call.audio_s3_uri, Call.audio_s3_key, Call.billsec,
        # disposition: Vitel's answered/unanswered state, read by the inbound
        # agent rule and the Stage 3 lock.
        Call.disposition, Call.registered_at, Call.call_number, Call.call_reference, raiseload=True,
    )).filter(Call.call_id.in_(ids)).all() if ids else []
    return rows, {str(call.call_id): call for call in calls}, total
