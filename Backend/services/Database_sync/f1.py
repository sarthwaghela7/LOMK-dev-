"""
f1.py

F1Processor service for syncing call data from Vitel to the backend.
Syncs configured Vitel numbers and extensions. The 601 inbound trunk is
restricted to calls routed to extensions 101 and 102.

Pipeline (see F1Processor.run):
    1. Fetch raw CDR records from the Vitel API for the target date.
    2. Filter to allowed extensions/src or special numbers (_filter_calls).
    3. Merge duplicate CDR legs (_merge_call_legs) — Vitel sometimes emits
       two records for one physical call (e.g. a short extension-pickup leg
       and the full trunk leg, differing only in billsec); these are
       collapsed into a single record, keeping the leg with the larger
       billsec, before anything is inserted.
    4. Insert new call records, skipping ones already in the DB (_insert_calls).
    5. Pull that day's recordings from S3 (_get_latest_batch).
    6. Resolve each call's recording (_map_audio). Vitel now supplies the
       authoritative recording filename in the CDR (``callrecordfilename``);
       when present it is used directly (_vitel_recording_mappings) and the
       legacy matcher below is bypassed for that call. Calls with no Vitel
       filename fall back to the legacy matcher:
       _match_audio matches by timestamp proximity
       AND real audio duration (via a cheap S3 range-get WAV header probe,
       _get_wav_duration_seconds) so a call is only matched to a recording
       whose length actually resembles its billsec, then assigned greedily
       by best combined score.
    7. Write matched audio paths back to the call records (_update_audio_paths).
"""

import os
import re
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytz
from concurrent.futures import ThreadPoolExecutor
from tqdm import tqdm
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from utils.logger import logger

_VITEL_SECRET_QUERY = re.compile(r"(?i)\b(username|password)=[^&\s'\")]+")


def redact_vitel_secrets(message) -> str:
    """The Vitel request carries the account name and password in its query string, and the error text of a failed request
    repeats the whole URL; mask both before the text reaches a log line, the stored sync error or a traceback."""
    return _VITEL_SECRET_QUERY.sub(lambda match: f"{match.group(1)}=***", str(message))
from utils.config import (
    AWS_S3_BUCKET_NAME,
    VITEL_USERNAME,
    VITEL_PASSWORD,
    VITEL_LINE,
    VITEL_BASE_URL,
    F1_IO_WORKERS,
)
from storage.db import SessionLocal
from storage.models import Call, FDAssignment, SyncLog, FDSyncFilter
from services.client_lifecycle import reconcile_client_call_totals, resolve_client_for_call
from services.vitel_metadata import (
    ANSWERED,
    VOICEMAIL,
    clean_recording_filename,
    disposition_key,
    recording_owner_ids,
)
import storage.models

print(storage.models.__file__)
print(Call.__table__.columns.keys())
from storage.s3 import S3Service


