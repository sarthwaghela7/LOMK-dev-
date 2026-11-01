# import json

# import requests

# from utils.config import LLM_API_KEY, LLM_MODEL, LLM_URL
# from utils.prompt_config import CALL_QUALITY_SYSTEM_PROMPT

# MODEL = LLM_MODEL


# class CallQuality:
#     def __init__(self):
#         if not LLM_API_KEY:
#             raise ValueError("LLM_API_KEY not found")
#         if not LLM_URL:
#             raise ValueError("LLM_URL not found")

#         self.api_key = LLM_API_KEY
#         self.url = LLM_URL

#     def _build_metadata_block(self, call_record) -> str:
#         metadata = {
#             "call_id": str(getattr(call_record, "call_id", "")),
#             "src": getattr(call_record, "src", None),
#             "dst": getattr(call_record, "dst", None),
#             "billsec": getattr(call_record, "billsec", None),
#             "disposition": getattr(call_record, "disposition", None),
#             "extension": getattr(call_record, "extension", None),
#             "extname": getattr(call_record, "extname", None),
#             "call_date": getattr(call_record, "call_date", None),
#             "call_time": getattr(call_record, "call_time", None),
#             "audio_s3_uri": getattr(call_record, "audio_s3_uri", None),
#             "processing_status": getattr(call_record, "processing_status", None),
#         }

#         return json.dumps(metadata, default=str, indent=2)

#     def build_context(self, transcript: str, call_record) -> str:
#         metadata_block = self._build_metadata_block(call_record)
#         return f"""
# {CALL_QUALITY_SYSTEM_PROMPT}

# CALL METADATA FROM DATABASE:
# {metadata_block}

# CALL TRANSCRIPT:
# {transcript}
# """.strip()

#     def get_quality(self, transcript: str, call_record) -> str:
#         context = self.build_context(transcript, call_record)

#         response = requests.post(
#             self.url,
#             headers={
#                 "Authorization": f"Bearer {self.api_key}",
#                 "Content-Type": "application/json",
#             },
#             json={
#                 "model": MODEL,
#                 "messages": [
#                     {
#                         "role": "user",
#                         "content": context
#                     }
#                 ],
#                 "temperature": 0
#             }
#         )

#         # print(response.status_code)
#         # print(response.text)

#         response.raise_for_status()

#         return response.json()["choices"][0]["message"]["content"].strip()