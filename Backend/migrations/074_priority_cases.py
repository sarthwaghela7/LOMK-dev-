"""Priority Cases: per-call classification of five immigration forms (RFE, I-130, I-485, H4, H4EAD).

New table call_priority_analysis (one row per call; its own status, independent of calls.fd_processing_status and of
call_analysis_results, so a priority-analysis problem can never touch the Calls page analysis):

    analysis_status   pending | processing | completed | failed
    analysis_source   llm | prefilter   (prefilter = no form mentioned anywhere in the transcript, so no paid request)
    transcript_hash   sha256 of calls.transcript when analysed; a changed transcript is re-analysed, an unchanged one never
    prompt_version    fd_prompts.version of 'priority_case_classification' used; a new version is re-analysed
    result            validated JSON {"forms": {form: {status, evidence, reason, confidence}}, "rev": ...}
    confirmed_forms / uncertain_forms   flattened from result for indexed filtering
    model, input_tokens, output_tokens, attempts, error, claimed_at, last_attempt_at, analyzed_at

Also seeds version 1 of the fd_prompts key 'priority_case_classification', only when that key has no rows yet, so
re-running never overwrites an administrator's edits (same rule as migration 069).

Additive only: no existing table or row is modified, and nothing is analysed by the migration. Safe to rerun.
Rollback (manual, nothing else depends on it):  DROP TABLE call_priority_analysis;
  DELETE FROM fd_prompts WHERE prompt_key = 'priority_case_classification';
"""
from sqlalchemy import text

from storage.db import engine

PROMPT_KEY = "priority_case_classification"

SYSTEM_PROMPT = (
    "You review phone-call transcripts for an immigration law firm and decide, for each of five form types, "
    "whether the call genuinely discusses it. The forms are:\n"
    "- RFE: a USCIS Request for Evidence (written 'RFE', 'R.F.E.', 'R F E' or 'Request for Evidence').\n"
    "- I-130: Form I-130, Petition for Alien Relative (family-based petition).\n"
    "- I-485: Form I-485, Application to Register Permanent Residence or Adjust Status (green card application).\n"
    "- H4: the H-4 dependent visa/status of an H-1B holder's spouse or child (extension, stamping, change of status).\n"
    "- H4EAD: the work permit (EAD, Form I-765) for an H-4 dependent.\n"
    "Judge each form independently; one call can confirm several forms or none.\n"
    "Transcripts come from speech-to-text, so ignore capitalisation, spacing, hyphens and punctuation: 'rfe', 'R F E', "
    "'I130', 'i 130', 'I-485', 'i485', 'H4', 'H-4', 'h 4', 'H4EAD', 'H4 EAD', 'H-4 EAD' and 'h4-ead' all name the "
    "same form. Allow reasonable transcription errors and spoken numbers (for example 'eye 130', 'I four eighty five', "
    "'H four'), but only when the surrounding conversation makes the form clear. Letters inside an unrelated word or "
    "number are not a form.\n"
    "Use these statuses:\n"
    "- confirmed: the form is actively discussed on THIS call for the caller's own matter (a pending, planned, "
    "received or problematic filing, a question about it, or an RFE the caller received).\n"
    "- not_confirmed: not mentioned, or mentioned only as past history with no bearing on the current matter, as a "
    "hypothetical or general-information example, explicitly denied or stated as not applicable, or as an unrelated "
    "abbreviation.\n"
    "- uncertain: a possible reference that cannot be reliably resolved (garbled or ambiguous transcription, an unclear "
    "'the petition' or 'the work permit' that might be this form, or conflicting statements). Never use confirmed when "
    "in doubt.\n"
    "A generic mention of an application, petition, dependent or employment authorisation is NOT enough for a form-"
    "specific status without clear context. Mark H4 confirmed only when the H-4 status itself is discussed, and H4EAD "
    "only when the H-4 work permit is; a call about the H-4 work permit alone is H4EAD, not H4.\n"
    "Use only the transcript. Never invent evidence. For confirmed and uncertain, evidence must be a short excerpt "
    "(at most 300 characters) copied word for word from the transcript, and reason one short sentence."
)

USER_PROMPT_TEMPLATE = "Classify the five forms for this call transcript.\nTranscript:\n{{transcript}}"

STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS call_priority_analysis (
        call_id UUID PRIMARY KEY REFERENCES calls(call_id) ON DELETE CASCADE,
        analysis_status TEXT NOT NULL DEFAULT 'pending'
            CHECK (analysis_status IN ('pending', 'processing', 'completed', 'failed')),
        analysis_source TEXT CHECK (analysis_source IN ('llm', 'prefilter')),
        transcript_hash TEXT,
        prompt_version INTEGER,
        result JSONB,
        confirmed_forms TEXT[] NOT NULL DEFAULT '{}',
        uncertain_forms TEXT[] NOT NULL DEFAULT '{}',
        model TEXT,
        input_tokens INTEGER,
        output_tokens INTEGER,
        attempts INTEGER NOT NULL DEFAULT 0,
        error TEXT,
        claimed_at TIMESTAMPTZ,
        last_attempt_at TIMESTAMPTZ,
        analyzed_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_call_priority_confirmed_forms ON call_priority_analysis USING GIN (confirmed_forms)",
    "CREATE INDEX IF NOT EXISTS ix_call_priority_status ON call_priority_analysis (analysis_status)",
)


def migrate_priority_cases():
    with engine.begin() as connection:
        for statement in STATEMENTS:
            connection.execute(text(statement))
        connection.execute(text("""
            INSERT INTO fd_prompts (prompt_key, prompt_name, system_prompt, user_prompt_template,
                                    version, is_active, updated_by)
            SELECT :k, 'Priority Cases - Form Classification', :sp, :up, 1, true, 'seed'
            WHERE NOT EXISTS (SELECT 1 FROM fd_prompts WHERE prompt_key = :k)
        """), {"k": PROMPT_KEY, "sp": SYSTEM_PROMPT, "up": USER_PROMPT_TEMPLATE})


if __name__ == "__main__":
    migrate_priority_cases()
