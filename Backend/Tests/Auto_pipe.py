from services.Processing_pipeline.pipeline import Pipeline

pipe = Pipeline()
data = ["out-16502379303-102-20260601-120545-1780333545.44454.WAV",
        "out-19016561525-102-20260601-093425-1780324465.44109.WAV",
        ]

for audio in data:
    pipe.process(s3_key=audio)