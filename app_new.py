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
import threading
import time
import uuid
import zipfile
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    Flask, abort, flash, g, jsonify, redirect, render_template, request,
    send_from_directory, url_for
)
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_login import current_user, login_required

# Import new security and auth modules
from models import db, User, ScreeningReport
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

_BATCHES: dict[str, dict[str, Any]] = {}
_BATCHES_LOCK = threading.Lock()

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

# ══════════════════════════════════════════════════════════════════════════
#  INFERENCE & BATCH PROCESSING
# ══════════════════════════════════════════════════════════════════════════

def _run_inference_on_dcm(dcm_path: Path, user_id: int) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Run inference on a single DICOM file"""
    if not _ensure_model_loaded():
        return None, None
    
    ri_mod = _MODEL["inference_mod"]
    image_id = dcm_path.stem
    user_reports_dir = UserDataManager().get_user_reports_dir(user_id)
    
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
        
        user_reports_dir.mkdir(parents=True, exist_ok=True)
        report = ri_mod.build_report(
            image_id, inference, _MODEL["calib_cfg"],
            user_reports_dir, img_rgb, true_label=None,
        )
        
        pred = report.get("prediction", {})
        pred.setdefault("raw_probability", inference.get("raw_prob_any"))
        pred.setdefault("calibrated_probability", inference.get("cal_prob_any"))
        pred.setdefault("decision_threshold", pred.get("decision_threshold_any"))
        report["prediction"] = pred
        
        report_path = user_reports_dir / f"{image_id}_report.json"
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2)
        
        # Save to database
        screening_report = ScreeningReport(
            user_id=user_id,
            upload_id=0,  # Will be set by caller if needed
            image_id=image_id,
            screening_outcome=pred.get("screening_outcome"),
            raw_probability=pred.get("raw_probability"),
            calibrated_probability=pred.get("calibrated_probability"),
            confidence_band=pred.get("confidence_band"),
            decision_threshold=pred.get("decision_threshold"),
            triage_action=report.get("triage", {}).get("action"),
            urgency=report.get("triage", {}).get("urgency"),
            report_json_path=str(report_path.relative_to(BASE_DIR)),
            generated_at=datetime.datetime.utcnow(),
        )
        db.session.add(screening_report)
        db.session.commit()
        
        log_audit("inference_completed", user_id=user_id, resource_type="report", 
                 resource_id=screening_report.id, status="success")
    
    except Exception as e:
        bbr.stop()
        logger.error(f"Inference failed: {e}", exc_info=True)
        log_audit("inference_failed", user_id=user_id, status="failure", details=str(e))
        raise
    
    bbr.stop()
    
    # Save trace
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"{ts}_{image_id}"
    try:
        bbr.save_report(str(LOGS_DIR / f"{base}.txt"))
        bbr.save_json(str(LOGS_DIR / f"{base}.json"))
    except Exception as e:
        logger.warning(f"Could not save trace: {e}")
    
    return report, {"timestamp": ts, "image_id": image_id}

def _new_batch(user_id: int, total: int, temp_dir: str | None = None) -> str:
    """Create a batch processing job"""
    batch_id = uuid.uuid4().hex[:12]
    with _BATCHES_LOCK:
        _BATCHES[batch_id] = {
            "user_id": user_id,
            "status": "running",
            "total": total,
            "processed": 0,
            "succeeded": 0,
            "failed_ids": [],
            "current_file": "",
            "image_ids": [],
            "started_at": datetime.datetime.now().isoformat(),
            "finished_at": None,
            "error": None,
            "temp_dir": temp_dir,
        }
    return batch_id

def _batch_update(batch_id: str, **kw: Any) -> None:
    """Update batch job status"""
    with _BATCHES_LOCK:
        if batch_id in _BATCHES:
            _BATCHES[batch_id].update(kw)

def _run_batch_worker(batch_id: str, dcm_paths: list[Path], user_id: int):
    """Process multiple DICOM files in background"""
    succeeded_ids = []
    failed_ids = []
    
    for i, path in enumerate(dcm_paths, 1):
        image_id = path.stem
        _batch_update(batch_id, current_file=image_id, processed=i - 1)
        
        try:
            report, _ = _run_inference_on_dcm(path, user_id)
            if report:
                succeeded_ids.append(image_id)
            else:
                failed_ids.append(image_id)
        except Exception as e:
            logger.error(f"Batch {batch_id}: failed {image_id} — {e}")
            failed_ids.append(image_id)
        
        _batch_update(
            batch_id,
            processed=i,
            succeeded=len(succeeded_ids),
            image_ids=list(succeeded_ids),
            failed_ids=list(failed_ids),
        )
    
    # Clean up
    with _BATCHES_LOCK:
        b = _BATCHES.get(batch_id, {})
        td = b.get("temp_dir")
    if td and Path(td).exists():
        shutil.rmtree(td, ignore_errors=True)
    
    _batch_update(
        batch_id,
        status="completed",
        current_file="",
        finished_at=datetime.datetime.now().isoformat(),
    )
    logger.info(f"Batch {batch_id} complete: {len(succeeded_ids)}/{len(dcm_paths)}, {len(failed_ids)} failed")

def _start_batch(dcm_paths: list[Path], user_id: int, temp_dir: str | None = None) -> str:
    """Start async batch processing"""
    batch_id = _new_batch(user_id, len(dcm_paths), temp_dir)
    t = threading.Thread(
        target=_run_batch_worker,
        args=(batch_id, dcm_paths, user_id),
        daemon=True,
        name=f"batch-{batch_id}",
    )
    t.start()
    return batch_id

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
    def date_display(self) -> str:
        if not self.generated_at:
            return "—"
        try:
            dt = datetime.datetime.fromisoformat(self.generated_at)
            return dt.strftime("%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            return self.generated_at[:16]
    
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
        ))
    
    return cases

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
            report, _ = _run_inference_on_dcm(path, current_user.id)
            if not report:
                flash("Model failed to load. Check server logs.", "error")
                return redirect(url_for("upload"))
            return redirect(url_for("case_detail", image_id=path.stem))
        except Exception as e:
            logger.error(f"Analysis failed: {e}")
            log_audit("analysis_failed", user_id=current_user.id, status="failure", details=str(e))
            flash(f"Analysis failed: {e}", "error")
            return redirect(url_for("upload"))
        finally:
            if path.exists() and path.parent == user_upload_dir:
                path.unlink()
    
    # Multiple files - async batch
    batch_id = _start_batch(dcm_paths, current_user.id, temp_dir)
    log_audit("batch_started", user_id=current_user.id, 
             details=f"batch_id={batch_id}, files={len(dcm_paths)}")
    return redirect(url_for("batch_progress", batch_id=batch_id))


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

    batch_id = _start_batch(dcm_paths, current_user.id)
    log_audit("directory_batch_started", user_id=current_user.id, details=f"batch_id={batch_id}, files={len(dcm_paths)}")
    return redirect(url_for("batch_progress", batch_id=batch_id))

@app.route("/batch/<batch_id>")
@login_required
def batch_progress(batch_id):
    """Batch processing progress page"""
    with _BATCHES_LOCK:
        batch = _BATCHES.get(batch_id)
        if not batch or batch.get("user_id") != current_user.id:
            abort(404)
        batch_copy = dict(batch)
    
    return render_template("batch_progress.html", batch=batch_copy, batch_id=batch_id)

@app.route("/batch/<batch_id>/status")
@login_required
def batch_status(batch_id):
    """Get batch status (JSON API)"""
    with _BATCHES_LOCK:
        batch = _BATCHES.get(batch_id)
        if not batch or batch.get("user_id") != current_user.id:
            return jsonify({"error": "Not found"}), 404
        return jsonify(batch)

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

@app.route("/case/<image_id>")
@login_required
def case_detail(image_id):
    """View screening report details"""
    report = ScreeningReport.query.filter_by(user_id=current_user.id, image_id=image_id).first()
    if not report:
        abort(404)
    
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
    return render_template("detail.html", report=report_data)

@app.route("/logs")
@login_required
def logs_page():
    """View user's inference logs"""
    log_files = []
    
    if LOGS_DIR.exists():
        for path in sorted(LOGS_DIR.iterdir(), reverse=True)[:50]:  # Last 50 logs
            if path.suffix in (".txt", ".json"):
                log_files.append({
                    "name": path.name,
                    "size": round(path.stat().st_size / 1024, 1),
                    "modified": datetime.datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
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
    )


@app.route("/gradcam/<path:filename>")
@login_required
def serve_gradcam(filename: str):
    """Serve a user's Grad-CAM image from their report directory."""
    safe_name = Path(filename).name
    reports_dir = UserDataManager().get_user_reports_dir(current_user.id)
    return send_from_directory(reports_dir, safe_name)

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
    from getpass import getpass
    
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

# ══════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    with app.app_context():
        init_db()
    
    app.run(host="0.0.0.0", port=APP_PORT, debug=APP_DEBUG)
