"""Case Types + EQ: per-call questions (modal, PDF) and the Questions table must agree with the Trends report."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from services import case_type_trends as T

CT = ZoneInfo("America/Chicago")
D = date
NOW = datetime(2026, 10, 12, 15, 0, tzinfo=CT)


def row(day, ref, case, question, ctype="New Client - Individual", t2=None):
    return {"day": day, "call_ref": ref, "t1": case, "t2": t2, "question": question, "client_type": ctype}


def groups_from(rows):
    """The same records as aggregated groups, exactly as fetch_groups would hand them to the report."""
    return [{"day": r["day"], "t1": r["t1"], "t2": r["t2"], "question": r["question"], "client_type": r["client_type"],
             "hour": None, "n": 1} for r in rows]


ROWS = [
    row(D(2026, 10, 5), "AF#1", "H-1B", "How long does H-1B take?"),
    row(D(2026, 10, 6), "AF#2", "H1B visa", "how long does h1b take"),             # same question, other spelling
    row(D(2026, 10, 7), "AF#3", "TN visa", "Can my spouse work on TN?"),            # a type no fixed list knows
    row(D(2026, 10, 8), "AF#4", "tn VISA", "What documents for TN renewal?"),        # case-only variant of the same type
    row(D(2026, 10, 8), "AF#5", "F1", "Can I start OPT early?"),
    row(D(2026, 10, 9), "AF#6", "H-1B", ""),                                         # no question: not an enquiry
    row(D(2026, 10, 9), "AF#7", "H-1B", "Leaving a message", ctype="Voicemail Call"),  # voicemail: not an enquiry
    row(D(2026, 10, 10), "AF#8", "", "Where is my case?"),                           # blank type: Unclassified, still counted
]


def fetch(db, start, end, with_hour=False):
    return [r for r in ROWS if start <= r["day"] <= end]


def test_the_question_list_reconciles_with_the_report_for_every_category():
    period = T.resolve_period("weekly", D(2026, 10, 7), today=D(2026, 10, 12))
    start, end = D.fromisoformat(period["start"]), D.fromisoformat(period["elapsed_end"])
    current = T.aggregate(groups_from(fetch(None, start, end)))
    report = T.assemble_report(period, current, T.aggregate([]), datetime(2026, 10, 12, 15, 0, tzinfo=CT))
    everything = T.list_questions(None, "weekly", D(2026, 10, 7), now=NOW, fetch=fetch)
    assert everything["total"] == report["totals"]["enquiries"] == 6
    for category in report["case_types"]:
        listed = T.list_questions(None, "weekly", D(2026, 10, 7), case_type=category["name"], now=NOW, fetch=fetch)
        assert listed["total"] == category["count"], category["name"]
        assert {i["case_type"] for i in listed["items"]} == {category["name"]}


def test_new_case_types_appear_without_any_code_change_and_case_variants_merge():
    items = T.list_questions(None, "weekly", D(2026, 10, 7), now=NOW, fetch=fetch)["items"]
    names = sorted({i["case_type"] for i in items})
    assert "TN visa" in names or "TN VISA" in names
    assert sum(1 for n in names if n.casefold() == "tn visa") == 1
    assert "Unclassified" in names and "H-1B" in names and "F-1 / OPT / STEM" in names


def test_every_listed_question_is_real_text_with_its_call_date_and_reference():
    items = T.list_questions(None, "weekly", D(2026, 10, 7), now=NOW, fetch=fetch)["items"]
    assert all(i["question"] and i["date"] and i["call_ref"] for i in items)
    assert "Leaving a message" not in {i["question"] for i in items}                 # voicemail
    assert [i["date"] for i in items] == sorted(i["date"] for i in items)


def test_a_repeated_question_is_found_across_spelling_variants_one_entry_per_call():
    listed = T.list_questions(None, "weekly", D(2026, 10, 5), question="How long does H-1B take?", now=NOW, fetch=fetch)
    assert listed["total"] == 2 and [i["call_ref"] for i in listed["items"]] == ["AF#1", "AF#2"]


def test_a_filter_that_matches_nothing_gives_an_empty_list_not_an_error():
    empty = T.list_questions(None, "weekly", D(2026, 10, 5), case_type="Asylum", now=NOW, fetch=fetch)
    assert empty["total"] == 0 and empty["items"] == [] and empty["truncated"] is False


def test_the_question_list_never_includes_days_that_have_not_happened_yet():
    late = ROWS + [row(D(2026, 10, 14), "AF#99", "H-1B", "A call from the future")]
    listed = T.list_questions(None, "weekly", D(2026, 10, 7), now=NOW, fetch=lambda db, s, e: [r for r in late if s <= r["day"] <= e])
    assert "A call from the future" not in {i["question"] for i in listed["items"]}


def test_table_groups_by_week_of_month_and_shows_only_types_that_occur():
    table = T.build_questions_table(ROWS, D(2026, 10, 1), D(2026, 10, 31))
    assert [b["label"] for b in table][:2] == ["10/01/2026 – Week 1", "10/08/2026 – Week 2"]
    week1 = next(b for b in table if b["start"] == "2026-10-01")
    assert [t["type"] for t in week1["types"]] != []
    assert {t["type"].casefold() for t in week1["types"]} >= {"h-1b", "tn visa"}
    assert all(t["questions"] for t in week1["types"])                                # no empty rows for absent types
    h1b = next(t for t in week1["types"] if t["type"] == "H-1B")
    assert h1b["questions"] == ["How long does H-1B take?"] and h1b["items"][0]["count"] == 2   # one line, counted twice


def test_table_total_equals_the_report_total_for_the_same_dates():
    table = T.build_questions_table(ROWS, D(2026, 10, 1), D(2026, 10, 31))
    in_table = sum(t["count"] for week in table for t in week["types"])
    report = T.aggregate(groups_from(ROWS))
    assert in_table == report["enquiries"] == 6


def test_empty_weeks_are_kept_with_no_types_and_an_empty_range_gives_no_rows():
    table = T.build_questions_table([], D(2026, 10, 1), D(2026, 10, 14))
    assert len(table) == 2 and all(week["types"] == [] for week in table)
    assert T.build_questions_table(ROWS, D(2026, 10, 9), D(2026, 10, 1)) == []


def test_weeks_follow_month_and_year_ends_and_first_last_days_are_included():
    table = T.build_questions_table([row(D(2026, 12, 31), "AF#1", "H-1B", "New Year question")], D(2026, 12, 29), D(2027, 1, 2))
    assert [(b["start"], b["end"]) for b in table] == [("2026-12-29", "2026-12-31"), ("2027-01-01", "2027-01-02")]
    assert table[0]["types"][0]["questions"] == ["New Year question"]
    assert table[1]["types"] == []


def test_leap_day_and_daylight_saving_dates_are_plain_calendar_days():
    rows = [row(D(2028, 2, 29), "AF#1", "H-1B", "Leap day"), row(D(2026, 3, 8), "AF#2", "H-1B", "DST starts"),
            row(D(2026, 11, 1), "AF#3", "H-1B", "DST ends")]
    leap = [b for b in T.build_questions_table(rows, D(2028, 2, 27), D(2028, 3, 1)) if b["types"]]
    assert len(leap) == 1 and leap[0]["start"] == "2028-02-29" and leap[0]["types"][0]["questions"] == ["Leap day"]
    assert T.build_questions_table(rows, D(2026, 3, 8), D(2026, 3, 8))[0]["types"][0]["questions"] == ["DST starts"]
    assert T.build_questions_table(rows, D(2026, 11, 1), D(2026, 11, 1))[0]["types"][0]["questions"] == ["DST ends"]


def test_repeated_loading_never_duplicates_counts():
    again = ROWS + []                          # a refresh reads the same canonical records again
    assert T.build_questions_table(again, D(2026, 10, 1), D(2026, 10, 31)) == T.build_questions_table(ROWS, D(2026, 10, 1), D(2026, 10, 31))


def test_unclassified_is_always_the_last_case_type_of_a_week_even_when_it_has_the_most_questions():
    rows = [row(D(2026, 10, 6), f"AF#{i}", "", f"Unclassified question {i}") for i in range(5)] + [
        row(D(2026, 10, 6), "AF#10", "H-1B", "An H-1B question"), row(D(2026, 10, 7), "AF#11", "TN visa", "A TN question"),
        row(D(2026, 10, 7), "AF#12", "TN visa", "Another TN question")]
    week = next(b for b in T.build_questions_table(rows, D(2026, 10, 1), D(2026, 10, 7)) if b["types"])
    names = [t["type"] for t in week["types"]]
    assert names[-1] == "Unclassified" and len(names) == 3
    assert names[:2] == ["TN visa", "H-1B"]                      # the others keep most-questions-first order
    assert week["types"][-1]["count"] == 5                          # it had the most, and is still last
