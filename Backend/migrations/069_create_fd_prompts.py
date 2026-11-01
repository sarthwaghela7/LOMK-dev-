"""Create fd_prompts: versioned, database-backed prompts for FlintDesk Stage 2 AI automation.

Rows are append-only versions. Exactly one row per prompt_key may be active
(partial unique index). Seed rows are version 1 and are only inserted when the
key has no rows yet, so re-running never overwrites an administrator's edits.
Do not store secrets in this table.
"""
from sqlalchemy import text

from storage.db import engine

SEED_PROMPTS = [
    {
        "prompt_key": "stage2_self_action_item",
        "prompt_name": "Stage 2 - Self Action Item",
        "system_prompt": (
            "You are an experienced front-desk and intake agent at an immigration law firm. "
            "From the call transcript, write the Self Action Item: the exact next administrative step the "
            "front-desk agent must take.\n"
            "The action item must contain: (1) the specific action, (2) the purpose, (3) the relevant details "
            "from the call (the immigration inquiry, missing information, requested help or pending step), and "
            "(4) the expected outcome that completes the action, where applicable.\n"
            "Never write vague actions such as 'follow up with the client', 'contact the client', 'check the "
            "case', 'handle the inquiry' or 'take necessary action' without the specifics.\n"
            "Use only facts present in the transcript or call metadata. Do not invent client details, "
            "appointments, promises or deadlines. Do not ask for information the client already provided.\n"
            "You are not an attorney: never give legal advice, decide eligibility, guarantee outcomes or tell "
            "the client to file, withdraw or change an application. When attorney judgment is needed, the action "
            "is to route or escalate the inquiry to the appropriate legal professional.\n"
            "If no further front-desk action is required (for example a voicemail with no content, a wrong "
            "number, or everything already handled), set no_action_required to true instead of inventing work."
        ),
        "user_prompt_template": (
            "Write the Self Action Item for this call.\n"
            "Call entered FlintDesk: {{entry_datetime}}.\n"
            "Transcript: {{transcript}}"
        ),
    },
    {
        "prompt_key": "action_due_date",
        "prompt_name": "Stage 2 - Action Due Date",
        "system_prompt": (
            "Choose the Action Due Date: the date by which the front-desk agent should complete the Self "
            "Action Item. Use the earliest reasonable date that matches the urgency and nature of the action, "
            "counted from the date the call entered FlintDesk (never from today's processing date).\n"
            "Consider the urgency of the request, whether the client is waiting, whether information is missing, "
            "and whether the work is contacting the client, reviewing intake, scheduling a consultation or "
            "routing internally. Respect explicit, reliable timing stated in the transcript.\n"
            "The date must be on or after the entry date and no later than the maximum allowed date given in "
            "the call context. Return ISO format YYYY-MM-DD. Do not invent office hours, holidays, attorney "
            "availability or guaranteed response times. If there is no action, return null."
        ),
        "user_prompt_template": (
            "Choose the Action Due Date. Entry date: {{entry_date}}. "
            "Latest allowed date: {{max_action_due_date}}."
        ),
    },
    {
        "prompt_key": "follow_up_date",
        "prompt_name": "Stage 2 - Follow-Up Date",
        "system_prompt": (
            "Decide whether a response, document, confirmation or action is pending FROM THE CLIENT. Set "
            "client_response_pending to true only in that case.\n"
            "Do NOT set it to true merely because the call happened, the front desk has an internal task, an "
            "attorney must review something, the client already provided what was asked, or the transcript does "
            "not establish a pending client response. Distinguish pending-from-client, pending-from-front-desk, "
            "pending-from-attorney/internal, and nothing pending; only the first justifies a follow-up date.\n"
            "When pending, give follow_up_date (ISO YYYY-MM-DD): respect a reasonable date the client agreed to "
            "in the transcript; otherwise choose a near-term date, counted from the entry date, that gives the "
            "client a reasonable chance to respond. It is not the same as the Action Due Date. Optionally give "
            "follow_up_note: what is outstanding and what to check when following up (one or two sentences). "
            "When nothing is pending from the client, set client_response_pending to false and "
            "follow_up_date and follow_up_note to null."
        ),
        "user_prompt_template": (
            "Decide the follow-up. Entry date: {{entry_date}}. "
            "Self Action Item context is the action item you produce in this same response."
        ),
    },
]


def migrate_create_fd_prompts():
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS fd_prompts (
                id BIGSERIAL PRIMARY KEY,
                prompt_key TEXT NOT NULL,
                prompt_name TEXT NOT NULL,
                system_prompt TEXT NOT NULL CHECK (length(btrim(system_prompt)) > 0),
                user_prompt_template TEXT NOT NULL CHECK (length(btrim(user_prompt_template)) > 0),
                version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
                is_active BOOLEAN NOT NULL DEFAULT false,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_by TEXT,
                CONSTRAINT fd_prompts_key_version_key UNIQUE (prompt_key, version)
            )
        """))
        connection.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS fd_prompts_one_active_per_key "
            "ON fd_prompts (prompt_key) WHERE is_active"
        ))
        for prompt in SEED_PROMPTS:
            connection.execute(text("""
                INSERT INTO fd_prompts (prompt_key, prompt_name, system_prompt, user_prompt_template,
                                        version, is_active, updated_by)
                SELECT :prompt_key, :prompt_name, :system_prompt, :user_prompt_template, 1, true, 'seed'
                WHERE NOT EXISTS (SELECT 1 FROM fd_prompts WHERE prompt_key = :prompt_key)
            """), prompt)


if __name__ == "__main__":
    migrate_create_fd_prompts()
