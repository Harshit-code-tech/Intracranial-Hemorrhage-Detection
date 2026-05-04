"""
ICH Screening Web Application with User Authentication & Data Privacy
======================================================================
Features:
  1. User authentication (login/register)
  2. User-specific data storage and privacy
  3. Upload .dcm files -> run AI model -> display screening report
  4. Browse past screening reports (user's data only)
  5. View execution logs (user's logs only)
  6. Production-ready security

Run:
    python app.py (gunicorn in production)
    Open http://127.0.0.1:7860
"""

# pyright: reportCallIssue=false, reportArgumentType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportMissingParameterType=false, reportAttributeAccessIssue=false, reportMissingTypeStubs=false, reportDeprecated=false

from __future__ import annotations
import run_interface as ri
import datetime
import json
import logging
import os
import shutil
import sys
import tempfile
import time
import uuid
import zipfile
import math
from dataclasses import dataclass
from getpass import getpass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

if load_dotenv:
    load_dotenv()

hf_hub_download: Any = None
try:
    import huggingface_hub
    hf_hub_download = getattr(huggingface_hub, "hf_hub_download", None)
except Exception:
    hf_hub_download = None

try:
    import blackbox_recorder as bbr
except Exception:
    class _NoopRecorder:
        def configure(self, **_kwargs: Any) -> None:
            return None
        def start(self) -> None:
            return None
        def stop(self) -> None:
            return None
        def save_report(self, _path: str) -> None:
            return None
        def save_json(self, _path: str) -> None:
            return None
    bbr = _NoopRecorder()

from flask import (
    Flask, Response, abort, flash, g, jsonify, redirect, render_template, request,
    send_from_directory, url_for
)
from types import SimpleNamespace
from celery.result import AsyncResult
from tasks import REDIS_URL, celery_app
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_login import current_user, login_required

# Import new security and auth modules
from models import db, User, ScreeningReport, ScreeningUpload, AuditLog
from auth_utils import init_auth, log_audit, get_client_ip
from auth_routes import auth_bp
from data_isolation import UserDataManager
from security import (
    init_security, sanitize_filename, check_upload_rate_limit
)

# ══════════════════════════════════════════════════════════════════════════
#  PATH CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════

BASE_DIR = Path(__file__).resolve().parent
MODEL_DIR = BASE_DIR / "download_imp"
CALIB_JSON = MODEL_DIR / "calibration_params.json"
NORM_JSON = MODEL_DIR / "normalization_stats.json"
LOGS_DIR = BASE_DIR / "logs"
UPLOAD_BASE_DIR = os.environ.get("UPLOAD_BASE_DIR", str(BASE_DIR / "uploads"))

# ══════════════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════

def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    return raw.strip().lower() in ("1", "true", "yes", "on") if raw else default

def _env_int(name: str, default: int, *, minimum: int | None = None) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
        return value if minimum is None or value >= minimum else default
    except ValueError:
        return default

APP_DEBUG = _env_bool("ICH_APP_DEBUG", False)
APP_PORT = _env_int("ICH_APP_PORT", _env_int("PORT", 7860, minimum=1), minimum=1)
MAX_UPLOAD_MB = _env_int("ICH_MAX_UPLOAD_MB", 2048, minimum=1)
LOG_LEVEL_NAME = os.environ.get("ICH_LOG_LEVEL", "INFO").strip().upper()
LOG_LEVEL = getattr(logging, LOG_LEVEL_NAME, logging.INFO)
SECRET_KEY = os.environ.get("SECRET_KEY", os.environ.get("ICH_SECRET_KEY", "")).strip()
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
HF_MODEL_REPO = os.environ.get("ICH_HF_MODEL_REPO", "").strip()
HF_TOKEN = os.environ.get("ICH_HF_TOKEN", "").strip()
LOCAL_MODE = _env_bool("ICH_LOCAL_MODE", True)
SHOW_LOGS = _env_bool("ICH_SHOW_LOGS", False)
GPU_BATCH_ENABLED = _env_bool("ICH_GPU_BATCH_INFERENCE", True)
GPU_BATCH_SIZE = _env_int("ICH_GPU_BATCH_SIZE", 2, minimum=1)
GPU_QUEUE_ENABLED = _env_bool("ICH_GPU_QUEUE_ENABLED", False)
GPU_QUEUE_NAME = os.environ.get("ICH_GPU_QUEUE_NAME", "gpu").strip() or "gpu"
CPU_QUEUE_NAME = os.environ.get("ICH_CPU_QUEUE_NAME", "cpu").strip() or "cpu"
IST = ZoneInfo("Asia/Kolkata")

def _now_ist() -> datetime.datetime:
    return datetime.datetime.now(IST).replace(tzinfo=None)

def _as_ist(dt: datetime.datetime | None) -> datetime.datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(IST)

