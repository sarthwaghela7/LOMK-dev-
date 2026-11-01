"""
synchronisation.py

Master orchestrator for the daily synchronization pipeline.

Execution order:
    1. Run F1Processor (fetch calls from Vitel for yesterday, match audio, insert to DB).
    2. If F1 succeeds, run F2Processor (process all unprocessed calls in DB).
    3. If F2 has internal retry, it handles itself. Otherwise, we run F2 a second time
       as a retry pass for any records that failed.
    4. Exit successfully.

Usage:
    python synchronisation.py              # Sync yesterday
    python synchronisation.py --date 2026-07-02  # Sync specific date
"""

import logging
import sys
import time
import argparse
from datetime import datetime, timedelta
import pytz
from functools import wraps

from sqlalchemy.exc import DBAPIError, OperationalError

from services.Database_sync.f1 import F1Processor
from services.Database_sync.f2 import F2Processor
from services.Database_sync.f3 import F3Processor
from utils.config import PROCESSING_APP

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("synchronisation")


def retry_on_db_error(max_attempts=3, delay_seconds=5):
    """
    Retry a step if it fails with a transient DB connection error
    (e.g. 'could not send data to server: Software caused connection abort').
    Does NOT retry on other exception types — those should fail fast.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except (DBAPIError, OperationalError) as e:
                    last_exc = e
                    logger.warning(
                        f"{func.__name__} hit a DB connection error on attempt "
                        f"{attempt}/{max_attempts}: {str(e)}"
                    )
                    if attempt < max_attempts:
                        time.sleep(delay_seconds)
            raise last_exc
        return wrapper
    return decorator


class Synchronisation:
    """
    Orchestrates F1 → F2 pipeline.
    F1 fetches calls. F2 processes all unprocessed calls in DB.
    """

    def __init__(self):
        self.logger = logger


    @retry_on_db_error(max_attempts=3, delay_seconds=5)
    def _run_f1(self, target_date=None):
        """Run F1Processor to fetch calls from Vitel."""
        self.logger.info("-" * 40)
        self.logger.info("STEP 1/3: F1Processor - Fetching calls from Vitel")
        self.logger.info("-" * 40)

        processor = F1Processor()
        try:
            processor.run(target_date=target_date)
            self.logger.info("F1Processor completed successfully")
        except Exception:
            self.logger.exception("F1Processor FAILED. Aborting pipeline — F2 will NOT run.")
            raise
        finally:
            try:
                processor.db.close()
            except:
                pass

    @retry_on_db_error(max_attempts=3, delay_seconds=5)
    def _run_f2(self, target_date):
        """Run F2Processor first pass."""
        self.logger.info("-" * 40)
        self.logger.info("STEP 2/3: F2Processor - Processing calls (first pass)")
        self.logger.info("-" * 40)

        processor = F2Processor()
        try:
            processor.run(start_date=target_date)
            self.logger.info("F2Processor first pass completed")
        except Exception:
            self.logger.exception("F2Processor first pass FAILED. Skipping retry.")
            raise
        finally:
            processor.close()

    @retry_on_db_error(max_attempts=3, delay_seconds=5)
    def _run_f2_retry(self, target_date):
        """Run F2Processor second pass as retry for failed records."""
        self.logger.info("-" * 40)
        self.logger.info("STEP 3/3: F2Processor - Retry pass for failed records")
        self.logger.info("-" * 40)

        processor = F2Processor()
        try:
            processor.run(start_date=target_date)
            self.logger.info("F2Processor retry pass completed")
        except Exception:
            self.logger.exception("F2Processor retry pass FAILED.")
            raise
        finally:
            processor.close()

    @retry_on_db_error(max_attempts=3, delay_seconds=5)
    def _run_f3(self, application=None, target_date=None):
        """Run the shared F3 LLM orchestrator for the active application."""
        app_name = (application or PROCESSING_APP or "autoflint").lower()
        self.logger.info("-" * 40)
        self.logger.info(f"STEP 4/4: F3Processor - {app_name} LLM orchestration")
        self.logger.info("-" * 40)

        processor = F3Processor(application=app_name)
        try:
            processor.run(start_date=target_date)
            self.logger.info("F3Processor completed")
        except Exception:
            self.logger.exception("F3Processor FAILED.")
            raise
        finally:
            processor.db.close()

    def run(self, target_date=None, application=None):
        """
        Execute pipeline: F1 -> F2 -> F3 for the configured application.

        Args:
            target_date: "YYYY-MM-DD" string or None (default: yesterday Central Time)
            application: "autoflint" or "flintdesk"
        """
        app_name = (application or PROCESSING_APP or "autoflint").lower()
        self.logger.info("=" * 60)
        self.logger.info(f"STARTING SYNCHRONIZATION PIPELINE ({app_name})")
        self.logger.info("=" * 60)

        if target_date:
            processing_date = target_date
            self.logger.info(f"Target date: {processing_date}")
        else:
            central = pytz.timezone("US/Central")
            processing_date = (datetime.now(central) - timedelta(days=1)).strftime("%Y-%m-%d")
            self.logger.info(f"Default date (yesterday): {processing_date}")

        self._run_f1(processing_date)
        self._run_f2(processing_date)
        self._run_f2_retry(processing_date)
        self._run_f3(app_name, processing_date)

        self.logger.info("=" * 60)
        self.logger.info("SYNCHRONIZATION COMPLETED SUCCESSFULLY")
        self.logger.info("=" * 60)
        
        
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run AutoFlint sync pipeline")
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Date to sync in YYYY-MM-DD format (default: yesterday)"
    )
    args = parser.parse_args()

    sync = Synchronisation()
    try:
        sync.run(target_date=args.date)
    except Exception:
        sys.exit(1)