class F1Processor:

    MATCH_TOLERANCE_SECONDS = 30

    def __init__(self):
        # Do NOT open a database session here. We'll open it only when needed
        # for actual database operations. This allows external operations
        # (Vitel API, S3 access, audio processing) to proceed without holding
        # a connection from the pool.
        self.db = None
        self.s3 = S3Service()
        self.bucket = AWS_S3_BUCKET_NAME

        self.username = VITEL_USERNAME
        self.password = VITEL_PASSWORD
        self.line = VITEL_LINE or "All"
        self.base_url = VITEL_BASE_URL
        # Refreshed from fd_sync_filters before each sync.  Recording names
        # use the answering extension as their source for some outbound calls,
        # while the CDR uses the public lead number.  Keeping all configured
        # lead numbers here lets old recordings still match if an extension is
        # later moved to a different lead number.
        self._configured_numbers = set()
        self._configured_extension_parent_numbers = {}
        self._extension_parent_history = {}

    def _utcnow(self):
        return datetime.now(timezone.utc).replace(tzinfo=None)

    def _parse_xml(self, xml_text):
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as original_error:
            # Vitel occasionally returns otherwise-valid CDR XML containing a
            # literal ampersand or XML-1.0 control character in caller/callee
            # metadata.  Those values should not abort an entire date's
            # ingestion.  Preserve valid XML entities and only repair values
            # that are invalid in XML text before trying once more.
            sanitized = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", xml_text)
            sanitized = re.sub(
                r"&(?!#\d+;|#x[0-9A-Fa-f]+;|[A-Za-z_:][A-Za-z0-9_.:-]*;)",
                "&amp;",
                sanitized,
            )
            if sanitized == xml_text:
                raise original_error
            try:
                root = ET.fromstring(sanitized)
                logger.warning(
                    "Vitel returned malformed XML text; repaired invalid text "
                    "characters before parsing the CDR response."
                )
            except ET.ParseError:
                raise original_error
        calls = []
        for record in root.findall("record"):
            calldate = record.findtext("calldate")
            if not calldate:
                continue
            d, t = calldate.split(" ")
            call = {"call_date": d, "call_time": t}
            for field in record:
                if field.tag != "calldate":
                    call[field.tag] = field.text
            calls.append(call)
        return calls

    def _fetch_calls_for_date(self, target_date):
        try:
            # FIX (2026-08-11): Vitel API uses EXCLUSIVE end-date (standard REST behavior).
            # To fetch all calls for the entire target_date, we increment end_date by 1 day.
            # This ensures the API returns calls from target_date 00:00:00 through 23:59:59.
            # Example: startdate=2026-08-10, enddate=2026-08-11 returns all Aug 10 calls.
            end_date = target_date + timedelta(days=1)
            
            params = {
                "username": self.username,
                "password": self.password,
                "line": self.line,
                "startdate": target_date.strftime("%Y-%m-%d"),
                "enddate": end_date.strftime("%Y-%m-%d"),
            }
            r = requests.get(self.base_url, params=params, timeout=60)
            r.raise_for_status()
            calls = self._parse_xml(r.text)
            logger.info(f"Fetched {len(calls)} calls from Vitel API for {target_date}")
            return calls
        except requests.exceptions.RequestException as e:
            # Rewrite the message in place (so every later str(e), traceback and stored sync error is masked too) and drop the
            # chained urllib3 error, whose text repeats the URL.
            e.args = (redact_vitel_secrets(e),)
            e.__cause__ = e.__context__ = None
            e.__suppress_context__ = True
            logger.error(f"Failed to fetch calls from Vitel: {e}")
            raise
        except ET.ParseError as e:
            logger.error(f"Failed to parse Vitel XML response: {str(e)}")
            raise

    @staticmethod
    def _digits_only(value):
        return "".join(ch for ch in str(value) if ch.isdigit())

    @classmethod
    def _normalize_number(cls, value):
        digits = cls._digits_only(value)
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        return digits

    def _load_sync_filters(self):
        # Acquire session only for this operation, then close it.
        # This prevents holding a connection during external operations.
        db = SessionLocal()
        try:
            rows = db.query(
                FDSyncFilter.filter_type,
                FDSyncFilter.value,
                FDSyncFilter.parent_number,
            ).all()
            assignment_rows = db.query(
                FDAssignment.extension,
                FDAssignment.number,
                FDAssignment.assignment_date,
            ).all()
            numbers = {
                self._normalize_number(value)
                for filter_type, value, _ in rows
                if filter_type == "number"
            }
            extensions_by_number = {}
            extension_parent_numbers = {}
            for filter_type, value, parent_number in rows:
                if filter_type == "extension" and parent_number:
                    parent = self._normalize_number(parent_number)
                    extension = str(value).strip()
                    extensions_by_number.setdefault(parent, set()).add(extension)
                    extension_parent_numbers.setdefault(extension, set()).add(parent)
            configured_numbers = {number for number in numbers if number}
            self._configured_numbers = configured_numbers
            self._configured_extension_parent_numbers = {
                extension: {number for number in parents if number in configured_numbers}
                for extension, parents in extension_parent_numbers.items()
            }
            histories = {}
            for extension, parent_number, assignment_date in assignment_rows:
                parent = self._normalize_number(parent_number)
                if parent in configured_numbers:
                    histories.setdefault(str(extension).strip(), []).append(
                        (assignment_date, parent)
                    )
            self._extension_parent_history = {
                extension: sorted(entries, key=lambda entry: entry[0])
                for extension, entries in histories.items()
            }
            return {
                number: extensions
                for number, extensions in extensions_by_number.items()
                if number in configured_numbers
            }, configured_numbers
        finally:
            db.close()

    def _is_configured_number(self, value, numbers):
        return self._normalize_number(value) in numbers

    def _parent_numbers_for_extension_at_date(self, extension, call_date):
        """Return the lead number(s) valid for an extension on ``call_date``."""
        extension = str(extension or "").strip()
        history = getattr(self, "_extension_parent_history", {}).get(extension, [])
        effective = [entry for entry in history if entry[0] <= call_date]
        if effective:
            latest_date = max(assigned_date for assigned_date, _ in effective)
            return {
                parent for assigned_date, parent in effective
                if assigned_date == latest_date
            }
        return getattr(self, "_configured_extension_parent_numbers", {}).get(extension, set())

    def _filter_calls(self, calls, phone_number=None):
        """Keep calls whose extension or source/destination is configured in the database."""
        extensions_by_number, allowed_numbers = self._load_sync_filters()
        requested_number = self._normalize_number(phone_number) if phone_number else None
        if requested_number and requested_number not in allowed_numbers:
            raise ValueError(
                f"Phone number {phone_number!r} is not configured in fd_sync_filters"
            )
        kept = []
        dropped = 0

        for call in calls:
            extension = str(call.get("extension", "")).strip()
            src = str(call.get("src", "")).strip()
            dst = str(call.get("dst", "")).strip()

            src_number = self._normalize_number(src)
            dst_number = self._normalize_number(dst)
            matched_number = src_number if src_number in allowed_numbers else dst_number if dst_number in allowed_numbers else None
            extension_match = matched_number is not None and extension in extensions_by_number.get(matched_number, set())
            # 601 used to require extension being literally "101"/"102" here,
            # on the assumption Vitel logs a per-extension attempt leg for
            # the 601 trunk. Real CDRs instead log `extension` as the
            # caller's own number for every 601 record (BUSY/NO ANSWER/
            # ANSWERED alike) -- the same shape 603 (no such restriction)
            # already uses successfully -- so that condition never matched
            # and silently dropped every 601 call. Matching 603's plain
            # number_match here is what "Capture inbound calls from number
            # 601" (the commit that added the special case) actually needed.
            number_match = matched_number is not None

            requested_number_match = (
                requested_number is None
                or src_number == requested_number
                or dst_number == requested_number
            )

            if (extension_match or number_match) and requested_number_match:
                kept.append(call)
            else:
                dropped += 1

        logger.info(
            f"Filtered raw call metadata using {len(allowed_numbers)} configured numbers and "
            f"{sum(len(items) for items in extensions_by_number.values())} configured extensions: "
            f"kept {len(kept)}, dropped {dropped} "
            f"(out of {len(calls)} fetched)."
        )
        return kept

    # Two Vitel CDR rows within this many seconds of each other, on the same
    # date/src/dst, are treated as legs of the SAME physical call (e.g. an
    # extension-pickup leg vs. the full trunk leg) rather than distinct calls.
    LEG_MERGE_TOLERANCE_SECONDS = 120

    def _merge_call_legs(self, calls):
        """
        Collapse duplicate CDR legs that represent one physical call.

        Vitel sometimes emits two records for a single call (e.g. a short
        extension leg with billsec=3 and the real trunk leg with billsec=297).
        These share call_date/src/dst and have call_times close together, but
        differ in billsec, so a naive (date, time, src, dst, billsec) dedupe
        key treats them as two separate calls. Here we cluster by
        (call_date, src, dst) + time proximity and keep only the leg with the
        largest billsec per cluster, since that's the one representing the
        actual conversation.
        """
        from collections import defaultdict

        groups = defaultdict(list)
        for c in calls:
            key = (c["call_date"], str(c.get("src", "")).strip(), str(c.get("dst", "")).strip())
            groups[key].append(c)

        merged = []
        dropped = 0

        for key, group in groups.items():
            group.sort(key=lambda c: c["call_time"])
            cluster = []

            def flush(cluster):
                nonlocal dropped
                if not cluster:
                    return
                if len(cluster) == 1:
                    merged.append(cluster[0])
                    return
                # Vitel's disposition is authoritative: the leg that carried the
                # conversation (ANSWERED / VOICEMAIL) represents the physical
                # call even when an unanswered ring leg reports a longer
                # billsec. Without any such leg this is the original
                # largest-billsec rule.
                def leg_rank(c):
                    conversation = disposition_key(c.get("disposition")) in (ANSWERED, VOICEMAIL)
                    return (conversation, int(c.get("billsec") or 0))

                best = max(cluster, key=leg_rank)
                kept = best
                # Every leg of one interaction carries the same Vitel recording
                # filename; keep it if only a dropped sibling leg reported it.
                if not clean_recording_filename(best.get("callrecordfilename")):
                    for other in cluster:
                        if clean_recording_filename(other.get("callrecordfilename")):
                            kept = {**best, "callrecordfilename": other["callrecordfilename"]}
                            break
                merged.append(kept)
                dropped += len(cluster) - 1
                logger.info(
                    f"Merged {len(cluster)} call legs for {key} into one record "
                    f"(kept billsec={best.get('billsec')}, dropped billsecs="
                    f"{[c.get('billsec') for c in cluster if c is not best]})"
                )

            for c in group:
                if not cluster:
                    cluster.append(c)
                    continue
                prev_dt = datetime.strptime(f"{cluster[-1]['call_date']} {cluster[-1]['call_time']}", "%Y-%m-%d %H:%M:%S")
                cur_dt = datetime.strptime(f"{c['call_date']} {c['call_time']}", "%Y-%m-%d %H:%M:%S")
                if (cur_dt - prev_dt).total_seconds() <= self.LEG_MERGE_TOLERANCE_SECONDS:
                    cluster.append(c)
                else:
                    flush(cluster)
                    cluster = [c]
            flush(cluster)

        if dropped:
            logger.info(f"Leg-merging collapsed {dropped} duplicate CDR leg(s); {len(merged)} unique calls remain.")

        return merged

    def _load_existing_call_keys(self, calls):
        # Acquire session only for this query, then close it.
        db = SessionLocal()
        try:
            dates = {
                datetime.strptime(c["call_date"], "%Y-%m-%d").date()
                for c in calls
            }
            if not dates:
                return set()

            existing_rows = (
                db.query(
                    Call.call_date, Call.call_time, Call.src, Call.dst
                )
                .filter(Call.call_date.in_(dates))
                .all()
            )
            return set(existing_rows)
        finally:
            db.close()

    def _insert_calls(self, calls):
        # Acquire session only for this operation, then close it immediately after.
        db = SessionLocal()
        try:
            existing_keys = self._load_existing_call_keys(calls)
            seen_in_batch = set()

            rows = []
            skipped = 0
            # Existing rows ingested before Vitel supplied recording filenames
            # (or before this column existed) pick the filename up here so the
            # next audio pass can use it. Never overwrites a stored value.
            filename_backfill = []
            for c in tqdm(calls, desc="Preparing call records", unit="call"):
                call_date = datetime.strptime(c["call_date"], "%Y-%m-%d").date()
                call_time = datetime.strptime(c["call_time"], "%H:%M:%S").time()
                billsec = int(c.get("billsec", 0))
                # A revised Vitel CDR can report a different billsec for the
                # same physical call. Duration is mutable metadata, not call
                # identity; treating it as identity created duplicate Calls
                # rows for one recording on later F1 runs.
                key = (call_date, call_time, c.get("src"), c.get("dst"))

                if key in existing_keys or key in seen_in_batch:
                    skipped += 1
                    recording = clean_recording_filename(c.get("callrecordfilename"))
                    if key in existing_keys and recording:
                        filename_backfill.append({
                            "f": recording, "d": call_date, "t": call_time,
                            "s": c.get("src"), "ds": c.get("dst"),
                        })
                    continue
                seen_in_batch.add(key)
                # Atomic, cross-process-safe allocation via the existing
                # Postgres sequence (same pattern as
                # services/client_lifecycle.py:_new_client_id). Replaces the
                # prior SELECT MAX(call_number)+1 approach, which two F1
                # processes running concurrently could both read before
                # either committed.
                call_number = db.execute(text("SELECT nextval('call_number_seq')")).scalar_one()

                client = resolve_client_for_call(db, c.get("src"), c.get("dst"), call_date)

                rows.append({
                    "call_id": uuid4(),
                    "call_number": call_number,
                    "call_reference": f"AF#{call_number}",
                    "call_date": call_date,
                    "call_time": call_time,
                    "src": c.get("src"),
                    "dst": c.get("dst"),
                    "billsec": billsec,
                    "disposition": c.get("disposition"),
                    "extension": c.get("extension"),
                    "extname": c.get("extname"),
                    # Vitel's authoritative recording filename (may be absent).
                    "vitel_recording_filename": clean_recording_filename(c.get("callrecordfilename")),
                    "audio_s3_bucket": None,
                    "audio_s3_key": None,
                    "audio_s3_uri": None,
                    "transcript": None,
                    "processing_status": "not processed",
                    "error_message": None,
                    "registered_at": pytz.timezone("America/Chicago").localize(datetime.combine(call_date, call_time)),
                    "updated_at": self._utcnow(),
                    "client_id": client.client_id if client else None,
                })

            inserted_ids = []
            if rows:
                # The existing-key check prevents revised Vitel CDR durations
                # from becoming a second call.  The database constraint still
                # protects concurrent inserts of the exact same source CDR.
                stmt = (
                    pg_insert(Call.__table__)
                    .values(rows)
                    .on_conflict_do_nothing(
                        constraint="uq_calls_date_time_src_dst_billsec",
                    )
                    .returning(Call.__table__.c.call_id)
                )
                result = db.execute(stmt)
                inserted_ids = [row[0] for row in result.fetchall()]
                skipped += len(rows) - len(inserted_ids)
                # resolve_client_for_call runs before INSERT so each row can
                # carry its client_id. Reconcile after the insert outcome is
                # known: an ignored duplicate must never inflate lifecycle
                # totals.
                reconcile_client_call_totals(db, (row["client_id"] for row in rows))

            if filename_backfill:
                db.execute(
                    text(
                        "UPDATE calls SET vitel_recording_filename = :f "
                        "WHERE call_date = :d AND call_time = :t AND src IS NOT DISTINCT FROM :s "
                        "AND dst IS NOT DISTINCT FROM :ds AND vitel_recording_filename IS NULL"
                    ),
                    filename_backfill,
                )

            db.commit()

            logger.info(
                f"Inserted {len(inserted_ids)} call records into database. "
                f"Skipped {skipped} duplicate(s)."
            )

            return inserted_ids, skipped

        except Exception as e:
            db.rollback()
            logger.error(f"Failed to insert calls: {str(e)}")
            raise
        finally:
            db.close()

    def _get_latest_batch(self, target_date=None):
        try:
            central = pytz.timezone("US/Central")

            if target_date is None:
                target_date = datetime.now(central).date()

            audio_files = []

            prefix = (
                f"vitel-call-recordings/"
                f"{target_date.year}/"
                f"{target_date.month:02d}/"
                f"{target_date.day:02d}/"
            )

            if hasattr(self.s3, "get_paginator"):
                paginator = self.s3.get_paginator("list_objects_v2")
            elif hasattr(self.s3, "client"):
                paginator = self.s3.client.get_paginator("list_objects_v2")
            else:
                raise AttributeError(
                    "S3Service does not expose get_paginator() or a .client "
                    "with get_paginator(); update _get_latest_batch to match "
                    "the actual S3Service interface."
                )

            pages = paginator.paginate(
                Bucket=self.bucket,
                Prefix=prefix
            )

            for page in pages:
                if "Contents" not in page:
                    continue

                for obj in page["Contents"]:
                    if obj["Key"].endswith("/"):
                        continue

                    if not obj["Key"].lower().endswith(".wav"):
                        continue

                    last_modified = obj["LastModified"].astimezone(central)

                    audio_files.append({
                        "key": obj["Key"],
                        "size": obj["Size"],
                        "last_modified": last_modified,
                        "bucket": self.bucket,
                        "s3_uri": f"s3://{self.bucket}/{obj['Key']}"
                    })

            if len(audio_files) == 0:
                logger.info(f"0 audio files found in {prefix}")
            else:
                logger.info(f"Retrieved {len(audio_files)} audio files from {prefix}")

            return audio_files

        except Exception as e:
            logger.error(f"Failed to retrieve audio files from S3: {str(e)}")
            raise

    def _get_wav_duration_seconds(self, key):
        """
        Read only the WAV header (first few KB via S3 range request) and compute
        duration from the RIFF/WAVE metadata without downloading the full object.

        The recordings are not always plain PCM WAVs; some are GSM WAVs (fmt tag 49).
        The stdlib wave module can parse plain PCM but fails on GSM-compressed WAVs,
        so we parse the RIFF fmt/data chunks directly instead of relying on the
        partial-file wave reader.
        """
        try:
            if hasattr(self.s3, "client"):
                s3_client = self.s3.client
            elif hasattr(self.s3, "get_object"):
                s3_client = self.s3
            else:
                logger.warning("S3Service has no usable client for range-get; skipping duration probe.")
                return None

            # WAV headers are tiny; 4KB is generous for RIFF/WAVE metadata even when
            # the file uses a non-PCM format like GSM 6.10 WAV.
            resp = s3_client.get_object(Bucket=self.bucket, Key=key, Range="bytes=0-4095")
            body = resp["Body"]
            try:
                data = body.read()
            finally:
                if hasattr(body, "close"):
                    body.close()
            if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
                logger.warning(f"Could not determine duration for '{key}': not a RIFF/WAVE object")
                return None

            offset = 12
            fmt_block = None
            data_size = None
            while offset + 8 <= len(data):
                chunk_id = data[offset:offset + 4]
                chunk_size = int.from_bytes(data[offset + 4:offset + 8], "little")
                chunk_start = offset + 8
                chunk_end = chunk_start + chunk_size
                if chunk_id == b"data":
                    data_size = chunk_size
                    break
                if chunk_end > len(data):
                    break

                if chunk_id == b"fmt ":
                    if chunk_size < 16:
                        logger.warning(f"Could not determine duration for '{key}': malformed fmt chunk")
                        return None
                    fmt_block = data[chunk_start:chunk_start + 16]
                elif chunk_id == b"data":
                    data_size = int.from_bytes(data[offset + 4:offset + 8], "little")

                offset = chunk_end + (chunk_size % 2)

            if fmt_block is None:
                logger.warning(f"Could not determine duration for '{key}': missing fmt chunk")
                return None

            audio_format = int.from_bytes(fmt_block[0:2], "little")
            sample_rate = int.from_bytes(fmt_block[4:8], "little")
            byte_rate = int.from_bytes(fmt_block[8:12], "little")
            block_align = int.from_bytes(fmt_block[12:14], "little")

            # Common WAV encodings: PCM (1), GSM (49), and other standard codecs.
            # For all of these, the duration is the data byte length divided by the
            # byte rate reported in the fmt chunk. This avoids the stdlib wave parser
            # failure on GSM-compressed WAV files while keeping the probe small.
            if sample_rate <= 0 or byte_rate <= 0:
                logger.warning(
                    f"Could not determine duration for '{key}': invalid WAV metadata "
                    f"(format={audio_format}, sample_rate={sample_rate}, byte_rate={byte_rate})"
                )
                return None

            if data_size is None:
                # When the range read does not include the full data chunk, fall back to
                # the object size minus the RIFF/WAVE header. This is still much smaller
                # than downloading the whole file and is correct for the recorded files.
                try:
                    head = s3_client.head_object(Bucket=self.bucket, Key=key)
                    size = int(head.get("ContentLength") or 0)
                    if size > 44:
                        data_size = max(size - 44, 0)
                except Exception:
                    data_size = 0

            if data_size is None or data_size <= 0:
                logger.warning(f"Could not determine duration for '{key}': missing data chunk length")
                return None

            duration = data_size / float(byte_rate)
            if duration <= 0:
                logger.warning(f"Could not determine duration for '{key}': computed duration was <= 0")
                return None

            return duration

        except Exception as e:
            logger.warning(f"Could not determine duration for '{key}': {str(e)}")
            return None

    def _parse_audio_filename(self, key):
        try:
            filename = os.path.basename(key)

            if not filename.lower().endswith(".wav"):
                return None

            name = filename[:-4]
            parts = name.split("-")

            if len(parts) < 6:
                logger.warning(f"Invalid audio filename format: {filename}")
                return None

            direction = parts[0]
            dst = parts[1]
            src = parts[2]
            date_str = parts[3]
            time_str = parts[4]
            uid = "-".join(parts[5:])

            call_datetime = datetime.strptime(
                f"{date_str}{time_str}",
                "%Y%m%d%H%M%S"
            )

            return {
                "direction": direction,
                "dst": dst,
                "src": src,
                "call_date": call_datetime.date(),
                "call_time": call_datetime.time(),
                "call_datetime": call_datetime,
                "uid": uid,
                "filename": filename,
                "s3_key": key,
                "s3_uri": f"s3://{self.bucket}/{key}"
            }

        except Exception as e:
            logger.error(f"Failed to parse audio filename '{key}': {str(e)}")
            return None

    def _normalize_phone(self, value):
        digits = "".join(ch for ch in str(value) if ch.isdigit())
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        return digits

    def _source_matches(self, audio, db_call):
        audio_src = str(audio.get("src", "")).strip()
        db_src = str(db_call.src).strip() if db_call.src is not None else ""

        if audio_src in {"101", "102"}:
            return db_src == "13128781255"

        # A direct phone-number match is conclusive, even when the CDR's
        # extension happens to contain the same number.  Checking the
        # extension rule first made Hannah AI recordings fail: the real
        # caller number was incorrectly treated as an extension and then
        # rejected against its trunk-parent list.
        if self._normalize_phone(audio_src) == self._normalize_phone(db_src):
            return bool(self._normalize_phone(audio_src))

        # Outbound recordings can use the answering extension as their source,
        # while Vitel stores the public lead number in the CDR. Resolve the
        # extension's effective-dated assignment, so moving an extension does
        # not make historic audio cross-match to a different lead line.
        #
        # Vitel's CDR `extension` column normally holds the agent extension
        # (101/102/201/203/204/205) for outbound calls, and the original logic
        # below matches the recording's extension against it.
        #
        # For a few hours from 2026-10-06 13:14 CT Vitel instead logged the
        # dialed client number there, so the extension in the audio FILENAME is
        # the only reliable one in that case. Supporting both keeps matching
        # correct whichever format Vitel sends: the original rule applies when
        # the CDR extension equals the recording's extension, and the fallback
        # applies only when the CDR extension is just a copy of `dst`.
        if audio_src:
            if not getattr(self, "_configured_numbers", set()):
                self._load_sync_filters()

            cdr_extension = str(getattr(db_call, "extension", "") or "").strip()
            cdr_dst = str(getattr(db_call, "dst", "") or "").strip()
            if audio_src == cdr_extension or (cdr_extension and cdr_extension == cdr_dst):
                parents = self._parent_numbers_for_extension_at_date(
                    audio_src,
                    getattr(db_call, "call_date", None),
                )
                return self._normalize_phone(db_src) in parents

        return self._normalize_phone(audio_src) == self._normalize_phone(db_src)

    def _dst_matches(self, audio, db_call):
        audio_dst = str(audio.get("dst", "")).strip()
        db_dst = str(db_call.dst).strip() if db_call.dst is not None else ""
        direction = str(audio.get("direction", "")).strip().lower()
        normalized_audio_dst = self._normalize_phone(audio_dst)
        normalized_db_dst = self._normalize_phone(db_dst)
        # A routing extension may appear as +1603 as well as 603. Unlike a
        # normal NANP number it is only three digits, so _normalize_phone
        # deliberately leaves its leading 1 intact; remove that optional
        # country-code prefix for this routing-only comparison.
        routing_audio_dst = "".join(ch for ch in audio_dst if ch.isdigit())
        routing_db_dst = "".join(ch for ch in db_dst if ch.isdigit())
        if len(routing_audio_dst) == 4 and routing_audio_dst.startswith("1"):
            routing_audio_dst = routing_audio_dst[1:]
        if len(routing_db_dst) == 4 and routing_db_dst.startswith("1"):
            routing_db_dst = routing_db_dst[1:]

        if db_dst == "601" and audio_dst == "3128781255":
            return True

        if db_dst == "603" and audio_dst == "2243374447":
            return True

        if db_dst == "605" and audio_dst == "2243939098":
            return True

        # dst = 602 / 2243860601 route to Hannah AI (the automated
        # attendant); its recordings are filed under whichever underlying
        # trunk actually took the call, not "602"/"2243860601" themselves.
        # Empirically confirmed against 2026-09-17's real S3 filenames: every
        # one of that date's missing-audio calls to these two destinations
        # matched one of these three trunk dst values by src+timestamp
        # (3128781255 and 603 are already-configured trunks; 6304452050 is
        # not otherwise configured but only ever appears for this alias).
        # Hannah AI recordings are stored against the underlying trunk, not
        # the hand-off extension. Cover both Front Desk (602) and Website
        # (604) routes and normalize country-code formatting in filenames.
        if routing_db_dst in {"602", "604", "2243860601"} and (
            normalized_audio_dst in {"3128781255", "6304452050"}
            or routing_audio_dst == "603"
        ):
            return True

        if audio_dst == db_dst:
            return True

        # Ordinary Vitel/S3 records can represent the same US number as a
        # 10-digit value, 11-digit NANP value, or a formatted value.  The
        # configured-number filter already treats those forms as equivalent;
        # audio matching must do the same.  Keep the explicit routing-trunk
        # mappings above because 601/603 are not the canonical phone number
        # encoded in the recording filename.
        if self._normalize_phone(audio_dst) == self._normalize_phone(db_dst):
            return True

        if direction == "in" and audio_dst == "3128781255":
            return db_dst == "vmu3128781255"

        if audio_dst.startswith("in") and audio_dst[2:] == "3128781255":
            return db_dst == "vmu3128781255"

        if audio_dst == "in3128781255":
            return db_dst == "vmu3128781255"

        if direction == "in" and audio_dst == "2243374447":
            return db_dst == "vmu2243374447"

        if audio_dst.startswith("in") and audio_dst[2:] == "2243374447":
            return db_dst == "vmu2243374447"

        if audio_dst == "in2243374447":
            return db_dst == "vmu2243374447"

        return False

    def _get_match_tolerance(self, audio, db_call):
        if self._dst_matches(audio, db_call):
            return self.MATCH_TOLERANCE_SECONDS + 30
        return self.MATCH_TOLERANCE_SECONDS

    # Weight given to duration-fit vs. timestamp-fit when ranking candidates.
    # A billsec that's way off from the audio's real length should lose to a
    # candidate with a slightly worse timestamp but a much better duration match.
    DURATION_MISMATCH_PENALTY_PER_SECOND = 2.0

    def _match_audio(self, inserted_calls, audio_files):
        try:
            def parse_and_probe(audio):
                parsed = self._parse_audio_filename(audio["key"])
                if parsed:
                    parsed["duration_seconds"] = self._get_wav_duration_seconds(audio["key"])
                return parsed

            with ThreadPoolExecutor(max_workers=F1_IO_WORKERS, thread_name_prefix="f1-audio") as executor:
                parsed_audio_files = [
                    parsed for parsed in tqdm(
                        executor.map(parse_and_probe, audio_files),
                        total=len(audio_files), desc="Parsing audio files", unit="file",
                    ) if parsed
                ]
            duration_failure_count = sum(
                parsed["duration_seconds"] is None for parsed in parsed_audio_files
            )

            logger.info(
                "[DURATION_CHECK] Audio duration probe summary: "
                f"{len(parsed_audio_files)} files inspected, "
                f"{duration_failure_count} failed duration lookup, "
                f"{len(parsed_audio_files) - duration_failure_count} succeeded."
            )

            candidates = []
            for db_call in tqdm(inserted_calls, desc="Building match candidates", unit="call"):
                call_datetime = datetime.combine(db_call.call_date, db_call.call_time)

                for audio in parsed_audio_files:
                    if not self._dst_matches(audio, db_call):
                        continue
                    if not self._source_matches(audio, db_call):
                        continue
                    if audio["call_date"] != db_call.call_date:
                        continue

                    diff = abs((audio["call_datetime"] - call_datetime).total_seconds())
                    if diff > self._get_match_tolerance(audio, db_call):
                        continue

                    duration = audio.get("duration_seconds")
                    if duration is not None:
                        duration_mismatch = abs(duration - int(db_call.billsec or 0))
                    else:
                        # No duration available (probe failed) — fall back to
                        # timestamp-only scoring for this pair.
                        duration_mismatch = 0

                    score = diff + duration_mismatch * self.DURATION_MISMATCH_PENALTY_PER_SECOND
                    candidates.append((score, db_call, audio))

            candidates.sort(key=lambda c: c[0])

            matched_call_ids = set()
            matched_audio_keys = set()
            mappings = []

            for score, db_call, audio in tqdm(candidates, desc="Assigning matches", unit="pair"):
                call_key = id(db_call)
                audio_key = audio["s3_key"]

                if call_key in matched_call_ids or audio_key in matched_audio_keys:
                    continue

                matched_call_ids.add(call_key)
                matched_audio_keys.add(audio_key)
                mappings.append({
                    "db_call": db_call,
                    "audio": audio
                })

            logger.info(f"Matched {len(mappings)} of {len(inserted_calls)} call records.")
            return mappings

        except Exception as e:
            logger.error(f"Failed to match audio files: {str(e)}")
            raise

    # ------------------------------------------------------------------
    # Audio resolution: Vitel filename first, legacy matcher as fallback
    # ------------------------------------------------------------------
    # Vitel now provides the authoritative call recording filename in the CDR
    # metadata (``callrecordfilename``).  The legacy filename
    # reconstruction/matching path (_match_audio and its helpers) is bypassed
    # for any call that has one, and is retained as the fallback for
    # incomplete/legacy metadata (calls with no Vitel filename).

    def _same_day_vitel_recordings(self, dates):
        """Every stored call on ``dates`` that carries a Vitel filename.

        Includes calls whose audio is already mapped: recording ownership
        (recording_owner_ids) must be decided across ALL legs of an
        interaction, not just the ones still missing audio.
        """
        db = SessionLocal()
        try:
            return (
                db.query(
                    Call.call_id, Call.call_date, Call.call_time, Call.dst,
                    Call.billsec, Call.disposition, Call.vitel_recording_filename,
                )
                .filter(
                    Call.call_date.in_(dates),
                    Call.vitel_recording_filename.isnot(None),
                    Call.vitel_recording_filename != "",
                )
                .all()
            )
        finally:
            db.close()

    def _vitel_recording_mappings(self, calls, audio_files):
        """Map calls to recordings using the filename Vitel supplied.

        Returns ``(mappings, spoken_for)``: mappings in the same shape
        _match_audio returns (so _update_audio_paths is shared), and the set of
        lower-cased filenames Vitel has assigned to some call that day, which
        the legacy matcher must not reuse.

        A recording is attached to exactly one call: Vitel stamps the same
        filename on every leg of an interaction (e.g. a ring-route leg that
        fell through to the AI route), so only the leg that carried the
        conversation owns it (recording_owner_ids).  A call whose file is not
        in S3 yet is left unmapped and retried on the next run; it does NOT
        fall back to the legacy matcher, which could attach the wrong file.
        """
        named = [c for c in calls if clean_recording_filename(getattr(c, "vitel_recording_filename", None))]
        if not named:
            return [], set()

        dates = {c.call_date for c in named}
        stored = self._same_day_vitel_recordings(dates)
        spoken_for = {
            clean_recording_filename(row.vitel_recording_filename).lower() for row in stored
        } | {clean_recording_filename(c.vitel_recording_filename).lower() for c in named}
        owner_ids = recording_owner_ids(stored)

        s3_by_name = {os.path.basename(a["key"]).lower(): a for a in audio_files}
        mappings = []
        for call in named:
            if call.call_id not in owner_ids:
                continue
            s3_object = s3_by_name.get(clean_recording_filename(call.vitel_recording_filename).lower())
            if s3_object is None:
                continue
            audio = self._parse_audio_filename(s3_object["key"]) or {
                "direction": "", "src": "", "dst": "", "filename": os.path.basename(s3_object["key"]),
                "s3_key": s3_object["key"], "s3_uri": s3_object["s3_uri"],
            }
            audio["duration_seconds"] = self._get_wav_duration_seconds(s3_object["key"])
            mappings.append({"db_call": call, "audio": audio})

        logger.info(
            f"Vitel filename: mapped {len(mappings)} of {len(named)} call(s) that carry a recording filename."
        )
        return mappings, spoken_for

    def _map_audio(self, calls, audio_files):
        """Vitel recording filename first; legacy matcher only as fallback."""
        vitel_mappings, spoken_for = self._vitel_recording_mappings(calls, audio_files)

        # Calls with a Vitel filename never reach the legacy matcher.
        legacy_calls = [c for c in calls if not clean_recording_filename(getattr(c, "vitel_recording_filename", None))]
        legacy_audio = audio_files
        if spoken_for:
            legacy_audio = [a for a in audio_files if os.path.basename(a["key"]).lower() not in spoken_for]
        # LEGACY PATH (fallback): timestamp/duration based matching, unchanged.
        legacy_mappings = self._match_audio(legacy_calls, legacy_audio) if legacy_calls else []
        return vitel_mappings + legacy_mappings

    @staticmethod
    def extension_from_audio(audio, db_call):
        """The agent extension to store on a call, taken from its recording.

        TEMPORARY (Vitel CDR format change, first seen 2026-10-06 13:14 CT):
        for outbound calls Vitel now logs the dialed client number in the CDR
        ``extension`` column instead of the agent extension, which every
        assignment lookup (display, edit/complete access, agent filters,
        dashboard) relies on.  The recording filename
        (``out-<dst>-<extension>-...``) still carries it, so once a recording
        is linked, write it back to the call exactly as the original format
        stored it.  Returns None when no correction applies (original format,
        inbound, or a recording not filed under an extension/lead number).
        Remove once Vitel restores the format.
        """
        extension = str(audio.get("src", "")).strip()
        cdr_extension = str(getattr(db_call, "extension", "") or "").strip()
        cdr_dst = str(getattr(db_call, "dst", "") or "").strip()
        if (
            cdr_extension
            and cdr_extension == cdr_dst
            and str(audio.get("direction", "")).strip().lower() == "out"
            and re.fullmatch(r"\d{3}|\d{10}", extension)
        ):
            return extension
        return None

    def _update_audio_paths(self, audio_mappings):
        # Acquire session only for this operation, then close it.
        db = SessionLocal()
        try:
            updated_count = 0

            for mapping in audio_mappings:
                # Match candidates can originate from another short-lived
                # session (for example, the same-day rematch path).  Reload
                # the row in this write session so the audio URI update is
                # tracked and committed instead of being applied to a
                # detached SQLAlchemy object.
                matched_call = mapping["db_call"]
                db_call = (
                    db.query(Call)
                    .filter(Call.call_id == matched_call.call_id)
                    .first()
                )
                if db_call is None:
                    logger.warning(
                        "Skipping audio mapping because call %s no longer exists.",
                        matched_call.call_id,
                    )
                    continue
                audio = mapping["audio"]

                db_call.audio_s3_key = audio["s3_key"]
                db_call.audio_s3_bucket = self.bucket
                db_call.audio_s3_uri = audio["s3_uri"]
                # TEMPORARY: restore the agent extension Vitel no longer sends.
                corrected_extension = self.extension_from_audio(audio, db_call)
                if corrected_extension:
                    if str(db_call.extname or "").strip() == str(db_call.dst or "").strip():
                        db_call.extname = corrected_extension
                    db_call.extension = corrected_extension
                # The duration shown in FlintDesk is the recording length,
                # not Vitel's billable-seconds estimate.  The matcher already
                # reads the WAV metadata, so persist that authoritative value
                # whenever a valid recording is attached.
                audio_duration = audio.get("duration_seconds")
                if audio_duration is not None and audio_duration > 0:
                    intended_billsec = int(audio_duration)
                    # Historical Vitel leg duplicates can already occupy the
                    # legacy unique key with this recording's duration.  Do
                    # not let that one stale duplicate roll back every audio
                    # mapping in the batch. The audio URI is authoritative;
                    # preserve this row's source billsec in that narrow case.
                    duration_conflict = (
                        db.query(Call.call_id)
                        .filter(
                            Call.call_id != db_call.call_id,
                            Call.call_date == db_call.call_date,
                            Call.call_time == db_call.call_time,
                            Call.src == db_call.src,
                            Call.dst == db_call.dst,
                            Call.billsec == intended_billsec,
                        )
                        .first()
                    )
                    if duration_conflict:
                        logger.warning(
                            "Preserving source billsec for duplicate CDR %s; "
                            "the recording duration is already held by %s.",
                            db_call.call_id,
                            duration_conflict[0],
                        )
                    else:
                        db_call.billsec = intended_billsec
                db_call.processing_status = "not processed"
                db_call.updated_at = self._utcnow()

                updated_count += 1

            db.flush()
            db.commit()
            logger.info(f"Updated audio paths for {updated_count} call records.")

        except Exception as e:
            db.rollback()
            logger.error(f"Failed to update audio paths: {str(e)}")
            raise
        finally:
            db.close()

    def _missing_audio_calls_for_date(self, target_date, phone_number=None):
        """Return same-day calls that still need an S3 audio mapping.

        With no phone scope, this is the normal scheduler recovery path: every
        new or existing client call without an audio URI is eligible. A supplied
        phone number retains the existing explicit, configured-line-only mode.
        """
        requested_number = None
        if phone_number is not None:
            requested_number = self._normalize_number(phone_number)
            _, configured_numbers = self._load_sync_filters()
            if not requested_number or requested_number not in configured_numbers:
                raise ValueError(
                    f"Phone number {phone_number!r} is not configured in fd_sync_filters"
                )

        if isinstance(target_date, str):
            target_date = datetime.strptime(target_date, "%Y-%m-%d").date()

        # Acquire session only for this query
        db = SessionLocal()
        try:
            candidates = (
                db.query(Call)
                .filter(
                    Call.call_date == target_date,
                    (Call.audio_s3_uri.is_(None)) | (Call.audio_s3_uri == ""),
                )
                .order_by(Call.call_time.asc())
                .all()
            )
            # Never remap a second Vitel CDR leg when its physical call is
            # already audio-mapped.  That duplicate cannot add a recording,
            # but it can collide with the legacy duration uniqueness key and
            # roll back the whole F1 batch.
            mapped_identities = {
                self._physical_call_identity(call)
                for call in db.query(Call)
                .filter(
                    Call.call_date == target_date,
                    Call.audio_s3_uri.isnot(None),
                    Call.audio_s3_uri != "",
                )
                .all()
            }
            candidates = [
                call for call in candidates
                if self._physical_call_identity(call) not in mapped_identities
            ]
            if requested_number is None:
                return candidates
            return [
                call for call in candidates
                if self._normalize_number(call.src) == requested_number
                or self._normalize_number(call.dst) == requested_number
            ]
        finally:
            db.close()

    @staticmethod
    def _physical_call_identity(call):
        """Identity used for a recording; billsec may change across Vitel legs."""
        return (
            call.call_date,
            call.call_time,
            str(call.src or "").strip(),
            str(call.dst or "").strip(),
        )

    def rematch_missing_audio(self, target_date, phone_number):
        """Idempotently attach audio to existing, scoped calls with no URI.

        This intentionally does not call Vital, create calls, or overwrite any
        existing audio path.  It is the recovery path for CDRs whose actual
        ``call_date`` differed from the F1 fetch date, so their recording was
        not present in the original S3 date-prefix scan.
        """
        try:
            if isinstance(target_date, str):
                target_date = datetime.strptime(target_date, "%Y-%m-%d").date()

            calls = self._missing_audio_calls_for_date(target_date, phone_number)
            logger.info(
                "F1 audio rematch for %s on %s: %s existing call(s) without audio.",
                self._normalize_number(phone_number),
                target_date,
                len(calls),
            )
            if not calls:
                return {"candidates": 0, "matched": 0}

            audio_files = self._get_latest_batch(target_date)
            mappings = self._map_audio(calls, audio_files)
            if mappings:
                self._update_audio_paths(mappings)

            logger.info(
                "F1 audio rematch complete for %s: matched %s of %s call(s).",
                target_date,
                len(mappings),
                len(calls),
            )
            return {"candidates": len(calls), "matched": len(mappings)}
        except Exception:
            raise

    def _log_sync(self, total_fetched, total_inserted, total_matched, skipped, status, error=None):
        log_session = SessionLocal()
        try:
            central = pytz.timezone('US/Central')
            now_ct = datetime.now(central)

            sync_log = SyncLog(
                sync_log_id=uuid4(),
                source_system="vitel",
                sync_action="fetch_calls",
                batch_date=now_ct.date(),
                started_at=now_ct,
                completed_at=now_ct,
                status=status,
                total_records=total_fetched,
                synced_records=total_inserted,
                failed_records=0,
                error_message=error,
            )
            log_session.add(sync_log)
            log_session.commit()
            logger.info(
                f"Sync log recorded - Status: {status}, Fetched: {total_fetched}, "
                f"Inserted: {total_inserted}, Skipped(duplicates): {skipped}, "
                f"Matched: {total_matched}"
            )
            if error:
                logger.error(f"Sync error details: {error}")
        except Exception as e:
            log_session.rollback()
            logger.error(f"Failed to log sync operation: {str(e)}")
        finally:
            log_session.close()

    def run(self, target_date=None, phone_number=None):
        # NOTE: This method performs external operations WITHOUT holding a database
        # connection. Each database operation (_insert_calls, _update_audio_paths) 
        # manages its own session lifecycle to prevent connection pool exhaustion.
        total_fetched = 0
        total_matched = 0
        skipped = 0
        
        try:
            logger.info("Starting F1Processor")

            central = pytz.timezone("US/Central")

            if target_date is None:
                run_date = datetime.now(central).date()  # acc to central time
            elif isinstance(target_date, str):
                run_date = datetime.strptime(target_date, "%Y-%m-%d").date()
            else:
                run_date = target_date

            logger.info(f"F1Processor running for date: {run_date}")

            # EXTERNAL OPERATION: Fetch calls from Vitel API (no DB connection needed)
            fetched_calls = self._fetch_calls_for_date(run_date)
            total_fetched = len(fetched_calls)
            if total_fetched == 0:
                logger.info(f"0 calls found from Vitel for {run_date}")
            else:
                logger.info(f"Fetched {total_fetched} calls from Vitel")

            # EXTERNAL OPERATION: Filter/merge calls (no DB connection needed)
            filtered_calls = self._filter_calls(
                fetched_calls,
                phone_number=phone_number,
            )

            merged_calls = self._merge_call_legs(filtered_calls)

            # DATABASE OPERATION: Insert calls (manages its own session)
            inserted_calls, skipped = self._insert_calls(merged_calls)

            # EXTERNAL OPERATION: Get audio files from S3 (no DB connection needed)
            audio_files = self._get_latest_batch(run_date)
            if len(audio_files) == 0:
                logger.info(f"0 audio files found in S3 for {run_date}")
            else:
                logger.info(f"Retrieved {len(audio_files)} audio files from S3 for {run_date}")

            # A call can reach Vitel before its recording appears in S3. Always
            # rematch every same-day call that still lacks an audio URI, not
            # merely calls inserted in this run. This makes the operation
            # idempotent and works equally for new and existing clients.
            calls_needing_audio = self._missing_audio_calls_for_date(
                run_date,
                phone_number,
            )
            logger.info(
                "F1 audio mapping candidates for %s: %s call(s) without audio URI.",
                run_date,
                len(calls_needing_audio),
            )

            # EXTERNAL OPERATION: Resolve audio (Vitel filename first, legacy
            # matcher as fallback; see _map_audio)
            mappings = self._map_audio(calls_needing_audio, audio_files)
            if len(mappings) == 0:
                logger.info(
                    "0 call-audio pairs matched out of %s calls needing audio",
                    len(calls_needing_audio),
                )
            else:
                logger.info(f"Matched {len(mappings)} call-audio pairs")
                total_matched = len(mappings)

            # DATABASE OPERATION: Update audio paths (manages its own session)
            if mappings:
                self._update_audio_paths(mappings)

            # No need to commit here; _insert_calls and _update_audio_paths 
            # each manage their own sessions and commits.

            self._log_sync(
                total_fetched,
                len(inserted_calls),
                total_matched,
                skipped,
                "success"
            )
            logger.info("F1Processor completed successfully")

        except Exception as e:
            logger.error(f"F1Processor failed: {str(e)}", exc_info=True)
            self._log_sync(total_fetched, 0, 0, 0, "failed", error=str(e))
            raise


