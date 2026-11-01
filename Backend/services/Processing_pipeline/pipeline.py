from pathlib import Path
from types import SimpleNamespace
import requests
from .stt_node import STT
from .llm_node import LLM

class Pipeline ():
    def __init__(self) :
        self.stt = STT()
        self.llm = LLM()
    
    def process(self, call_record):
        s3_reference = getattr(call_record, "audio_s3_uri", None) or getattr(call_record, "audio_s3_key", None)
        stt_result = self.stt.transcribe(s3_reference)
        llm_result = self.llm.generate(stt_result, call_record)
        
        print(stt_result)
        print(llm_result)
        
        return stt_result, llm_result


if __name__ == "__main__":

    pipe = Pipeline()
    list = [
        "out-+18608697020-101-20260601-143251-1780342371.44822.WAV",
        "out-+14157697284-101-20260601-142801-1780342081.44803.WAV",
        "out-5516894615-101-20260601-124838-1780336118.44549.WAV",
        "out-2247165682-101-20260601-120717-1780333637.44456.WAV"
    ]
    
    for call in list:
        pipe.process(
            SimpleNamespace(
                call_id="demo",
                src=None,
                dst=None,
                billsec=None,
                disposition=None,
                extension=None,
                extname=None,
                call_date=None,
                call_time=None,
                audio_s3_uri=call,
                audio_s3_key=call,
                processing_status=None,
            )
        )
        