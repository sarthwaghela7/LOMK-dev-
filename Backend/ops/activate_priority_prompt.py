"""Publish the contextual Priority Cases prompt (services/priority_case_prompt.py) as the next fd_prompts version.

Dry run by default: shows the active version and whether the new text differs, and writes nothing.

  python -m ops.activate_priority_prompt                 # preview
  python -m ops.activate_priority_prompt --apply         # insert version N+1 and make it the single active one

Activating a version is what makes the next worker run (last PRIORITY_CASES_LOOKBACK_DAYS days) and the next backfill
re-analyse calls: results are keyed by prompt version. It makes NO model request itself. Roll back with
fd_prompt_store.activate_version('priority_case_classification', <old version>, <who>).
Safe to re-run: if the active prompt already has this exact text, nothing is written.
"""
import argparse
import sys

from services import fd_prompt_store as store
from services.priority_case_prompt import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from services.priority_cases import PROMPT_KEY


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the new version (default: preview only)")
    parser.add_argument("--by", default="ops:contextual-prompt", help="value stored in fd_prompts.updated_by")
    args = parser.parse_args()

    error = store.validate_prompt_fields(SYSTEM_PROMPT, USER_PROMPT_TEMPLATE)
    if error:
        print(f"New prompt is not valid: {error}", file=sys.stderr)
        return 1
    rows = store.list_prompts(PROMPT_KEY)
    active = next((r for r in rows if r["is_active"]), None)
    if not rows or active is None:
        print(f"No active '{PROMPT_KEY}' row in fd_prompts; apply migration 074 first.", file=sys.stderr)
        return 1
    print(f"Active: version {active['version']} (updated_by={active['updated_by']}, updated_at={active['updated_at']}); "
          f"{len(rows)} version(s) stored.")
    if active["system_prompt"].strip() == SYSTEM_PROMPT.strip() and active["user_prompt_template"].strip() == USER_PROMPT_TEMPLATE.strip():
        print("The active prompt already has this text. Nothing to do.")
        return 0
    print(f"New text: system {len(SYSTEM_PROMPT)} chars, user template {len(USER_PROMPT_TEMPLATE)} chars -> "
          f"would become version {max(r['version'] for r in rows) + 1}.")
    if not args.apply:
        print("Dry run: nothing written. Re-run with --apply.")
        return 0
    version = store.save_new_version(PROMPT_KEY, SYSTEM_PROMPT, USER_PROMPT_TEMPLATE, args.by)
    print(f"Saved version {version} and made it active. No analysis was started; the worker / backfill will re-analyse calls.")
    return 0 if version else 1


if __name__ == "__main__":
    sys.exit(main())
