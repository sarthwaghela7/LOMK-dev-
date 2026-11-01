from types import SimpleNamespace

from services.voicemail_content import apply_content_voicemail_flag, content_verdict, desired_voicemail_flag

SCREENING = ("Front Desk: If you record your name and reason for calling, I'll see if this person is available. "
             "Client: Hey, this is Christobel from the Law Office. Front Desk: Thanks. Please stay on the line. "
             "[on-hold music] I'm sorry. This person is not available")
GREETING = "Front Desk: Sorry, I missed your call. Leave me a message. Bye. Hello? Hello? [background noise]"
CONVERSATION = ("Client: Hi. Front Desk: Hello. Client: May I know who I'm speaking to? Front Desk: Yes, hi. "
                "This is Christobel. Client: I thought it was you, so I called back. Front Desk: Right. "
                "Client: About the price for thirty minutes. Front Desk: Let me check and call you back.")


def test_call_screening_and_voicemail_greeting_are_voicemail():
    assert content_verdict(SCREENING) == "yes"
    assert content_verdict(GREETING) == "yes"


SCREENED_AND_ANSWERED = ("Front Desk: Hi. If you record your name and reason for calling, I'll see if this person is available. "
    "Client: Hi, I'm Priyanka calling from the offices of Mary Kennedy. Front Desk: Thanks. Please stay on the line. [on-hold music] "
    "Hello? Yes, who is this? Hi, this is Priyanka. Let me check and come back onto the call if you have some reduced price or something, "
    "because my date is not yet there, so I need to- Yeah. Okay, sure. Thank you. Okay. Thank you. Bye-bye. Thank you so much for your time today.")


def test_a_screened_call_that_connected_is_not_voicemail():
    assert content_verdict(SCREENED_AND_ANSWERED) is None
    assert desired_voicemail_flag(None, SCREENED_AND_ANSWERED, "Pricing concern discussed") is None


def test_a_real_conversation_is_left_to_the_existing_rule():
    # The content rule never marks a conversation as voicemail and never removes a voicemail.
    assert content_verdict(CONVERSATION + " The number goes to voicemail.") is None


def test_undecided_and_empty_transcripts_leave_the_ai_answer_alone():
    assert content_verdict("Front Desk: Hello. Client: Hi, I have a question about my case.") is None
    assert content_verdict("") is None and content_verdict(None) is None


def test_explicit_flag_is_never_changed():
    for flag in ("Yes", "No", "yes", "true"):
        assert desired_voicemail_flag(flag, SCREENING, "") is None


def test_flag_written_only_when_it_changes_the_outcome():
    assert desired_voicemail_flag(None, SCREENING, "Person not available") == "Yes"
    assert desired_voicemail_flag("Not Applicable", GREETING, "") == "Yes"
    # Outcome text already says voicemail: the existing rule counts it, nothing to write.
    assert desired_voicemail_flag(None, GREETING, "Voicemail left") is None
    # A conversation is never written, whatever its outcome says.
    assert desired_voicemail_flag(None, CONVERSATION, "client said it goes to voicemail") is None
    assert desired_voicemail_flag(None, CONVERSATION, "callback promised") is None


def test_pipeline_hook_sets_the_flag_on_the_analysis_row():
    row = SimpleNamespace(voicemail_left_if_no_answer=None, fd_a_call_outcome="Person not available")
    apply_content_voicemail_flag(row, SCREENING)
    assert row.voicemail_left_if_no_answer == "Yes"
    kept = SimpleNamespace(voicemail_left_if_no_answer="No", fd_a_call_outcome="")
    apply_content_voicemail_flag(kept, SCREENING)
    assert kept.voicemail_left_if_no_answer == "No"


def test_a_failure_in_the_content_check_never_blocks_saving_the_analysis():
    class Exploding:
        voicemail_left_if_no_answer = None

        @property
        def fd_a_call_outcome(self):
            raise RuntimeError("boom")

    apply_content_voicemail_flag(Exploding(), SCREENING)  # must not raise


def test_it_only_adds_voicemails_and_never_removes_one():
    for flag in ("Yes", "No", "yes", "true"):
        row = SimpleNamespace(voicemail_left_if_no_answer=flag, fd_a_call_outcome="Voicemail left")
        apply_content_voicemail_flag(row, CONVERSATION)
        assert row.voicemail_left_if_no_answer == flag
