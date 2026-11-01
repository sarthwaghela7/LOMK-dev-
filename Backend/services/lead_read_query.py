"""Filter and paginate lead IDs before loading their call/analysis payloads."""
from sqlalchemy import Date, Numeric, String, and_, case, cast, func, or_, select
from sqlalchemy.orm import aliased
from storage.models import Call, FDAssignment, FDLead
from services.read_scope import digits, lower


def client_phone(value):
    value = digits(value)
    value = case(
        (and_(func.length(value) == 11, func.left(value, 1) == "1"), func.substr(value, 2)),
        (and_(func.length(value) == 12, func.left(value, 2) == "91"), func.substr(value, 3)),
        else_=value,
    )
    return case((func.length(value) >= 7, value))


def lead_entry_date():
    """The Entry Date a lead is listed under: its received date when that is a valid ISO date, else its import day (Central Time)."""
    business_date = cast(func.timezone("America/Chicago", FDLead.lead_added_at), Date)
    return case(
        (FDLead.fd_m_leads_date_received.op("~")(r"^\d{4}-\d{2}-\d{2}$"), cast(FDLead.fd_m_leads_date_received, Date)),
        else_=business_date,
    )


def lead_id_order():
    """Latest first, deterministically: the Entry Date (today on top), then the number inside the Lead ID (descending, so LD#1002
    is above LD#1001; IDs without digits last), then the import time, the full ID text and the row id, so pages never shuffle or
    overlap. The Leads page's S1 and S2 stages are the same list in this order."""
    digits_only = func.nullif(func.regexp_replace(FDLead.fd_m_leads_lead_id, r"\D", "", "g"), "")
    return (lead_entry_date().desc(), cast(digits_only, Numeric).desc().nulls_last(), FDLead.lead_added_at.desc().nulls_last(),
            FDLead.fd_m_leads_lead_id.desc(), FDLead.id.desc())


def filtered_leads(db, *, owner=None, start_date=None, end_date=None,
                   extension="", client_id="", call_id="", lead_number="",
                   lead_id="", client_number="", agent="", page=None, page_size=None):
    query = db.query(FDLead)
    if owner:
        query = query.filter(or_(FDLead.fd_m_leads_owner_id == owner.id,
            and_(FDLead.fd_m_leads_owner_id.is_(None), FDLead.fd_m_leads_owner == owner.name)))
    business_date = cast(func.timezone("America/Chicago", FDLead.lead_added_at), Date)
    # The date range applies to the Entry Date (fd_m_leads_date_received, ISO
    # text); leads without a valid one fall back to their import date.
    entry_date = case(
        (FDLead.fd_m_leads_date_received.op("~")(r"^\d{4}-\d{2}-\d{2}$"),
         cast(FDLead.fd_m_leads_date_received, Date)),
        else_=business_date,
    )
    if start_date:
        query = query.filter(entry_date >= start_date)
    if end_date:
        query = query.filter(entry_date <= end_date)
    # Lead Number is the website/trunk phone number, not the imported Lead
    # ID. The old implementation applied both controls to Lead ID, causing
    # the Lead Number filter belt control to return the wrong rows.
    if lead_id:
        query = query.filter(func.lower(FDLead.fd_m_leads_lead_id).contains(
            lead_id.lower(), autoescape=True
        ))
    if client_number:
        import re
        requested = re.sub(r"\D", "", client_number)[-10:]
        query = query.filter(func.right(digits(FDLead.fd_m_leads_phone), 10).contains(requested, autoescape=True))
    if agent and agent != "all":
        name = func.coalesce(func.nullif(FDLead.fd_m_leads_owner, ""), FDLead.fd_a_leads_agent_name, "")
        query = query.filter(func.lower(name).contains(agent.lower(), autoescape=True))
    if lead_number or extension or client_id or call_id:
        # Unlinked leads display a proposed nearest phone/date call. Filter
        # against that same proposal, not just the explicit link.
        candidate = aliased(Call)
        phone = client_phone(FDLead.fd_m_leads_phone)
        proposed = select(candidate.call_id).where(
            phone.isnot(None), or_(client_phone(candidate.src) == phone, client_phone(candidate.dst) == phone)
        ).order_by(func.abs(candidate.call_date - business_date),
                   candidate.call_time.desc(), candidate.call_id.asc()).limit(1).correlate(FDLead).scalar_subquery()
        effective = func.coalesce(FDLead.fd_a_leads_call_id, proposed)
        matched = aliased(Call)
        query = query.outerjoin(matched, matched.call_id == effective)
        if lead_number:
            requested = func.right(digits(lead_number), 10)
            assigned_extensions = select(FDAssignment.extension).where(
                func.right(digits(FDAssignment.number), 10) == requested
            )
            query = query.filter(or_(
                func.right(digits(matched.src), 10) == requested,
                func.right(digits(matched.dst), 10) == requested,
                matched.dst.in_(assigned_extensions),
            ))
        if extension:
            value = func.coalesce(func.nullif(FDLead.fd_m_leads_extension, ""), matched.extension, "")
            query = query.filter(lower(value) == extension.strip().lower())
        if client_id:
            query = query.filter(func.lower(func.coalesce(matched.client_id, "")).contains(client_id.lower(), autoescape=True))
        if call_id:
            # The table shows the call reference (AF#...), not the internal
            # UUID, so match either.
            needle = call_id.strip().lower()
            query = query.filter(or_(
                func.lower(cast(matched.call_id, String)).contains(needle, autoescape=True),
                func.lower(func.coalesce(matched.call_reference, "")).contains(needle, autoescape=True),
            ))
    query = query.order_by(*lead_id_order())
    if page and page_size:
        query = query.offset((page - 1) * page_size).limit(page_size)
    return query
