"""Read-only current-day Vitel/S3 verification; outputs counts, never credentials/CDRs."""
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

import boto3
import requests
from dotenv import load_dotenv


def main():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    day = datetime.now(ZoneInfo("America/Chicago")).date()
    try:
        response = requests.get(os.environ["VITEL_BASE_URL"], params={
        "username": os.environ["VITEL_USERNAME"], "password": os.environ["VITEL_PASSWORD"],
        "line": os.getenv("VITEL_LINE") or "All", "startdate": str(day),
        "enddate": str(day + timedelta(days=1)),
        }, timeout=30)
        if response.status_code != 200:
            raise RuntimeError(f"Vitel returned HTTP {response.status_code}")
        rows = ET.fromstring(response.text).findall("record")
        for extension in ("203", "204", "205"):
            count = sum((row.findtext("extension") or "").strip() == extension for row in rows)
            print(f"Vitel {day}: extension {extension} records={count}")
    except requests.RequestException as exc:
        print(f"Vitel verification unavailable: {type(exc).__name__}")
    client = boto3.client("s3", region_name=os.getenv("AWS_REGION"))
    count = 0
    for page in client.get_paginator("list_objects_v2").paginate(
            Bucket=os.environ["AWS_S3_BUCKET_NAME"], Prefix=f"vitel-call-recordings/{day:%Y/%m/%d}/"):
        count += sum(obj["Key"].lower().endswith((".wav", ".mp3")) for obj in page.get("Contents", []))
    print(f"S3 {day}: recording objects={count}; recording-to-extension assignment requires CDR matching")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Verification failed: {type(exc).__name__}")
        raise SystemExit(1) from None
