"""PostgreSQL expressions shared by bounded list reads and aggregates."""
from sqlalchemy import Date, and_, case, cast, func, literal, or_, select
from sqlalchemy.orm import aliased

from storage.models import Call, FDAssignment, FDSyncFilter
from services.dashboard_call_scope import DASHBOARD_NUMBERS, DASHBOARD_EXTENSIONS


def digits(value):
    return func.regexp_replace(func.coalesce(value, ""), "[^0-9]", "", "g")


def normalized_number(value):
    value = digits(value)
    return case((and_(func.length(value) == 11, func.left(value, 1) == "1"), func.substr(value, 2)), else_=value)


def lower(value):
    return func.lower(func.trim(func.coalesce(value, "")))


def dashboard_scope(call=Call):
    # Special 601/603/605 mappings require one of DASHBOARD_NUMBERS in src/dst,
    # already covered by the first two terms.
    return or_(normalized_number(call.src).in_(DASHBOARD_NUMBERS),
               normalized_number(call.dst).in_(DASHBOARD_NUMBERS),
               normalized_number(call.extension).in_(DASHBOARD_EXTENSIONS))


def is_inbound_call(call=Call):
    """SQL twin of routes.calls._call_direction(...) == "Inbound"."""
    def in_system(column):
        return or_(
            normalized_number(column).in_(select(normalized_number(FDSyncFilter.value)).where(normalized_number(FDSyncFilter.value) != "")),
            normalized_number(column).in_(select(normalized_number(FDSyncFilter.parent_number)).where(normalized_number(FDSyncFilter.parent_number) != "")),
        )
    return and_(~in_system(call.src), in_system(call.dst))


def unanswered_disposition(call=Call):
    """SQL twin of services.vitel_metadata.is_unanswered_disposition."""
    from services.vitel_metadata import UNANSWERED
    key = func.upper(func.regexp_replace(func.coalesce(call.disposition, ""), "[\\s_-]+", "", "g"))
    return key.in_(sorted(UNANSWERED))


def inbound_agent_answered(call=Call):
    """SQL twin of services.vitel_metadata.inbound_agent_extension(...) truthiness."""
    from services.vitel_metadata import NON_AGENT_EXTENSIONS, RING_ROUTE_DSTS
    extension = func.trim(func.coalesce(call.extension, ""))
    answered = func.upper(func.regexp_replace(func.coalesce(call.disposition, ""), "[\\s_-]+", "", "g")) == "ANSWERED"
    ring_route = normalized_number(call.dst).in_(sorted(RING_ROUTE_DSTS))
    shape = or_(extension.op("~")("^[0-9]{3}$"),
                and_(ring_route, extension.op("~")("^[0-9]{10}$"),
                     normalized_number(extension) != normalized_number(call.src)))
    return and_(answered, shape, extension.notin_(sorted(NON_AGENT_EXTENSIONS)))


def assignment_agent_id(call=Call):
    assignment = aliased(FDAssignment)
    routing = aliased(FDSyncFilter)
    active_assignment = aliased(FDAssignment)
    parent = normalized_number(assignment.number)
    routed = select(literal(1)).where(
        routing.filter_type == "extension",
        normalized_number(routing.parent_number) == parent,
        normalized_number(routing.value) == normalized_number(call.dst),
    ).correlate(assignment, call).exists()
    extension_matches = func.trim(assignment.extension) == func.trim(call.extension)
    number_matches = or_(normalized_number(call.src) == parent,
                         normalized_number(call.dst) == parent, routed)
    # When a lead number is moved mid-day, older CDRs retain the previous
    # public number. They still belong to their extension's sole active owner.
    # Keep the normal number match as first priority; only use this fallback
    # when there is exactly one active owner for the extension.
    active_owner_count = select(
        func.count(func.distinct(active_assignment.agent_id))
    ).where(
        func.trim(active_assignment.extension) == func.trim(call.extension),
        active_assignment.is_active.is_(True),
    ).correlate(call).scalar_subquery()
    # Inbound calls are owned only when ANSWERED by a plain agent extension
    # (see routes.calls.assignment_agent_details / vitel_metadata.inbound_agent_extension);
    # every other inbound leg has no extension owner.
    eligible = and_(extension_matches, parent != "", or_(number_matches, active_owner_count == 1),
                    or_(~is_inbound_call(call), inbound_agent_answered(call)))
    # Latest assignment <= call date; if none exists, oldest matching owner.
    past = assignment.assignment_date <= call.call_date
    return select(assignment.agent_id).where(eligible).order_by(
        case((number_matches, 0), else_=1),
        case((past, 0), else_=1),
        case((past, assignment.assignment_date)).desc().nulls_last(),
        case((past, assignment.created_at)).desc().nulls_last(),
        assignment.assignment_date.asc(), assignment.created_at.asc(),
        assignment.id.asc(),
    ).limit(1).correlate(call).scalar_subquery()


def analysis_date(analysis):
    raw = func.trim(analysis.call_recording_date)
    # Preserve the two legacy date formats, including invalid-calendar-day
    # rejection. pg_input_is_valid is available on the project's PostgreSQL 17.
    iso = case(
        (raw.op("~")(r"^[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}$"), raw),
        (raw.op("~")(r"^[0-9]{1,2}/[0-9]{1,2}/[0-9]{4}$"),
         func.concat(func.split_part(raw, "/", 3), "-", func.split_part(raw, "/", 1), "-", func.split_part(raw, "/", 2))),
    )
    parsed = case((func.pg_input_is_valid(iso, "date"), cast(iso, Date)))
    return func.coalesce(analysis.call_recording_date_normalized, parsed)
