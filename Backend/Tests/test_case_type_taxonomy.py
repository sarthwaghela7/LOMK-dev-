import pytest

from services.case_type_taxonomy import (
    OTHER, UNCLASSIFIED, classify_case_type, clean_question, is_enquiry_client_type, question_key, redact_for_ai,
)


@pytest.mark.parametrize("raw,expected", [
    ("H-1B visa", "H-1B"), ("H1B extension", "H-1B"), ("h-1b", "H-1B"), ("H-1B transfer", "H-1B"),
    ("H4 EAD", "H-4 / H-4 EAD"), ("H4EAD", "H-4 / H-4 EAD"), ("H1B/H4 EAD", "H-4 / H-4 EAD"),   # H-4 first, as before
    ("F1 visa process", "F-1 / OPT / STEM"), ("STEM OPT", "F-1 / OPT / STEM"),
    ("I-140", "I-140 / EB / PERM"), ("Form I-140", "I-140 / EB / PERM"), ("EB-2 NIW", "I-140 / EB / PERM"),
    ("Employment-based green card", "I-140 / EB / PERM"), ("Employment-based immigration", "I-140 / EB / PERM"),
    ("I-485 filing", "I-485 / Green Card"), ("Adjustment of status", "I-485 / Green Card"), ("Green card", "I-485 / Green Card"),
    ("RFE", "RFE / NOID"), ("Citizenship", "Citizenship"),
])
def test_known_families_are_grouped_whatever_the_spelling(raw, expected):
    assert classify_case_type(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "  ", "N/A", "n/a", "Immigration", "immigration consultation", "Paid Consultation",
                                 "Technical support", "visa", "Unknown"])
def test_blank_or_generic_values_are_unclassified_never_guessed(raw):
    assert classify_case_type(raw) == UNCLASSIFIED


def test_a_specific_but_unmapped_label_is_its_own_category_not_invented_into_a_family():
    assert classify_case_type("Mandamus lawsuit") == "Mandamus lawsuit"
    assert classify_case_type("  e-2   investor. ") == "E-2 Investor"      # tidied; all-lowercase is title-cased
    assert classify_case_type("TN visa") == "TN visa"                      # the AI's own capitalisation is kept
    assert len(classify_case_type("longword " * 30)) <= 60                 # long free text is capped


def test_the_first_usable_label_wins_and_falls_back_to_the_second_field():
    assert classify_case_type("", "H-1B") == "H-1B"
    assert classify_case_type("N/A", "H-1B") == UNCLASSIFIED      # a stated N/A is not overridden by a later guess
    assert classify_case_type(None, None) == UNCLASSIFIED


def test_voicemail_and_spam_are_not_enquiries():
    assert not is_enquiry_client_type("Voicemail Call") and not is_enquiry_client_type(" spam / wrong call ")
    assert is_enquiry_client_type("New Client - Individual") and is_enquiry_client_type(None)


def test_harmless_differences_do_not_split_a_question():
    a = "Is my I-485 delayed?"
    for variant in ("is my I485 delayed", "  IS  MY  i-485   DELAYED ?? ", "Is my I‑485 delayed!"):
        assert question_key(a) == question_key(variant)


def test_materially_different_questions_are_not_merged():
    pairs = [("Is my I-485 delayed?", "Is my I-140 delayed?"),
             ("Can I travel on H-1B?", "Can I not travel on H-1B?"),
             ("Status of H-1B extension", "Status of H-1B transfer"),
             ("What is the fee?", "What is the filing fee?")]
    for left, right in pairs:
        assert question_key(left) != question_key(right)


def test_original_question_text_is_only_whitespace_tidied():
    assert clean_question("  Can I\n travel\t on  H-1B? ") == "Can I travel on H-1B?"


def test_text_sent_to_the_ai_has_no_contact_details_and_is_capped():
    text = redact_for_ai("Call me on (312) 555-0100 or jane@example.com about I-485 " + "x" * 400)
    assert "555" not in text and "@" not in text and "[phone]" in text and "[email]" in text and len(text) <= 180
