"""Daily usage and cost monitoring (STT, LLM, Railway, Supabase, S3).

Self-contained: nothing here is imported by the call-processing pipeline
except ``monitoring.recorder`` (best-effort usage ledger writes that can never
raise into the caller). Run the report with ``python -m monitoring``.
"""
