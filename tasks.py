"""
Celery task workers for async inference and batch processing.
Handles long-running DICOM processing jobs with progress tracking via Redis.

Run worker with: celery -A tasks worker --loglevel=info
"""

import logging
import os
import shutil
import datetime
import ssl
import sys
import traceback
from pathlib import Path
from typing import Any

# Ensure the app directory is in the Python path so imports work in worker processes
APP_DIR = Path(__file__).parent.absolute()
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from celery import Celery, current_task

logger = logging.getLogger(__name__)

# Extract Redis URL from environment
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# Initialize Celery app
celery_app = Celery(
    "ich_tasks",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

# Configure Celery with SSL support for Upstash Redis
ssl_config = None
redis_backend_ssl = None
if REDIS_URL.startswith("rediss://"):
    ssl_config = {"ssl_cert_reqs": ssl.CERT_NONE}
    redis_backend_ssl = {"ssl_cert_reqs": ssl.CERT_NONE}

celery_app.conf.update(
    broker_use_ssl=ssl_config,
    redis_backend_use_ssl=redis_backend_ssl,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,  # 1 hour hard limit
    task_soft_time_limit=3300,  # 55 min soft limit
    result_expires=86400,  # 24 hours
)


@celery_app.task(bind=True, name="tasks.process_dicom_batch")
def process_dicom_batch(
    self,
    batch_id: str,
    dcm_paths: list[str],
    user_id: int,
    temp_dir: str | None = None,
) -> dict[str, Any]:
    """
    Process a batch of DICOM files asynchronously with progress tracking.
    
    Args:
        batch_id: Unique identifier for this batch job
        dcm_paths: List of DICOM file paths to process
        user_id: User ID for audit and data isolation
        temp_dir: Optional temporary directory to clean up after
    
    Returns:
        Dictionary with final batch status and results matching frontend expectations
    """
    # Import here to avoid circular imports. Add diagnostics to help debug
    # ModuleNotFoundError issues when Celery workers can't find `app_new`.
    try:
        # Ensure APP_DIR is present in sys.path for worker subprocesses
        if str(APP_DIR) not in sys.path:
            sys.path.insert(0, str(APP_DIR))
            logger.info(f"Inserted APP_DIR into sys.path: {APP_DIR}")
        else:
            logger.info(f"APP_DIR already in sys.path: {APP_DIR}")

        logger.info(f"tasks.py APP_DIR={APP_DIR}")
        logger.info(f"sys.path (first 10): {sys.path[:10]}")
        # List files in the app dir for visibility
        try:
            files = [p.name for p in Path(APP_DIR).iterdir() if p.exists()]
            logger.info(f"APP_DIR contents: {files[:50]}")
        except Exception as _e:
            logger.warning(f"Could not list APP_DIR contents: {_e}")

        from app_new import app, _run_inference_on_dcm
        from auth_utils import log_audit
        from models import ScreeningUpload, db
    except Exception as e:
        logger.error("Failed importing application modules inside Celery worker:\n" + traceback.format_exc())
        raise

    total = len(dcm_paths)
    succeeded_ids = []
    failed_ids = []
    started_at = datetime.datetime.now().isoformat()

    logger.info(f"Batch {batch_id} starting: {total} files for user {user_id}")

    try:
        with app.app_context():
            for i, path_str in enumerate(dcm_paths, 1):
                # Check if task was revoked (compat across Celery versions)
                request_ctx = current_task.request
                is_revoked = bool(getattr(request_ctx, "is_revoked", False)) or bool(
                    getattr(request_ctx, "revoked", False)
                )
                if is_revoked:
                    logger.info(f"Batch {batch_id} revoked, stopping")
                    break

                path = Path(path_str)
                image_id = path.stem

                upload_record = ScreeningUpload(
                    user_id=user_id,
                    file_name=path.name,
                    original_filename=path.name,
                    file_size=path.stat().st_size if path.exists() else None,
                    file_path=str(path),
                    processing_status="processing",
                )
                db.session.add(upload_record)
                db.session.commit()

                # Update Celery task state with progress (matches _BATCHES format for frontend)
                self.update_state(
                    state="PROGRESS",
                    meta={
                        "batch_id": batch_id,
                        "user_id": user_id,
                        "status": "running",
                        "total": total,
                        "processed": i - 1,
                        "succeeded": len(succeeded_ids),
                        "failed_ids": list(failed_ids),
                        "image_ids": list(succeeded_ids),
                        "current_file": image_id,
                        "started_at": started_at,
                        "finished_at": None,
                        "error": None,
                        "temp_dir": temp_dir,
                    },
                )

                try:
                    report, _ = _run_inference_on_dcm(path, user_id, upload_record.id)
                    if report:
                        upload_record.processing_status = "completed"
                        db.session.commit()
                        succeeded_ids.append(image_id)
                    else:
                        upload_record.processing_status = "failed"
                        db.session.commit()
                        failed_ids.append(image_id)
                except Exception as e:
                    logger.error(f"Batch {batch_id}: failed {image_id} — {e}")
                    db.session.rollback()
                    upload_record.processing_status = "failed"
                    try:
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
                    failed_ids.append(image_id)

                # Update after processing each file
                self.update_state(
                    state="PROGRESS",
                    meta={
                        "batch_id": batch_id,
                        "user_id": user_id,
                        "status": "running",
                        "total": total,
                        "processed": i,
                        "succeeded": len(succeeded_ids),
                        "failed_ids": list(failed_ids),
                        "image_ids": list(succeeded_ids),
                        "current_file": "",
                        "started_at": started_at,
                        "finished_at": None,
                        "error": None,
                        "temp_dir": temp_dir,
                    },
                )

        # Cleanup temporary directory if provided
        if temp_dir and Path(temp_dir).exists():
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
                logger.info(f"Cleaned up temp_dir: {temp_dir}")
            except Exception as e:
                logger.warning(f"Failed to clean temp_dir {temp_dir}: {e}")

        # Log final audit result
        with app.app_context():
            audit_status = "success" if len(failed_ids) == 0 else "partial"
            log_audit(
                "batch_processing_completed",
                user_id=user_id,
                details=f"batch_id={batch_id}, processed={total}, succeeded={len(succeeded_ids)}, failed={len(failed_ids)}",
                status=audit_status,
            )

        # Return final result matching _BATCHES format for frontend compatibility
        result = {
            "batch_id": batch_id,
            "user_id": user_id,
            "status": "completed",
            "total": total,
            "processed": total,
            "succeeded": len(succeeded_ids),
            "failed_ids": list(failed_ids),
            "image_ids": list(succeeded_ids),
            "current_file": "",
            "started_at": started_at,
            "finished_at": datetime.datetime.now().isoformat(),
            "error": None,
            "temp_dir": temp_dir,
        }

        logger.info(
            f"Batch {batch_id} complete: {len(succeeded_ids)}/{total} succeeded, "
            f"{len(failed_ids)} failed"
        )
        return result

    except Exception as e:
        logger.error(f"Batch {batch_id} error: {e}", exc_info=True)
        with app.app_context():
            log_audit(
                "batch_processing_failed",
                user_id=user_id,
                details=f"batch_id={batch_id}, error={str(e)}",
                status="failure",
            )
        raise


@celery_app.task(name="tasks.health_check")
def health_check() -> str:
    """Simple health check task for monitoring."""
    return "Celery worker is healthy"
