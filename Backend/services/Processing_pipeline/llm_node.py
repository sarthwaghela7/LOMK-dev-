import json
import requests

from utils.config import LLM_API_KEY, LLM_MODEL, LLM_URL
from utils.prompt_config import LLM_NODE_SYSTEM_PROMPT


class LLM:
    """
    AutoFlint LLM analysis node.

    This module is responsible only for AutoFlint LLM analysis.
    FlintDesk analysis is handled separately by fd_llm_node.py.
    """

    def __init__(self):
        if not LLM_API_KEY:
            raise ValueError("LLM_API_KEY not found")

        if not LLM_URL:
            raise ValueError("LLM_URL not found")

        self.api_key = LLM_API_KEY
        self.url = LLM_URL
        self.model = LLM_MODEL

    def _build_metadata_block(self, call_record) -> str:
        """
        Build the call metadata block used by the AutoFlint LLM.
        """
        metadata = {
            "call_id": str(getattr(call_record, "call_id", "")),
            "src": getattr(call_record, "src", None),
            "dst": getattr(call_record, "dst", None),
            "billsec": getattr(call_record, "billsec", None),
            "disposition": getattr(call_record, "disposition", None),
            "extension": getattr(call_record, "extension", None),
            "extname": getattr(call_record, "extname", None),
            "call_date": getattr(call_record, "call_date", None),
            "call_time": getattr(call_record, "call_time", None),
            "audio_s3_uri": getattr(call_record, "audio_s3_uri", None),
            "processing_status": getattr(
                call_record,
                "processing_status",
                None
            ),
        }

        return json.dumps(
            metadata,
            default=str,
            indent=2
        )

    def build_context(self, transcript: str, call_record) -> str:
        """
        Build the prompt/context sent to the AutoFlint LLM.
        """
        metadata_block = self._build_metadata_block(call_record)

        return f"""
{LLM_NODE_SYSTEM_PROMPT}

CALL METADATA FROM DATABASE:
{metadata_block}

CALL TRANSCRIPT:
{transcript or ''}
""".strip()

    def request_analysis(self, transcript: str, call_record) -> dict:
        """
        Generate AutoFlint LLM analysis.

        FlintDesk analysis is intentionally NOT handled here.
        FlintDesk uses fd_llm_node.py instead.
        """
        context = self.build_context(
            transcript,
            call_record
        )

        response = requests.post(
            self.url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "messages": [
                    {
                        "role": "user",
                        "content": context,
                    }
                ],
                "temperature": 0,
            },
            timeout=(10, 120),
        )

        response.raise_for_status()

        return response.json()

    @staticmethod
    def parse_response(response, call_record=None):
        return response["choices"][0]["message"]["content"].strip()

    def generate(self, transcript: str, call_record) -> str:
        return self.parse_response(self.request_analysis(transcript, call_record), call_record)