def _format_dt_ist(dt: datetime.datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    local = _as_ist(dt)
    return local.strftime(fmt) if local else "—"

def _format_iso_ist(value: str | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    if not value:
        return "—"
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except Exception:
        return value[:16]
    return _format_dt_ist(parsed, fmt)

def _to_ist_naive(dt: datetime.datetime | None) -> datetime.datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(IST).replace(tzinfo=None)

def _cuda_available() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False

# ══════════════════════════════════════════════════════════════════════════
#  FLASK APP SETUP
# ══════════════════════════════════════════════════════════════════════════

app = Flask(__name__, template_folder="templates", static_folder="static")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

# Configuration
app.config.update(
    MAX_CONTENT_LENGTH=MAX_UPLOAD_MB * 1024 * 1024,
    SECRET_KEY=SECRET_KEY or os.urandom(32).hex(),
    DEBUG=APP_DEBUG and os.environ.get("FLASK_ENV") == "development",
    SQLALCHEMY_DATABASE_URI=DATABASE_URL or "sqlite:///ich_app.db",
    SQLALCHEMY_ENGINE_OPTIONS={
        "pool_pre_ping": True,
        "pool_recycle": 280,
    },
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SESSION_COOKIE_SECURE=not APP_DEBUG,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(days=30),
)

# Initialize extensions
db.init_app(app)
init_auth(app)
init_security(app)

# Register blueprints
app.register_blueprint(auth_bp)

@app.context_processor
def inject_feature_flags():
    log_count = 0
    if SHOW_LOGS and LOGS_DIR.exists():
        try:
            log_count = sum(1 for path in LOGS_DIR.iterdir() if path.suffix == ".json")
        except OSError:
            log_count = 0
    return {"show_logs": SHOW_LOGS, "log_count": log_count}

# ══════════════════════════════════════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("ich_app")

# ══════════════════════════════════════════════════════════════════════════
#  DATABASE INITIALIZATION
# ══════════════════════════════════════════════════════════════════════════

def init_db():
    """Initialize database tables"""
    with app.app_context():
        db.create_all()
        logger.info("Database initialized")

# ══════════════════════════════════════════════════════════════════════════
#  MODEL & INFERENCE STATE
# ══════════════════════════════════════════════════════════════════════════

LOGS_DIR.mkdir(parents=True, exist_ok=True)
bbr.configure(
    include=["run_interface", "app"],
    capture_args=True,
    capture_returns=True,
    sampling_rate=1.0,
)

_MODEL: dict[str, Any] = {
    "loaded": False,
    "model": None,
    "grad_cam": None,
    "loaded_folds": [],
    "transform": None,
    "device": None,
    "temperature": None,
    "calib_cfg": None,
    "inference_mod": None,
}

# ══════════════════════════════════════════════════════════════════════════
#  MODEL LOADING
# ══════════════════════════════════════════════════════════════════════════

def _required_model_files(fold_selection: str) -> list[str]:
    """Get list of required model files"""
    files = ["calibration_params.json", "normalization_stats.json"]
    raw = (fold_selection or "ensemble").strip().lower()
    if raw in ("", "ensemble", "all"):
        files.extend([f"best_model_fold{i}.pth" for i in range(5)])
    elif raw == "best":
        files.append("best_model_fold4.pth")
    elif raw.isdigit():
        files.append(f"best_model_fold{int(raw)}.pth")
    else:
        files.extend([f"best_model_fold{i}.pth" for i in range(5)])
    return files

def _download_runtime_artifacts_if_needed(fold_selection: str) -> bool:
    """Download missing model files from Hugging Face"""
    required_files = _required_model_files(fold_selection)
    missing = [f for f in required_files if not (MODEL_DIR / f).exists()]
    
    if not missing:
        return True
    if not HF_MODEL_REPO or not hf_hub_download:
        logger.warning(f"Missing model files and HF_MODEL_REPO not configured: {missing}")
        return False
    
    try:
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        for filename in missing:
            logger.info(f"Downloading {filename}...")
            hf_hub_download(
                repo_id=HF_MODEL_REPO,
                filename=filename,
                repo_type="model",
                local_dir=str(MODEL_DIR),
                token=HF_TOKEN or None,
            )
        return True
    except Exception as e:
        logger.error(f"Failed downloading model artifacts: {e}")
        return False

def _ensure_model_loaded() -> bool:
    """Lazy-load ML model on first inference"""
    if _MODEL["loaded"]:
        return True
    
    try:
        import torch
        sys.path.insert(0, str(BASE_DIR))
        
        device = "cuda" if torch.cuda.is_available() else "cpu"
        fold_selection = os.environ.get("ICH_FOLD_SELECTION", "ensemble")
        
        if not _download_runtime_artifacts_if_needed(fold_selection):
            return False
        
        if not CALIB_JSON.exists():
            logger.error(f"Calibration file not found: {CALIB_JSON}")
            return False
        
        with open(CALIB_JSON) as f:
            calib_cfg = json.load(f)
        
        if NORM_JSON.exists():
            with open(NORM_JSON) as f:
                norm = json.load(f)
            mean = norm.get("mean_3ch", [0.162136, 0.141483, 0.183675])
            std = norm.get("std_3ch", [0.312067, 0.283885, 0.305968])
        else:
            mean, std = [0.485, 0.456, 0.406], [0.229, 0.224, 0.225]
        
        models, grad_cams, loaded_folds = ri.load_runtime_models(device, fold_selection)
        if not models:
            logger.error(f"Failed to load model checkpoints from {MODEL_DIR}")
            return False
        
        transform = ri.T.Compose([
            ri.T.ToPILImage(),
            ri.T.ToTensor(),
            ri.T.Normalize(mean=mean, std=std),
        ])
        
        _MODEL.update({
            "loaded": True,
            "model": models,
            "grad_cam": grad_cams,
            "loaded_folds": loaded_folds,
            "transform": transform,
            "device": device,
            "temperature": float(calib_cfg.get("temperature", 1.0)),
            "calib_cfg": calib_cfg,
            "inference_mod": ri,
        })
        logger.info(f"Model loaded: device={device}, folds={loaded_folds}")
        return True
    
    except Exception as e:
        logger.error(f"Model loading failed: {e}", exc_info=True)
        return False


def _gpu_batch_ready() -> bool:
    if not GPU_BATCH_ENABLED:
        return False
    if not _ensure_model_loaded():
        return False
    return _MODEL.get("device") == "cuda"


def _infer_images_batch(dcm_paths: list[Path]) -> list[tuple[Any, dict[str, Any]]]:
    if not _ensure_model_loaded():
        raise RuntimeError("Model not loaded")

    ri_mod = _MODEL["inference_mod"]
    images = [ri_mod.dicom_to_rgb(str(path), size=ri_mod.IMG_SIZE) for path in dcm_paths]
    inferences = ri_mod.infer_batch(
        images,
        _MODEL["model"],
        _MODEL["grad_cam"],
        _MODEL["transform"],
        _MODEL["device"],
        _MODEL["temperature"],
    )
    return list(zip(images, inferences, strict=False))


def _persist_inference_result(
    image_id: str,
    user_id: int,
    upload_id: int,
    img_rgb: Any,
    inference: dict[str, Any],
) -> dict[str, Any]:
    ri_mod = _MODEL["inference_mod"]
    user_reports_dir = UserDataManager().get_user_reports_dir(user_id)
    user_reports_dir.mkdir(parents=True, exist_ok=True)

    report = ri_mod.build_report(
        image_id,
        inference,
        _MODEL["calib_cfg"],
        user_reports_dir,
        img_rgb,
        true_label=None,
    )

    pred = report.get("prediction", {})
    pred.setdefault("raw_probability", inference.get("raw_prob_any"))
    pred.setdefault("calibrated_probability", inference.get("cal_prob_any"))
    pred.setdefault("decision_threshold", pred.get("decision_threshold_any"))
    report["prediction"] = pred

    explainability = report.get("explainability", {}) if isinstance(report, dict) else {}
    gradcam_reference = (
        report.get("cloudinary_heatmap_url")
        or explainability.get("heatmap_path")
        or explainability.get("image_path")
    )

    report_path = user_reports_dir / f"{image_id}_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, separators=(",", ":"), ensure_ascii=True)

    user_data_dir = UserDataManager().get_user_data_dir(user_id)
    screening_report = ScreeningReport(
        user_id=user_id,
        upload_id=upload_id,
        image_id=image_id,
        screening_outcome=pred.get("screening_outcome"),
        raw_probability=pred.get("raw_probability"),
        calibrated_probability=pred.get("calibrated_probability"),
        confidence_band=pred.get("confidence_band"),
        decision_threshold=pred.get("decision_threshold"),
        triage_action=report.get("triage", {}).get("action"),
        urgency=report.get("triage", {}).get("urgency"),
        report_json_path=str(report_path.relative_to(user_data_dir)),
        gradcam_image_path=gradcam_reference,
        llm_summary=report.get("llm_summary"),
        report_payload=json.dumps(report, ensure_ascii=True, separators=(",", ":")),
        generated_at=_now_ist(),
    )
    db.session.add(screening_report)
    db.session.commit()

    log_audit(
        "inference_completed",
        user_id=user_id,
        resource_type="report",
        resource_id=screening_report.id,
        status="success",
    )
    return report

# ══════════════════════════════════════════════════════════════════════════
#  INFERENCE & BATCH PROCESSING
# ══════════════════════════════════════════════════════════════════════════

def _run_inference_on_dcm(
    dcm_path: Path,
    user_id: int,
    upload_id: int,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Run inference on a single DICOM file"""
    if not _ensure_model_loaded():
        return None, None
    
    ri_mod = _MODEL["inference_mod"]
    image_id = dcm_path.stem
    
    bbr.start()
    
    try:
        img_rgb = ri_mod.dicom_to_rgb(str(dcm_path), size=ri_mod.IMG_SIZE)
        inference = ri_mod.infer_single(
            img_rgb,
            _MODEL["model"],
            _MODEL["grad_cam"],
            _MODEL["transform"],
            _MODEL["device"],
            _MODEL["temperature"],
        )
        report = _persist_inference_result(image_id, user_id, upload_id, img_rgb, inference)
    
    except Exception as e:
        db.session.rollback()
        bbr.stop()
        logger.error(f"Inference failed: {e}", exc_info=True)
        log_audit("inference_failed", user_id=user_id, status="failure", details=str(e))
        raise
    
    bbr.stop()
    
    # Save trace
    ts = _now_ist().strftime("%Y%m%d_%H%M%S")
    base = f"{ts}_{image_id}"
    try:
        bbr.save_report(str(LOGS_DIR / f"{base}.txt"))
        bbr.save_json(str(LOGS_DIR / f"{base}.json"))
    except Exception as e:
        logger.warning(f"Could not save trace: {e}")
    
    return report, {"timestamp": ts, "image_id": image_id}

def _start_batch(dcm_paths: list[Path], user_id: int, temp_dir: str | None = None) -> str:
    """Trigger async batch processing via Celery."""
    batch_id = f"u{user_id}_{uuid.uuid4().hex[:12]}"
    dcm_paths_str = [str(p) for p in dcm_paths]
    queue = None
    if GPU_QUEUE_ENABLED:
        queue = GPU_QUEUE_NAME if _cuda_available() else CPU_QUEUE_NAME
    
    # Send task to Celery worker
    try:
        task_kwargs = {
            "batch_id": batch_id,
            "dcm_paths": dcm_paths_str,
            "user_id": user_id,
            "temp_dir": temp_dir,
        }
        send_kwargs = {"task_id": batch_id}
        if queue:
            send_kwargs["queue"] = queue
        task = celery_app.send_task(
            "tasks.process_dicom_batch",
            kwargs=task_kwargs,
            **send_kwargs,
        )
    except Exception as exc:
        logger.error("Failed to enqueue Celery batch task", exc_info=True)
        raise RuntimeError("Celery enqueue failed") from exc
    
    logger.info(f"Started Celery batch task {batch_id} (task_id={task.id})")
    return batch_id


def _iter_batches(items: list[Path], batch_size: int) -> list[list[Path]]:
    return [items[i:i + batch_size] for i in range(0, len(items), batch_size)]


def _run_batch_sync(dcm_paths: list[Path], user_id: int, temp_dir: str | None = None) -> dict[str, Any]:
    """Fallback synchronous batch processing when Celery is unavailable."""
    total = len(dcm_paths)
    succeeded_ids: list[str] = []
    failed_ids: list[str] = []
    started_at = _now_ist().isoformat()
    sync_batch_id = f"sync_u{user_id}_{uuid.uuid4().hex[:12]}"
    use_gpu_batch = _gpu_batch_ready() and total > 1

    log_audit(
        "batch_sync_started",
        user_id=user_id,
        details=f"batch_id={sync_batch_id}, files={total}",
        status="success",
    )

    user_upload_dir = UserDataManager().get_user_upload_dir(user_id)

    try:
        if use_gpu_batch:
            logger.info(
                "GPU batch inference enabled (size=%s); per-image traces are skipped.",
                GPU_BATCH_SIZE,
            )
            for chunk in _iter_batches(dcm_paths, GPU_BATCH_SIZE):
                upload_records: list[ScreeningUpload] = []
                for path in chunk:
                    upload_record = ScreeningUpload(
                        user_id=user_id,
                        file_name=path.name,
                        original_filename=path.name,
                        file_size=path.stat().st_size if path.exists() else None,
                        file_path=str(path.relative_to(user_upload_dir)) if path.parent == user_upload_dir else str(path),
                        processing_status="processing",
                    )
                    db.session.add(upload_record)
                    db.session.commit()
                    upload_records.append(upload_record)

                try:
                    batch_results = _infer_images_batch(chunk)
                except Exception as exc:
                    logger.error("GPU batch inference failed — %s", exc, exc_info=True)
                    for path, upload_record in zip(chunk, upload_records, strict=False):
                        image_id = path.stem
                        db.session.rollback()
                        upload_record.processing_status = "failed"
                        try:
                            db.session.commit()
                        except Exception:
                            db.session.rollback()
                        failed_ids.append(image_id)
                    continue

                for (path, upload_record), (img_rgb, inference) in zip(
                    zip(chunk, upload_records, strict=False),
                    batch_results,
                    strict=False,
                ):
                    image_id = path.stem
                    try:
                        report = _persist_inference_result(
                            image_id,
                            user_id,
                            upload_record.id,
                            img_rgb,
                            inference,
                        )
                        if report:
                            upload_record.processing_status = "completed"
                            db.session.commit()
                            succeeded_ids.append(image_id)
                        else:
                            upload_record.processing_status = "failed"
                            db.session.commit()
                            failed_ids.append(image_id)
                    except Exception as exc:
                        logger.error(f"Sync batch failed {image_id} — {exc}", exc_info=True)
                        db.session.rollback()
                        upload_record.processing_status = "failed"
                        try:
                            db.session.commit()
                        except Exception:
                            db.session.rollback()
                        failed_ids.append(image_id)
        else:
            for path in dcm_paths:
                image_id = path.stem

                upload_record = ScreeningUpload(
                    user_id=user_id,
                    file_name=path.name,
                    original_filename=path.name,
                    file_size=path.stat().st_size if path.exists() else None,
                    file_path=str(path.relative_to(user_upload_dir)) if path.parent == user_upload_dir else str(path),
                    processing_status="processing",
                )
                db.session.add(upload_record)
                db.session.commit()

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
                except Exception as exc:
                    logger.error(f"Sync batch failed {image_id} — {exc}", exc_info=True)
                    db.session.rollback()
                    upload_record.processing_status = "failed"
                    try:
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
                    failed_ids.append(image_id)
    finally:
        if temp_dir and Path(temp_dir).exists():
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
                logger.info(f"Cleaned up temp_dir: {temp_dir}")
            except Exception as exc:
                logger.warning(f"Failed to clean temp_dir {temp_dir}: {exc}")

    log_audit(
        "batch_sync_completed",
        user_id=user_id,
        details=(
            f"batch_id={sync_batch_id}, processed={total}, "
            f"succeeded={len(succeeded_ids)}, failed={len(failed_ids)}"
        ),
        status="success" if not failed_ids else "partial",
    )

    return {
        "batch_id": sync_batch_id,
        "user_id": user_id,
        "status": "completed",
        "total": total,
        "processed": total,
        "succeeded": len(succeeded_ids),
        "failed_ids": list(failed_ids),
        "image_ids": list(succeeded_ids),
        "current_file": "",
        "started_at": started_at,
        "finished_at": _now_ist().isoformat(),
        "error": None,
        "temp_dir": temp_dir,
    }


def _extract_user_id_from_batch_id(batch_id: str) -> int | None:
    """Recover the user id embedded in a batch id."""
    if not batch_id.startswith("u"):
        return None
    user_part = batch_id.split("_", 1)[0][1:]
    try:
        return int(user_part)
    except ValueError:
        return None


def _get_queue_depth() -> int | None:
    """Best-effort queue depth for the default Celery queue."""
    if not REDIS_URL.startswith("redis"):
        return None

    try:
        from redis import Redis
        client = Redis.from_url(REDIS_URL, decode_responses=True)
        return int(client.llen("celery"))
    except Exception:
        return None
# ══════════════════════════════════════════════════════════════════════════
#  DATA MODEL & UTILITIES
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class CaseRow:
    """Display row for screening report"""
    image_id: str = ""
    outcome: str = "Unknown"
    raw_prob: float | None = None
    cal_prob: float | None = None
    band: str = "N/A"
    triage: str = "N/A"
    urgency: str = "N/A"
    generated_at: str = ""
    report_file: str | None = None
    gradcam_file: str | None = None

    @property
    def gradcam_url(self) -> str | None:
        if not self.gradcam_file:
            return None
        if self.gradcam_file.startswith("http"):
            return self.gradcam_file
        return self.gradcam_file
    
    @property
    def date_display(self) -> str:
        return _format_iso_ist(self.generated_at)
    
    @property
    def is_positive(self) -> bool:
        return "no hemorrhage" not in self.outcome.lower()

def _load_user_cases(user_id: int) -> list[CaseRow]:
    """Load user's screening reports from database"""
    reports = ScreeningReport.query.filter_by(user_id=user_id).order_by(
        ScreeningReport.generated_at.desc()
    ).all()
    
    cases = []
    for r in reports:
        cases.append(CaseRow(
            image_id=r.image_id,
            outcome=r.screening_outcome or "Unknown",
            raw_prob=r.raw_probability,
            cal_prob=r.calibrated_probability,
            band=r.confidence_band or "N/A",
            triage=r.triage_action or "N/A",
            urgency=r.urgency or "N/A",
            generated_at=r.generated_at.isoformat() if r.generated_at else "",
            report_file=Path(r.report_json_path).name if r.report_json_path else None,
            gradcam_file=_resolve_gradcam_reference(r),
        ))
    
    return cases


def _resolve_gradcam_reference(report: ScreeningReport) -> str | None:
    """Resolve the best available Grad-CAM reference for a report."""
    if report.gradcam_image_path:
        return str(report.gradcam_image_path)

    if report.report_payload:
        try:
            payload = json.loads(report.report_payload)
            explainability = payload.get("explainability", {}) if isinstance(payload, dict) else {}
            return (
                payload.get("cloudinary_heatmap_url")
                or explainability.get("heatmap_path")
                or explainability.get("image_path")
            )
        except json.JSONDecodeError:
            pass

    if not report.report_json_path:
        return None

    try:
        user_data_dir = UserDataManager().get_user_data_dir(report.user_id)
        report_path = user_data_dir / report.report_json_path
        if not report_path.exists():
            return None

        with open(report_path, "r", encoding="utf-8") as f:
            payload = json.load(f)

        explainability = payload.get("explainability", {}) if isinstance(payload, dict) else {}
        return (
            payload.get("cloudinary_heatmap_url")
            or explainability.get("heatmap_path")
            or explainability.get("image_path")
        )
    except (OSError, json.JSONDecodeError, TypeError, AttributeError):
        return None

def compute_stats(rows: list[CaseRow]) -> dict[str, Any]:
    """Compute statistics for dashboard"""
    total = len(rows)
    positive = sum(1 for r in rows if r.is_positive)
    urgent = sum(1 for r in rows if r.urgency.upper() == "URGENT")
    cal_probs = [r.cal_prob for r in rows if r.cal_prob is not None]
    avg_cal = sum(cal_probs) / len(cal_probs) if cal_probs else 0.0
    pos_rate = (positive / total * 100) if total else 0.0
    
    return {
        "total": total,
        "positive": positive,
        "negative": total - positive,
        "urgent": urgent,
        "avg_cal_prob": avg_cal,
        "pos_rate": pos_rate,
        "heatmaps": sum(1 for r in rows if r.gradcam_file),
    }


def _compute_ground_truth_stats(user_id: int) -> dict[str, Any]:
    """Compute ground-truth agreement stats for a user."""
    reports = ScreeningReport.query.filter_by(user_id=user_id).all()
    labeled = [r for r in reports if (r.true_label or "").upper() in ("POSITIVE", "NEGATIVE")]
    total = len(labeled)
    if total == 0:
        return {
            "total": 0,
            "tp": 0,
            "tn": 0,
            "fp": 0,
            "fn": 0,
            "accuracy": None,
            "fp_rate": None,
        }

    def _ai_positive(report: ScreeningReport) -> bool:
        return "no hemorrhage" not in (report.screening_outcome or "").lower()

    tp = tn = fp = fn = 0
    for r in labeled:
        ai_pos = _ai_positive(r)
        truth_pos = (r.true_label or "").upper() == "POSITIVE"
        if ai_pos and truth_pos:
            tp += 1
        elif ai_pos and not truth_pos:
            fp += 1
        elif not ai_pos and truth_pos:
            fn += 1
        else:
            tn += 1

    accuracy = (tp + tn) / total if total else None
    fp_rate = fp / (fp + tn) if (fp + tn) else None

    return {
        "total": total,
        "tp": tp,
        "tn": tn,
        "fp": fp,
        "fn": fn,
        "accuracy": accuracy,
        "fp_rate": fp_rate,
    }


def _load_calibration() -> dict[str, Any]:
    """Load calibration file safely for template rendering."""
    if not CALIB_JSON.exists():
        return {}
    try:
        with open(CALIB_JSON, "r", encoding="utf-8") as f:
            calib = json.load(f)
        # Add backward-compatible aliases expected by templates
        return {
            **calib,
            "method": calib.get("method", calib.get("best_method", "N/A")),
            "temperature": calib.get("temperature", 1.0),
            "raw_ece": calib.get("ece_raw", 0.0),
            "cal_ece": calib.get("ece_isotonic", calib.get("ece_temp", 0.0)),
            "raw_brier": calib.get("brier_raw", 0.0),
            "cal_brier": calib.get("brier_isotonic", calib.get("brier_temp", 0.0)),
            "calibrated_threshold": calib.get("threshold_at_spec90", 0.5),
            "base_threshold": calib.get("base_threshold", 0.5),
            "high_threshold": calib.get("high_threshold", calib.get("triage_high_thresh", 0.7)),
            "low_threshold": calib.get("low_threshold", calib.get("triage_low_thresh", 0.3)),
        }
    except (OSError, json.JSONDecodeError):
        return {}


def _load_normalization() -> dict[str, Any]:
    """Load normalization statistics safely for template rendering."""
    if not NORM_JSON.exists():
        return {}
    try:
        with open(NORM_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}

    mean = data.get("mean_3ch") or data.get("mean")
    std = data.get("std_3ch") or data.get("std")
    return {
        "mean": mean,
        "std": std,
        "n_images": data.get("n_images"),
    }

# ══════════════════════════════════════════════════════════════════════════
#  MIDDLEWARE
# ══════════════════════════════════════════════════════════════════════════

@app.before_request
def _log_request():  # pyright: ignore[reportUnusedFunction]
    g._start = time.perf_counter()
    g._client_info = get_client_ip()

@app.after_request
def _log_response(response):  # pyright: ignore[reportUnusedFunction]
    elapsed = (time.perf_counter() - getattr(g, "_start", time.perf_counter())) * 1000
    logger.info(
        f"{request.method} {request.path} -> {response.status_code} ({elapsed:.1f}ms) from {getattr(g, '_client_info', 'unknown')}"
    )
    return response

# ══════════════════════════════════════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════════════════════════════════════

@app.route("/")
def home():
    """Home page"""
    if not current_user.is_authenticated:
        return redirect(url_for("auth.login"))
    
    cases = _load_user_cases(current_user.id)
    stats = compute_stats(cases)
    
    log_audit("page_view_home", user_id=current_user.id, status="success")
    return render_template("home.html", stats=stats, user=current_user)

@app.route("/upload", methods=["GET"])
@login_required
def upload():
    """Upload page"""
    return render_template("upload.html", local_mode=LOCAL_MODE)

@app.route("/analyze", methods=["POST"])
@login_required
def analyze():
    """Process uploaded DICOM files"""
    # Check rate limit
    is_limited, msg = check_upload_rate_limit(current_user.id)
    if is_limited:
        log_audit("upload_rate_limited", user_id=current_user.id, status="failure")
        return jsonify({"error": msg}), 429
    
    files = request.files.getlist("file")
    files = [f for f in files if f.filename]
    
    if not files:
        flash("No files were uploaded.", "error")
        return redirect(url_for("upload"))
    
    user_upload_dir = UserDataManager().get_user_upload_dir(current_user.id)
    user_upload_dir.mkdir(parents=True, exist_ok=True)
    
    dcm_paths: list[Path] = []
    temp_dir: str | None = None
    
    for f in files:
        filename = f.filename or ""
        fname = filename.lower()
        
        if fname.endswith(".zip"):
            temp_dir = tempfile.mkdtemp(prefix="ich_zip_")
            zip_path = Path(temp_dir) / secure_filename(filename)
            f.save(str(zip_path))
            try:
                with zipfile.ZipFile(zip_path, "r") as zf:
                    zf.extractall(temp_dir)
                dcm_paths.extend(sorted(Path(temp_dir).rglob("*.dcm")))
            except zipfile.BadZipFile:
                shutil.rmtree(temp_dir, ignore_errors=True)
                log_audit("upload_failed", user_id=current_user.id, 
                         status="failure", details="Bad ZIP file")
                flash("The uploaded ZIP file is corrupted.", "error")
                return redirect(url_for("upload"))
        
        elif fname.endswith(".dcm"):
            safe = sanitize_filename(filename)
            save_path = user_upload_dir / safe
            f.save(str(save_path))
            dcm_paths.append(save_path)
    
    if not dcm_paths:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        log_audit("upload_no_dcm", user_id=current_user.id, status="failure")
        flash("No .dcm files found in the upload.", "error")
        return redirect(url_for("upload"))
    
    # Single file - synchronous
    if len(dcm_paths) == 1 and temp_dir is None:
        path = dcm_paths[0]
        try:
            user_upload_dir = UserDataManager().get_user_upload_dir(current_user.id)
            upload_record = ScreeningUpload(
                user_id=current_user.id,
                file_name=path.name,
                original_filename=path.name,
                file_size=path.stat().st_size if path.exists() else None,
                file_path=str(path.relative_to(user_upload_dir)) if path.parent == user_upload_dir else str(path),
                processing_status="processing",
            )
            db.session.add(upload_record)
            db.session.commit()

            report, _ = _run_inference_on_dcm(path, current_user.id, upload_record.id)
            if not report:
                flash("Model failed to load. Check server logs.", "error")
                return redirect(url_for("upload"))

            upload_record.processing_status = "completed"
            db.session.commit()
            return redirect(url_for("case_detail", image_id=path.stem))
        except Exception as e:
            db.session.rollback()
            logger.error(f"Analysis failed: {e}")
            log_audit("analysis_failed", user_id=current_user.id, status="failure", details=str(e))
            flash(f"Analysis failed: {e}", "error")
            return redirect(url_for("upload"))
        finally:
            if path.exists() and path.parent == user_upload_dir:
                path.unlink()
    
    # Multiple files - async batch
    try:
        batch_id = _start_batch(dcm_paths, current_user.id, temp_dir)
        log_audit(
            "batch_started",
            user_id=current_user.id,
            details=f"batch_id={batch_id}, files={len(dcm_paths)}",
        )
        return redirect(url_for("batch_progress", batch_id=batch_id, total=len(dcm_paths)))
    except Exception:
        logger.error("Celery unavailable; running synchronous fallback", exc_info=True)
        flash("Celery worker unavailable. Running batch synchronously; this may take a while.", "warning")
        result = _run_batch_sync(dcm_paths, current_user.id, temp_dir)
        flash(
            f"Batch complete: {result['succeeded']}/{result['total']} succeeded.",
            "info",
        )
        return redirect(url_for("reports"))


@app.route("/analyze/directory", methods=["POST"])
@login_required
def analyze_directory():
    """Local-only route for scanning a server-side directory of DICOM files."""
    if not LOCAL_MODE:
        abort(403)

    dir_path_str = request.form.get("dir_path", "").strip()
    if not dir_path_str:
        flash("Please enter a directory path.", "error")
        return redirect(url_for("upload"))

    scan_dir = Path(dir_path_str)
    if not scan_dir.is_dir():
        flash(f"Directory not found: {dir_path_str}", "error")
        return redirect(url_for("upload"))

    dcm_paths = sorted(scan_dir.rglob("*.dcm"))
    if not dcm_paths:
        flash(f"No .dcm files found in: {dir_path_str}", "error")
        return redirect(url_for("upload"))

    try:
        batch_id = _start_batch(dcm_paths, current_user.id)
        log_audit(
            "directory_batch_started",
            user_id=current_user.id,
            details=f"batch_id={batch_id}, files={len(dcm_paths)}",
        )
        return redirect(url_for("batch_progress", batch_id=batch_id, total=len(dcm_paths)))
    except Exception:
        logger.error("Celery unavailable; running synchronous directory scan", exc_info=True)
        flash("Celery worker unavailable. Running directory scan synchronously.", "warning")
        result = _run_batch_sync(dcm_paths, current_user.id)
        flash(
            f"Directory scan complete: {result['succeeded']}/{result['total']} succeeded.",
            "info",
        )
        return redirect(url_for("reports"))

@app.route("/batch/<batch_id>")
@login_required
def batch_progress(batch_id):
    """Batch processing progress page"""
    batch = _get_batch_from_celery(batch_id)
    if not batch or batch.get("user_id") != current_user.id:
        abort(404)
    expected_total = request.args.get("total", type=int)
    if expected_total and (batch.get("total") or 0) == 0:
        batch["total"] = expected_total
    return render_template(
        "batch_progress.html",
        batch=batch,
        batch_id=batch_id,
        expected_total=expected_total or 0,
    )

@app.route("/batch/<batch_id>/status")
@login_required
def batch_status(batch_id):
    """Get batch status (JSON API)"""
    batch = _get_batch_from_celery(batch_id)
    if not batch or batch.get("user_id") != current_user.id:
        return jsonify({"error": "Not found"}), 404
    return jsonify(batch)

@app.route("/batch/<batch_id>/cancel", methods=["POST"])
@login_required
def cancel_batch(batch_id):
    """Cancel a running batch task."""
    user_id = _extract_user_id_from_batch_id(batch_id)
    if user_id != current_user.id:
        abort(404)
    try:
        celery_app.control.revoke(batch_id, terminate=True, signal="SIGTERM")
        log_audit(
            "batch_canceled",
            user_id=current_user.id,
            details=f"batch_id={batch_id}",
            status="success",
        )
        return jsonify({"status": "canceled"})
    except Exception as exc:
        logger.error("Failed to cancel batch %s: %s", batch_id, exc, exc_info=True)
        return jsonify({"error": "Cancel failed"}), 500

def _get_batch_from_celery(batch_id: str) -> dict[str, Any] | None:
    """Retrieve batch status from Celery task result backend."""
    # In a production system, we'd also validate user_id from the database
    # For now, we rely on Celery returning task metadata with user_id in meta dict
    queue_size = _get_queue_depth()
    
    # Try to find the task associated with this batch_id
    # Celery doesn't provide a direct "get by batch_id" so we query the backend
    result = AsyncResult(batch_id, app=celery_app)
    user_id = _extract_user_id_from_batch_id(batch_id)
    
    if result.state == "PENDING" and not result.info:
        # Task has been queued but has not written progress yet.
        return {
            "batch_id": batch_id,
            "user_id": user_id,
            "status": "pending",
            "total": 0,
            "processed": 0,
            "succeeded": 0,
            "failed_ids": [],
            "image_ids": [],
            "current_file": "",
            "started_at": None,
            "finished_at": None,
            "error": None,
            "queue_size": queue_size,
        }
    elif result.state == "REVOKED":
        return {
            "batch_id": batch_id,
            "user_id": user_id,
            "status": "canceled",
            "total": 0,
            "processed": 0,
            "succeeded": 0,
            "failed_ids": [],
            "image_ids": [],
            "current_file": "",
            "started_at": None,
            "finished_at": None,
            "error": None,
            "queue_size": queue_size,
        }
    
    # Build response matching _BATCHES format for frontend compatibility
    if result.state == "PROGRESS":
        meta = result.info or {}
        return {
            "batch_id": meta.get("batch_id", batch_id),
            "user_id": meta.get("user_id", user_id),
            "status": meta.get("status", "running"),
            "total": meta.get("total", 0),
            "processed": meta.get("processed", 0),
            "succeeded": meta.get("succeeded", 0),
            "failed_ids": meta.get("failed_ids", []),
            "image_ids": meta.get("image_ids", []),
            "current_file": meta.get("current_file", ""),
            "started_at": meta.get("started_at"),
            "finished_at": meta.get("finished_at"),
            "error": meta.get("error"),
            "queue_size": meta.get("queue_size", queue_size),
        }
    elif result.state == "SUCCESS":
        # Task completed
        return result.result if isinstance(result.result, dict) else {
            "batch_id": batch_id,
            "user_id": user_id,
            "status": "completed",
            "error": None,
            "queue_size": queue_size,
        }
    elif result.state == "FAILURE":
        # Task failed
        return {
            "batch_id": batch_id,
            "user_id": user_id,
            "status": "failed",
            "error": str(result.info) if result.info else "Unknown error",
            "queue_size": queue_size,
        }
    elif result.state == "REVOKED":
        return {
            "batch_id": batch_id,
            "user_id": user_id,
            "status": "revoked",
            "error": "Task was revoked",
            "queue_size": queue_size,
        }
    else:
        # PENDING or other states
        return {
            "batch_id": batch_id,
            "user_id": user_id,
            "status": "pending",
            "error": None,
            "queue_size": queue_size,
        }
@app.route("/reports")
@login_required
def reports():
    """User's screening reports"""
    route_start = time.perf_counter()
    cases = _load_user_cases(current_user.id)
    total_cases = len(cases)
    
    # Filtering
    q = request.args.get("q", "").strip()
    band = request.args.get("band", "")
    urgency = request.args.get("urgency", "")
    outcome = request.args.get("outcome", "")
    sort_by = request.args.get("sort", "date_desc")
    try:
        page = max(1, int(request.args.get("page", "1") or 1))
    except ValueError:
        page = 1
    try:
        page_size = int(request.args.get("page_size", "50") or 50)
    except ValueError:
        page_size = 50
    if page_size not in (10, 50, 100):
        page_size = 50
    
    if q:
        ql = q.lower()
        cases = [c for c in cases if ql in c.image_id.lower() or ql in c.outcome.lower()]
    if band:
        cases = [c for c in cases if c.band.upper() == band.upper()]
    if urgency:
        cases = [c for c in cases if c.urgency.upper() == urgency.upper()]
    if outcome == "POSITIVE":
        cases = [c for c in cases if c.is_positive]
    elif outcome == "NEGATIVE":
        cases = [c for c in cases if not c.is_positive]
    
    if sort_by == "date_desc":
        cases = sorted(cases, key=lambda c: c.generated_at or "", reverse=True)
    elif sort_by == "date_asc":
        cases = sorted(cases, key=lambda c: c.generated_at or "")
    elif sort_by == "prob_desc":
        cases = sorted(cases, key=lambda c: c.cal_prob or 0, reverse=True)
    elif sort_by == "prob_asc":
        cases = sorted(cases, key=lambda c: c.cal_prob or 0)
    
    stats = compute_stats(cases)
    total_items = len(cases)
    total_pages = max(1, math.ceil(total_items / page_size))
    page = min(page, total_pages)
    page_start = (page - 1) * page_size
    rows = cases[page_start: page_start + page_size]
    route_compute_ms = (time.perf_counter() - route_start) * 1000
    
    return render_template(
        "reports.html",
        rows=rows,
        cases=rows,
        stats=stats,
        calib=_load_calibration(),
        q=q,
        band=band,
        urgency=urgency,
        outcome=outcome,
        sort=sort_by,
        sort_by=sort_by,
        page=page,
        page_size=page_size,
        page_start=page_start,
        total_pages=total_pages,
        total_items=total_items,
        total_cases=total_cases,
        route_compute_ms=route_compute_ms,
        data_refresh_ms=0,
        data_cache_hit=False,
    )


@app.route("/report/<image_id>/delete", methods=["POST"])
@login_required
def delete_report(image_id):
    """Delete a single report and its associated files for the current user."""
    report = ScreeningReport.query.filter_by(user_id=current_user.id, image_id=image_id).first()
    if not report:
        flash("Report not found", "error")
        return redirect(url_for("reports"))

    reports_dir = UserDataManager().get_user_reports_dir(current_user.id)
    try:
        for path in reports_dir.glob(f"{image_id}*"):
            try:
                path.unlink()
            except OSError:
                logger.warning(f"Failed to delete file: {path}")
    except Exception:
        logger.exception("Error while removing report files")

    try:
        db.session.delete(report)
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Failed to delete report DB entry")
        flash("Failed to delete report", "error")
        return redirect(url_for("reports"))

    log_audit("report_deleted", user_id=current_user.id, resource_type="report", resource_id=report.id)
    flash("Report deleted", "success")
    return redirect(url_for("reports"))


@app.route("/reports/delete_all", methods=["POST"])
@login_required
def delete_all_reports():
    """Delete all reports and local files for the current user."""
    reports = ScreeningReport.query.filter_by(user_id=current_user.id).all()
    reports_dir = UserDataManager().get_user_reports_dir(current_user.id)

    # Remove files
    try:
        for path in reports_dir.iterdir():
            if path.is_file():
                try:
                    path.unlink()
                except OSError:
                    logger.warning(f"Failed to delete file: {path}")
    except Exception:
        logger.exception("Error while removing user report files")

    # Remove DB entries
    try:
        for r in reports:
            db.session.delete(r)
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Failed to delete report DB entries")
        flash("Failed to delete all reports", "error")
        return redirect(url_for("reports"))

    log_audit("reports_deleted_all", user_id=current_user.id, resource_type="report", resource_id=None)
    flash("All reports deleted", "success")
    return redirect(url_for("reports"))

@app.route("/case/<image_id>")
@login_required
def case_detail(image_id):
    """View screening report details"""
    report = ScreeningReport.query.filter_by(user_id=current_user.id, image_id=image_id).first()
    if not report:
        abort(404)
    
    report_data = None
    if report.report_payload:
        try:
            report_data = json.loads(report.report_payload)
        except json.JSONDecodeError:
            report_data = None

    if report_data is None:
        user_reports_dir = UserDataManager().get_user_reports_dir(current_user.id)
        report_path = user_reports_dir / f"{image_id}_report.json"
        if not report_path.exists():
            abort(404)
        try:
            with open(report_path) as f:
                report_data = json.load(f)
        except (json.JSONDecodeError, OSError):
            abort(500)
    
    log_audit("report_viewed", user_id=current_user.id, resource_type="report", resource_id=report.id)
    # Build a lightweight `row` object matching CaseRow used elsewhere so the
    # detail template can access properties like `row.image_id`, `row.cal_prob`.
    def _format_date(dt):
        try:
            return dt.isoformat()
        except Exception:
            return str(dt) if dt else ""

    gradcam_ref = _resolve_gradcam_reference(report)
    gradcam_url = None
    if gradcam_ref:
        if gradcam_ref.startswith("http"):
            gradcam_url = gradcam_ref
        else:
            gradcam_url = url_for("serve_gradcam", filename=Path(gradcam_ref).name)

    row = SimpleNamespace(
        image_id=report.image_id,
        outcome=report.screening_outcome or "Unknown",
        raw_prob=report.raw_probability,
        cal_prob=report.calibrated_probability,
        band=report.confidence_band or "N/A",
        triage=report.triage_action or "N/A",
        urgency=report.urgency or "N/A",
        generated_at=_format_date(report.generated_at),
        date_display=_format_dt_ist(report.generated_at),
        report_file=Path(report.report_json_path).name if report.report_json_path else None,
        gradcam_url=gradcam_url,
        true_label=report.true_label,
        is_positive=("no hemorrhage" not in (report.screening_outcome or "").lower()),
    )

    return render_template("detail.html", row=row, report_record=report, payload=report_data)


@app.route("/case/<image_id>/ground-truth", methods=["POST"])
@login_required
def update_ground_truth(image_id):
    """Update ground truth label for a report."""
    report = ScreeningReport.query.filter_by(user_id=current_user.id, image_id=image_id).first()
    if not report:
        abort(404)

    raw_value = (request.form.get("true_label") or "").strip()
    normalized = raw_value.upper().replace(" ", "_").replace("/", "_")
    allowed = {"POSITIVE", "NEGATIVE", "UNKNOWN", "N_A"}
    if not normalized or normalized == "N_A":
        report.true_label = None
    elif normalized not in allowed:
        flash("Invalid ground truth value.", "error")
        return redirect(url_for("case_detail", image_id=image_id))
    else:
        report.true_label = "UNKNOWN" if normalized == "UNKNOWN" else normalized

    try:
        db.session.commit()
        log_audit("ground_truth_updated", user_id=current_user.id, resource_type="report", resource_id=report.id)
        flash("Ground truth updated.", "success")
    except Exception:
        db.session.rollback()
        logger.exception("Failed to update ground truth")
        flash("Failed to update ground truth.", "error")

    return redirect(url_for("case_detail", image_id=image_id))

@app.route("/logs")
@login_required
def logs_page():
    """View user's inference logs"""
    if not SHOW_LOGS:
        abort(404)
    log_files = []
    
    if LOGS_DIR.exists():
        for path in sorted(LOGS_DIR.iterdir(), reverse=True)[:50]:  # Last 50 logs
            if path.suffix in (".txt", ".json"):
                modified = datetime.datetime.fromtimestamp(
                    path.stat().st_mtime,
                    tz=datetime.timezone.utc,
                )
                modified_local = _as_ist(modified)
                log_files.append({
                    "name": path.name,
                    "size": round(path.stat().st_size / 1024, 1),
                    "modified": modified_local.isoformat() if modified_local else "",
                })
    
    return render_template("logs.html", logs=log_files)

@app.route("/about")
def about():
    """About page"""
    return render_template("about.html", calib=_load_calibration())

@app.route("/evaluation")
def evaluation():
    """Model evaluation page"""
    cases = _load_user_cases(current_user.id) if current_user.is_authenticated else []
    gt_stats = _compute_ground_truth_stats(current_user.id) if current_user.is_authenticated else None
    cal_probs = [r.cal_prob for r in cases if r.cal_prob is not None]

    bins = [0] * 10
    for p in cal_probs:
        bins[min(int(p * 10), 9)] += 1

    band_data: dict[str, dict[str, int]] = {}
    for bnd in ("HIGH", "MEDIUM", "LOW"):
        subset = [r for r in cases if r.band.upper() == bnd]
        positive = sum(1 for r in subset if r.is_positive)
        band_data[bnd] = {
            "total": len(subset),
            "positive": positive,
            "negative": len(subset) - positive,
        }

    return render_template(
        "evaluation.html",
        stats=compute_stats(cases),
        calib=_load_calibration(),
        norm=_load_normalization(),
        bins=bins,
        band_data=band_data,
        total=len(cases),
        gt_stats=gt_stats,
    )


@app.route("/gradcam/<path:filename>")
@login_required
def serve_gradcam(filename: str):
    """Serve a user's Grad-CAM image from their report directory."""
    safe_name = Path(filename).name
    reports_dir = UserDataManager().get_user_reports_dir(current_user.id)
    return send_from_directory(reports_dir, safe_name)

@app.route("/report-json/<path:filename>")
@login_required
def serve_report_json(filename: str):
    """Serve a user's report JSON file from their report directory."""
    safe_name = Path(filename).name
    reports_dir = UserDataManager().get_user_reports_dir(current_user.id)
    report_path = reports_dir / safe_name
    if report_path.exists():
        return send_from_directory(reports_dir, safe_name, mimetype="application/json")

    image_id = safe_name.replace("_report.json", "")
    report = ScreeningReport.query.filter_by(user_id=current_user.id, image_id=image_id).first()
    if report and report.report_payload:
        return Response(report.report_payload, mimetype="application/json")

    abort(404)

@app.errorhandler(401)
def unauthorized(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Unauthorized"}), 401
    return redirect(url_for("auth.login"))

@app.errorhandler(403)
def forbidden(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Forbidden"}), 403
    flash("Access denied", "error")
    return redirect(url_for("home"))

@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not found"}), 404
    return render_template("404.html"), 404

@app.errorhandler(500)
def server_error(e):
    logger.error(f"Server error: {e}", exc_info=True)
    if request.path.startswith("/api/"):
        return jsonify({"error": "Server error"}), 500
    return render_template("500.html"), 500

# ══════════════════════════════════════════════════════════════════════════
#  CLI COMMANDS
# ══════════════════════════════════════════════════════════════════════════

@app.cli.command()
def init_db_cmd():
    """Initialize database"""
    init_db()
    print("Database initialized!")

@app.cli.command()
def create_admin():
    """Create admin user (interactive)"""
    username = input("Username: ").strip()
    email = input("Email: ").strip()
    password = getpass("Password: ")
    
    if User.query.filter_by(username=username).first():
        print("User already exists!")
        return
    
    user = User(username=username, email=email, full_name="Admin")
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    print(f"Admin user '{username}' created!")

@app.cli.command()
def migrate_utc_to_ist():
    """Convert existing UTC timestamps to IST (run once)."""
    with app.app_context():
        updates = 0
        models = {
            User: ["created_at", "updated_at"],
            ScreeningUpload: ["upload_timestamp"],
            ScreeningReport: ["generated_at", "created_at"],
            AuditLog: ["timestamp"],
        }
        for model, fields in models.items():
            for row in model.query.all():
                changed = False
                for field in fields:
                    value = getattr(row, field, None)
                    updated = _to_ist_naive(value)
                    if updated and updated != value:
                        setattr(row, field, updated)
                        changed = True
                if changed:
                    updates += 1
        db.session.commit()
        print(f"Migrated timestamps for {updates} rows.")

# ══════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    with app.app_context():
        init_db()
    
    app.run(host="0.0.0.0", port=APP_PORT, debug=APP_DEBUG)