def run_date_range(start_date, end_date, phone_number=None):
    """Run F1 once per inclusive Central-date range.

    A separate processor/session is intentionally used for each date because
    ``F1Processor.run`` owns and closes its database session.  Supplying a
    phone number keeps every ingestion pass limited to that configured line.
    """
    if isinstance(start_date, str):
        start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
    if isinstance(end_date, str):
        end_date = datetime.strptime(end_date, "%Y-%m-%d").date()
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")

    current_date = start_date
    while current_date <= end_date:
        F1Processor().run(target_date=current_date, phone_number=phone_number)
        current_date += timedelta(days=1)


def rematch_missing_audio_date_range(start_date, end_date, phone_number):
    """Run the no-Vital, no-insert audio recovery once per inclusive date."""
    if isinstance(start_date, str):
        start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
    if isinstance(end_date, str):
        end_date = datetime.strptime(end_date, "%Y-%m-%d").date()
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")

    current_date = start_date
    while current_date <= end_date:
        F1Processor().rematch_missing_audio(current_date, phone_number)
        current_date += timedelta(days=1)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Fetch one day's Vitel calls into AutoFlint")
    date_group = parser.add_mutually_exclusive_group()
    date_group.add_argument("--date", help="Central date to fetch, in YYYY-MM-DD format")
    date_group.add_argument(
        "--start-date",
        help="First inclusive Central date of a backfill (requires --end-date)",
    )
    parser.add_argument(
        "--end-date",
        help="Last inclusive Central date of a backfill (requires --start-date)",
    )
    parser.add_argument(
        "--phone-number",
        help="Restrict ingestion to one configured source or destination number",
    )
    parser.add_argument(
        "--rematch-missing-audio",
        action="store_true",
        help="Attach S3 audio only to existing scoped calls that have no audio URI; does not call Vital or insert calls",
    )
    args = parser.parse_args()
    if args.rematch_missing_audio:
        if not args.phone_number:
            parser.error("--phone-number is required with --rematch-missing-audio")
        if args.start_date:
            if not args.end_date:
                parser.error("--end-date is required with --start-date")
            rematch_missing_audio_date_range(
                args.start_date,
                args.end_date,
                args.phone_number,
            )
        elif args.date:
            F1Processor().rematch_missing_audio(args.date, args.phone_number)
        else:
            parser.error("--date or --start-date/--end-date is required with --rematch-missing-audio")
    elif args.start_date:
        if not args.end_date:
            parser.error("--end-date is required with --start-date")
        run_date_range(args.start_date, args.end_date, args.phone_number)
    elif args.end_date:
        parser.error("--start-date is required with --end-date")
    else:
        F1Processor().run(target_date=args.date, phone_number=args.phone_number)
