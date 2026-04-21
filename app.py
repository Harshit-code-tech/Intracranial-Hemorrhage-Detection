"""
ICH Screening Web Application
==============================
Features:
  1. Upload a .dcm file -> run AI model -> display screening report
  2. Browse past screening reports with date, outcome, band, urgency filters
  3. View execution logs from inference runs

Run:
    python webapp/app.py
    Open http://127.0.0.1:7860
"""

from __future__ import annotations
import run_interface as ri
import csv
import datetime
import json
import math
import logging
import os
import shutil
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

hf_hub_download: Any = None
try:
    import huggingface_hub
    hf_hub_download = getattr(huggingface_hub, "hf_hub_download", None)
except Exception:
    hf_hub_download = None

try:
    import blackbox_recorder as bbr  # type: ignore[import-untyped]
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
    Flask, abort, flash, g, jsonify, redirect,
    render_template, request, send_from_directory, url_for,
)
from werkzeug.utils import secure_filename


# ══════════════════════════════════════════════════════════════════════════
#  PATH CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════

BASE_DIR    = Path(__file__).resolve().parent        # webapp/
PROJECT_DIR = BASE_DIR                               # project root
TEST_DIR    = BASE_DIR
MODEL_DIR   = BASE_DIR / "download_imp"
OUTPUT_DIR  = MODEL_DIR / "outputs"
REPORTS_DIR = OUTPUT_DIR / "reports"
SUMMARY_CSV = OUTPUT_DIR / "report_summary.csv"
CALIB_JSON  = MODEL_DIR / "calibration_params.json"
NORM_JSON   = MODEL_DIR / "normalization_stats.json"
MODEL_PATH  = MODEL_DIR / "best_model_fold4.pth"
UPLOAD_DIR  = BASE_DIR / "uploads"
LOGS_DIR    = BASE_DIR / "logs"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int, *, minimum: int | None = None) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if minimum is not None and value < minimum:
        return default
    return value


# ══════════════════════════════════════════════════════════════════════════
#  FLASK SETUP
# ══════════════════════════════════════════════════════════════════════════

if load_dotenv is not None:
    load_dotenv(BASE_DIR / ".env")

APP_DEBUG = _env_bool("ICH_APP_DEBUG", True)
APP_PORT = _env_int("ICH_APP_PORT", _env_int("PORT", 7860, minimum=1), minimum=1)
MAX_UPLOAD_MB = _env_int("ICH_MAX_UPLOAD_MB", 2048, minimum=1)
LOG_LEVEL_NAME = os.environ.get("ICH_LOG_LEVEL", "INFO").strip().upper()
LOG_LEVEL = getattr(logging, LOG_LEVEL_NAME, logging.INFO)
SECRET_KEY = os.environ.get("ICH_SECRET_KEY", "").strip()
HF_MODEL_REPO = os.environ.get("ICH_HF_MODEL_REPO", os.environ.get("HF_REPO_ID", "")).strip()
HF_TOKEN = os.environ.get("ICH_HF_TOKEN", os.environ.get("HF_TOKEN", "")).strip()

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = SECRET_KEY or os.urandom(24)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

# Local mode: enables server-side directory scanning.
# Auto-detected (running from source) or forced via env var.
LOCAL_MODE = _env_bool("ICH_LOCAL_MODE", True)

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("ich_app")


# ══════════════════════════════════════════════════════════════════════════
#  BLACKBOX RECORDER — traces inference function calls
#
#  We configure it once at module level.  start()/stop() bracket each
#  inference run.  After each run, the trace is saved to logs/ as both a
#  human-readable .txt and a structured .json.
# ══════════════════════════════════════════════════════════════════════════

LOGS_DIR.mkdir(parents=True, exist_ok=True)

bbr.configure(
    include=["run_interface", "app"],
    capture_args=True,
    capture_returns=True,
    sampling_rate=1.0,
)


def _save_trace(image_id: str) -> dict[str, str | None]:
    """
    Save the current blackbox trace to logs/ and return metadata about it.
    Called immediately after bbr.stop().
    """
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"{ts}_{image_id}"
    txt_path  = LOGS_DIR / f"{base}.txt"
    json_path = LOGS_DIR / f"{base}.json"

    try:
        bbr.save_report(str(txt_path))
    except Exception:
        logger.warning("Could not save text trace for %s", image_id)

    try:
        bbr.save_json(str(json_path))
    except Exception:
        logger.warning("Could not save JSON trace for %s", image_id)

    return {
        "timestamp": ts,
        "image_id":  image_id,
        "txt_file":  txt_path.name if txt_path.exists() else None,
        "json_file": json_path.name if json_path.exists() else None,
    }


# ══════════════════════════════════════════════════════════════════════════
#  BATCH PROCESSING STATE
#
#  Each batch job is a background thread processing a list of .dcm paths.
#  The UI polls /batch/status/<id> for live progress.
# ══════════════════════════════════════════════════════════════════════════

_BATCHES: dict[str, dict[str, Any]] = {}
_BATCHES_LOCK = threading.Lock()


def _new_batch(total: int, temp_dir: str | None = None) -> str:
    """Create a fresh batch record and return its unique ID."""
    batch_id = uuid.uuid4().hex[:12]
    with _BATCHES_LOCK:
        _BATCHES[batch_id] = {
            "status":       "running",     # running | completed | failed
            "total":        total,
            "processed":    0,
            "succeeded":    0,
            "failed_ids":   [],
            "current_file": "",
            "image_ids":    [],            # successfully processed IDs
            "started_at":   datetime.datetime.now().isoformat(),
            "finished_at":  None,
            "error":        None,
            "temp_dir":     temp_dir,      # cleaned up after completion
        }
    return batch_id


def _batch_update(batch_id: str, **kw: Any) -> None:
    """Thread-safe update of a batch record."""
    with _BATCHES_LOCK:
        if batch_id in _BATCHES:
            _BATCHES[batch_id].update(kw)


def _run_batch_worker(batch_id: str, dcm_paths: list[Path]):
    """
    Background thread: process a list of .dcm files sequentially.
    Updates the batch record after each file for real-time UI feedback.
    """
    succeeded_ids: list[str] = []
    failed_ids: list[str] = []

    for i, path in enumerate(dcm_paths, 1):
        image_id = path.stem
        _batch_update(batch_id, current_file=image_id, processed=i - 1)

        try:
            report, _trace = _run_inference_on_dcm(path)
            if report is not None:
                succeeded_ids.append(image_id)
            else:
                failed_ids.append(image_id)
        except Exception as e:
            logger.error("Batch %s: failed %s — %s", batch_id, image_id, e)
            failed_ids.append(image_id)

        _batch_update(
            batch_id,
            processed=i,
            succeeded=len(succeeded_ids),
            image_ids=list(succeeded_ids),
            failed_ids=list(failed_ids),
        )

    # Clean up temp directory if one was used (ZIP extraction)
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
    # Force cache reload on next page view
    _CACHE["data_signature"] = None
    logger.info(
        "Batch %s complete: %d/%d succeeded, %d failed",
        batch_id, len(succeeded_ids), len(dcm_paths), len(failed_ids),
    )


def _start_batch(dcm_paths: list[Path], temp_dir: str | None = None) -> str:
    """Create a batch job & launch its worker thread. Returns batch_id."""
    batch_id = _new_batch(total=len(dcm_paths), temp_dir=temp_dir)
    t = threading.Thread(
        target=_run_batch_worker,
        args=(batch_id, dcm_paths),
        daemon=True,
        name=f"batch-{batch_id}",
    )
    t.start()
    return batch_id


# ══════════════════════════════════════════════════════════════════════════
#  IN-MEMORY CACHE
# ══════════════════════════════════════════════════════════════════════════

_CACHE: dict[str, Any] = {
    "data_signature":       None,
    "cases":                {},
    "rows_sorted":          [],
    "data_last_refresh_ms": None,
    "data_last_cache_hit":  False,
    "calib_signature":      None,
    "calib":                {},
    "norm_signature":       None,
    "norm":                 {},
}


# ══════════════════════════════════════════════════════════════════════════
#  MODEL STATE — lazy-loaded on first upload
# ══════════════════════════════════════════════════════════════════════════

_MODEL: dict[str, Any] = {
    "loaded":        False,
    "model":         None,
    "grad_cam":      None,
    "loaded_folds":  [],
    "transform":     None,
    "device":        None,
    "temperature":   None,
    "calib_cfg":     None,
    "inference_mod": None,
}


def _required_model_files(fold_selection: str) -> list[str]:
    files = [
        "calibration_params.json",
        "normalization_stats.json",
    ]
    raw = (fold_selection or "ensemble").strip().lower()
    if raw in ("", "ensemble", "all"):
        files.extend([f"best_model_fold{i}.pth" for i in range(5)])
        return files
    if raw == "best":
        files.append("best_model_fold4.pth")
        return files
    if raw.isdigit():
        files.append(f"best_model_fold{int(raw)}.pth")
        return files
    # Fallback to ensemble behavior for unknown values.
    files.extend([f"best_model_fold{i}.pth" for i in range(5)])
    return files


def _download_runtime_artifacts_if_needed(fold_selection: str) -> bool:
    required_files = _required_model_files(fold_selection)
    missing = [name for name in required_files if not (MODEL_DIR / name).exists()]
    if not missing:
        return True

    if not HF_MODEL_REPO:
        logger.warning(
            "Missing runtime model files (%s) and ICH_HF_MODEL_REPO/HF_REPO_ID is not set.",
            ", ".join(missing),
        )
        return False

    if hf_hub_download is None:
        logger.error(
            "huggingface_hub is not installed, cannot download missing model artifacts."
        )
        return False

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading missing model artifacts from Hugging Face repo: %s", HF_MODEL_REPO)
    try:
        for filename in missing:
            hf_hub_download(
                repo_id=HF_MODEL_REPO,
                filename=filename,
                repo_type="model",
                local_dir=str(MODEL_DIR),
                token=HF_TOKEN or None,
            )
            logger.info("Downloaded artifact: %s", filename)
        return True
    except Exception as exc:
        logger.error("Failed downloading model artifacts from Hugging Face: %s", exc)
        return False


def _ensure_model_loaded() -> bool:
    """Lazy-load the ML model on first inference request."""
    if _MODEL["loaded"]:
        return True

    try:
        import torch

        sys.path.insert(0, str(BASE_DIR))

        device = "cuda" if torch.cuda.is_available() else "cpu"
        fold_selection = os.environ.get("ICH_FOLD_SELECTION", "ensemble")

        _download_runtime_artifacts_if_needed(fold_selection)

        if not CALIB_JSON.exists():
            logger.error(
                "Missing calibration file at %s. Provide local files or set ICH_HF_MODEL_REPO.",
                CALIB_JSON,
            )
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
            logger.error("No fold checkpoints could be loaded from %s", MODEL_DIR)
            return False

        transform = ri.T.Compose([
            ri.T.ToPILImage(),
            ri.T.ToTensor(),
            ri.T.Normalize(mean=mean, std=std),
        ])

        _MODEL.update({
            "loaded":        True,
            "model":         models,
            "grad_cam":      grad_cams,
            "loaded_folds":  loaded_folds,
            "transform":     transform,
            "device":        device,
            "temperature":   float(calib_cfg.get("temperature", 1.0)),
            "calib_cfg":     calib_cfg,
            "inference_mod": ri,
        })
        logger.info(
            "Model loaded (device=%s, fold_selection=%s, folds=%s)",
            device,
            fold_selection,
            loaded_folds,
        )
        return True

    except Exception as e:
        logger.error("Model loading failed: %s", e, exc_info=True)
        return False


def _run_inference_on_dcm(dcm_path: Path) -> tuple[dict[str, Any] | None, dict[str, str | None] | None]:
    """
    Run inference on one .dcm file, with blackbox tracing.
    Returns (report_dict, trace_metadata) or (None, None) on failure.
    """
    if not _ensure_model_loaded():
        return None, None

    ri = _MODEL["inference_mod"]
    image_id = dcm_path.stem

    # Start tracing this inference run
    bbr.start()

    try:
        img_rgb = ri.dicom_to_rgb(str(dcm_path), size=ri.IMG_SIZE)

        inference = ri.infer_single(
            img_rgb,
            _MODEL["model"],
            _MODEL["grad_cam"],
            _MODEL["transform"],
            _MODEL["device"],
            _MODEL["temperature"],
        )

        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        report = ri.build_report(
            image_id, inference, _MODEL["calib_cfg"],
            REPORTS_DIR, img_rgb, true_label=None,
        )
        pred = report.get("prediction", {})
        pred.setdefault("raw_probability", inference.get("raw_prob_any"))
        pred.setdefault("calibrated_probability", inference.get("cal_prob_any"))
        pred.setdefault("decision_threshold", pred.get("decision_threshold_any"))
        report["prediction"] = pred

        report_path = REPORTS_DIR / f"{image_id}_report.json"
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2)

        _append_to_summary_csv(image_id, report)
        _CACHE["data_signature"] = None

    except Exception:
        bbr.stop()
        raise

    # Stop tracing and save the execution log
    bbr.stop()
    trace_meta = _save_trace(image_id)

    return report, trace_meta


def _append_to_summary_csv(image_id: str, report: dict[str, Any]) -> None:
    """Append one report row to the summary CSV."""
    pred = report["prediction"]
    row: dict[str, Any] = {
        "image_id":          image_id,
        "true_label":        "",
        "screening_outcome": pred["screening_outcome"],
        "raw_prob":          pred["raw_probability"],
        "cal_prob":          pred["calibrated_probability"],
        "confidence_band":   pred["confidence_band"],
        "triage_action":     report["triage"]["action"],
        "urgency":           report["triage"]["urgency"],
        "generated_at":      report.get("generated_at", ""),
    }

    file_exists = SUMMARY_CSV.exists()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with open(SUMMARY_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


# ══════════════════════════════════════════════════════════════════════════
#  DATA MODEL
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class CaseRow:
    image_id:     str        = ""
    outcome:      str        = "Unknown"
    raw_prob:     float|None = None
    cal_prob:     float|None = None
    band:         str        = "N/A"
    triage:       str        = "N/A"
    urgency:      str        = "N/A"
    true_label:   str        = ""
    generated_at: str        = ""     # ISO timestamp from report JSON
    report_file:  str|None   = None
    gradcam_file: str|None   = None

    @property
    def date_display(self) -> str:
        """Format the ISO timestamp as a short readable date."""
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


# ══════════════════════════════════════════════════════════════════════════
#  UTILITIES
# ══════════════════════════════════════════════════════════════════════════

def _to_float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _file_mtime(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns if path.exists() else -1
    except OSError:
        return -1


def _data_signature() -> tuple[int, int]:
    return _file_mtime(REPORTS_DIR), _file_mtime(SUMMARY_CSV)


def _parse_positive_int(value: str | None, default: int) -> int:
    try:
        n = int(value or default)
        return n if n > 0 else default
    except (TypeError, ValueError):
        return default


# ══════════════════════════════════════════════════════════════════════════
#  DATA LOADING
# ══════════════════════════════════════════════════════════════════════════

def _load_summary_csv() -> dict[str, dict[str, Any]]:
    """Read report_summary.csv into memory, keyed by image_id."""
    if not SUMMARY_CSV.exists():
        return {}
    rows: dict[str, dict[str, Any]] = {}
    with SUMMARY_CSV.open("r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            iid = (row.get("image_id") or "").strip()
            if not iid:
                continue
            rows[iid] = {
                "image_id":     iid,
                "outcome":      row.get("screening_outcome", "Unknown"),
                "raw_prob":     _to_float(row.get("raw_prob")),
                "cal_prob":     _to_float(row.get("cal_prob")),
                "band":         row.get("confidence_band") or "N/A",
                "triage":       row.get("triage_action")   or "N/A",
                "urgency":      row.get("urgency")         or "N/A",
                "true_label":   row.get("true_label", ""),
                "generated_at": row.get("generated_at", ""),
            }
    return rows


def _scan_report_assets() -> tuple[set[str], set[str]]:
    """One dir walk to find which image IDs have JSON and PNG files."""
    report_ids:  set[str] = set()
    gradcam_ids: set[str] = set()
    if not REPORTS_DIR.exists():
        return report_ids, gradcam_ids
    for path in REPORTS_DIR.iterdir():
        if not path.is_file():
            continue
        if path.name.endswith("_report.json"):
            report_ids.add(path.name[:-12])
        elif path.name.endswith("_gradcam.png"):
            gradcam_ids.add(path.name[:-12])
    return report_ids, gradcam_ids


def _read_generated_at(image_id: str) -> str:
    """Read the generated_at timestamp from a report JSON file."""
    path = REPORTS_DIR / f"{image_id}_report.json"
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text("utf-8"))
        return data.get("generated_at", "")
    except (json.JSONDecodeError, OSError):
        return ""


def _load_cases_from_json() -> dict[str, CaseRow]:
    """Fallback: read each *_report.json when CSV is unavailable."""
    summary = _load_summary_csv()
    cases: dict[str, CaseRow] = {}
    for rp in sorted(REPORTS_DIR.glob("*_report.json")):
        try:
            payload = json.loads(rp.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        iid  = str(payload.get("image_id", rp.stem.replace("_report", ""))).strip()
        pred = payload.get("prediction", {})
        tri  = payload.get("triage", {})
        expl = payload.get("explainability", {})
        sr   = summary.get(iid, {})
        gc   = Path(str(expl.get("heatmap_path", ""))).name or None
        cases[iid] = CaseRow(
            image_id=iid,
            outcome=pred.get("screening_outcome", sr.get("outcome", "Unknown")),
            raw_prob=_to_float(pred.get("raw_probability", sr.get("raw_prob"))),
            cal_prob=_to_float(pred.get("calibrated_probability", sr.get("cal_prob"))),
            band=pred.get("confidence_band", sr.get("band", "N/A")),
            triage=tri.get("action", sr.get("triage", "N/A")),
            urgency=tri.get("urgency", sr.get("urgency", "N/A")),
            true_label=str(payload.get("ground_truth_label", sr.get("true_label", ""))),
            generated_at=payload.get("generated_at", ""),
            report_file=rp.name,
            gradcam_file=gc,
        )
    return cases


def load_cases_cached() -> dict[str, CaseRow]:
    """Return all cases, re-reading from disk only when files change."""
    sig = _data_signature()
    if _CACHE["data_signature"] == sig:
        _CACHE["data_last_cache_hit"] = True
        return _CACHE["cases"]

    start   = time.perf_counter()
    summary = _load_summary_csv()

    if summary:
        report_ids, gradcam_ids = _scan_report_assets()
        cases = {}
        for iid, sr in summary.items():
            # Resolve generated_at: prefer CSV value, fall back to JSON file
            gen_at = sr.get("generated_at", "")
            if not gen_at and iid in report_ids:
                gen_at = _read_generated_at(iid)

            cases[iid] = CaseRow(
                image_id=iid,
                outcome=sr.get("outcome", "Unknown"),
                raw_prob=_to_float(sr.get("raw_prob")),
                cal_prob=_to_float(sr.get("cal_prob")),
                band=sr.get("band", "N/A"),
                triage=sr.get("triage", "N/A"),
                urgency=sr.get("urgency", "N/A"),
                true_label=sr.get("true_label", ""),
                generated_at=gen_at,
                report_file=f"{iid}_report.json" if iid in report_ids else None,
                gradcam_file=f"{iid}_gradcam.png" if iid in gradcam_ids else None,
            )
    elif REPORTS_DIR.exists():
        cases = _load_cases_from_json()
    else:
        cases = {}

    elapsed_ms = (time.perf_counter() - start) * 1000
    _CACHE.update({
        "data_signature":       sig,
        "cases":                cases,
        "rows_sorted":          sorted(cases.values(), key=lambda c: c.image_id),
        "data_last_refresh_ms": elapsed_ms,
        "data_last_cache_hit":  False,
    })
    logger.info("Cache refresh: %d cases in %.1f ms", len(cases), elapsed_ms)
    return cases


def load_case_payload(image_id: str) -> dict[str, Any] | None:
    """Load full JSON report for one case (Raw JSON button)."""
    path = REPORTS_DIR / f"{image_id}_report.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def compute_stats(rows: list[CaseRow]) -> dict[str, Any]:
    """Compute summary statistics for the dashboard cards."""
    total    = len(rows)
    positive = sum(1 for r in rows if r.is_positive)
    urgent   = sum(1 for r in rows if r.urgency.upper() == "URGENT")
    heatmaps = sum(1 for r in rows if r.gradcam_file)
    cal_probs = [r.cal_prob for r in rows if r.cal_prob is not None]
    avg_cal   = sum(cal_probs) / len(cal_probs) if cal_probs else 0.0
    pos_rate  = (positive / total * 100) if total else 0.0

    # Date range
    dates = sorted(r.generated_at for r in rows if r.generated_at)
    newest = dates[-1] if dates else ""
    oldest = dates[0]  if dates else ""

    return {
        "total":          total,
        "positive":       positive,
        "negative":       total - positive,
        "urgent":         urgent,
        "heatmaps":       heatmaps,
        "avg_cal_prob":   avg_cal,
        "pos_rate":       pos_rate,
        "band_counts":    dict(Counter(r.band.upper()    for r in rows)),
        "urgency_counts": dict(Counter(r.urgency.upper() for r in rows)),
        "newest_date":    newest,
        "oldest_date":    oldest,
    }


def _load_json_cached(path: Path, sig_key: str, data_key: str, label: str) -> dict[str, Any]:
    """Mtime-based JSON cache loader for calibration/normalization."""
    sig = _file_mtime(path)
    if _CACHE[sig_key] == sig:
        return _CACHE[data_key]
    data: dict[str, Any] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("Could not read %s", path)
    _CACHE[sig_key] = sig
    _CACHE[data_key] = data
    return data


def load_calibration() -> dict[str, Any]:
    calib = _load_json_cached(CALIB_JSON, "calib_signature", "calib", "Calibration")
    if not calib:
        return {}
    # Backward-compatible aliases expected by templates.
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


def load_normalization() -> dict[str, Any]:
    return _load_json_cached(NORM_JSON, "norm_signature", "norm", "Normalization")


def filter_cases(
    rows: list[CaseRow],
    q: str,
    band: str,
    urgency: str,
    outcome: str,
    sort_by: str,
) -> list[CaseRow]:
    """Apply text search, dropdown filters, and sorting."""
    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in r.image_id.lower() or ql in r.outcome.lower()]
    if band:
        rows = [r for r in rows if r.band.upper() == band.upper()]
    if urgency:
        rows = [r for r in rows if r.urgency.upper() == urgency.upper()]
    if outcome == "POSITIVE":
        rows = [r for r in rows if r.is_positive]
    elif outcome == "NEGATIVE":
        rows = [r for r in rows if not r.is_positive]

    if sort_by == "date_desc":
        rows = sorted(rows, key=lambda r: r.generated_at or "", reverse=True)
    elif sort_by == "date_asc":
        rows = sorted(rows, key=lambda r: r.generated_at or "")
    elif sort_by == "prob_desc":
        rows = sorted(rows, key=lambda r: r.cal_prob or 0, reverse=True)
    elif sort_by == "prob_asc":
        rows = sorted(rows, key=lambda r: r.cal_prob or 0)
    # default: sorted by image_id (already the case from cache)

    return rows


def load_logs() -> list[dict[str, Any]]:
    """Scan the logs/ directory and return metadata for each trace."""
    if not LOGS_DIR.exists():
        return []

    log_files: dict[str, dict[str, Any]] = {}  # base_name -> {txt_file, json_file, ...}

    for path in sorted(LOGS_DIR.iterdir(), reverse=True):
        if not path.is_file():
            continue
        stem = path.stem  # e.g. "20260228_153000_ID_abc123"
        if path.suffix == ".txt":
            log_files.setdefault(stem, {})["txt_file"] = path.name
            # Parse out timestamp and image_id from filename
            parts = stem.split("_", 2)
            if len(parts) >= 3:
                log_files[stem]["timestamp"] = f"{parts[0]}_{parts[1]}"
                log_files[stem]["image_id"]  = parts[2]
            log_files[stem]["size_kb"] = round(path.stat().st_size / 1024, 1)
        elif path.suffix == ".json":
            log_files.setdefault(stem, {})["json_file"] = path.name

    entries: list[dict[str, Any]] = []
    for stem in sorted(log_files, reverse=True):
        info = log_files[stem]
        ts_raw = info.get("timestamp", "")
        try:
            dt = datetime.datetime.strptime(ts_raw, "%Y%m%d_%H%M%S")
            display = dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            display = ts_raw
        entries.append({
            "stem":      stem,
            "timestamp": display,
            "image_id":  info.get("image_id", ""),
            "txt_file":  info.get("txt_file"),
            "json_file": info.get("json_file"),
            "size_kb":   info.get("size_kb", 0),
        })

    return entries


# ══════════════════════════════════════════════════════════════════════════
#  MIDDLEWARE
# ══════════════════════════════════════════════════════════════════════════

@app.before_request
def _start_timer() -> None:  # pyright: ignore[reportUnusedFunction]
    g._start_time = time.perf_counter()


@app.after_request
def _log_timing(response: Any) -> Any:  # pyright: ignore[reportUnusedFunction]
    elapsed = (time.perf_counter() - getattr(g, "_start_time", time.perf_counter())) * 1000
    logger.info("%s %s -> %s (%.1f ms)", request.method, request.path, response.status_code, elapsed)
    return response


# ══════════════════════════════════════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════════════════════════════════════

@app.route("/")
def home():
    """Landing page with quick stats and navigation."""
    load_cases_cached()
    all_rows = _CACHE["rows_sorted"]
    stats = compute_stats(all_rows)
    log_count = len(list(LOGS_DIR.glob("*.txt"))) if LOGS_DIR.exists() else 0
    return render_template("home.html", stats=stats, log_count=log_count)


@app.route("/upload")
def upload():
    return render_template("upload.html", local_mode=LOCAL_MODE)


@app.route("/analyze", methods=["POST"])
def analyze():
    """
    Accept one or more .dcm files (or a .zip) and run inference.

    Single file  → synchronous, redirect straight to the report.
    Multiple     → asynchronous batch, redirect to progress page.
    """
    files = request.files.getlist("file")
    files = [f for f in files if f.filename]

    if not files:
        flash("No files were uploaded.", "error")
        return redirect(url_for("upload"))

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    # ── Collect all .dcm paths (expand .zip archives) ────────────────
    dcm_paths: list[Path] = []
    temp_dir: str | None = None      # set if a zip needed extraction

    for f in files:
        filename = f.filename or ""
        fname = filename.lower()

        if fname.endswith(".zip"):
            temp_dir = tempfile.mkdtemp(prefix="ich_zip_")
            zip_save = Path(temp_dir) / secure_filename(filename)
            f.save(str(zip_save))
            try:
                with zipfile.ZipFile(zip_save, "r") as zf:
                    zf.extractall(temp_dir)
            except zipfile.BadZipFile:
                shutil.rmtree(temp_dir, ignore_errors=True)
                flash("The uploaded ZIP file is corrupted.", "error")
                return redirect(url_for("upload"))
            # Recursively find .dcm inside extracted tree
            dcm_paths.extend(sorted(Path(temp_dir).rglob("*.dcm")))

        elif fname.endswith(".dcm"):
            safe = secure_filename(filename)
            save_path = UPLOAD_DIR / safe
            f.save(str(save_path))
            dcm_paths.append(save_path)

        else:
            # skip non-dcm / non-zip silently
            continue

    if not dcm_paths:
        flash("No .dcm files found in the upload.", "error")
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        return redirect(url_for("upload"))

    # ── Single file → synchronous (fast path) ────────────────────────
    if len(dcm_paths) == 1 and temp_dir is None:
        single_path = dcm_paths[0]
        try:
            report, _trace = _run_inference_on_dcm(single_path)
            if report is None:
                flash("Model failed to load. Check server logs.", "error")
                return redirect(url_for("upload"))
            return redirect(url_for("case_detail", image_id=single_path.stem))
        except Exception as e:
            logger.error("Analysis failed for %s: %s", single_path.name, e, exc_info=True)
            flash(f"Analysis failed: {e}", "error")
            return redirect(url_for("upload"))
        finally:
            if single_path.exists() and single_path.parent == UPLOAD_DIR:
                single_path.unlink()

    # ── Multiple files → asynchronous batch ──────────────────────────
    batch_id = _start_batch(dcm_paths, temp_dir=temp_dir)
    logger.info("Batch %s started: %d files", batch_id, len(dcm_paths))
    return redirect(url_for("batch_progress", batch_id=batch_id))


@app.route("/analyze/directory", methods=["POST"])
def analyze_directory():
    """
    Local-only route: scan a server-side directory for .dcm files and
    start a batch job.  Disabled when LOCAL_MODE is off.
    """
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

    batch_id = _start_batch(dcm_paths)
    logger.info("Directory batch %s started: %d files from %s", batch_id, len(dcm_paths), dir_path_str)
    return redirect(url_for("batch_progress", batch_id=batch_id))


@app.route("/batch/progress/<batch_id>")
def batch_progress(batch_id: str):
    """Batch progress page — polls /batch/status/<id> via JS."""
    with _BATCHES_LOCK:
        batch = _BATCHES.get(batch_id)
    if not batch:
        abort(404)
    return render_template("batch_progress.html", batch_id=batch_id, batch=batch)


@app.route("/batch/status/<batch_id>")
def batch_status(batch_id: str):
    """JSON endpoint polled by the progress page for live updates."""
    with _BATCHES_LOCK:
        batch = _BATCHES.get(batch_id)
    if not batch:
        return jsonify({"error": "not found"}), 404
    # Return a safe copy (no Path objects)
    return jsonify({
        "status":       batch["status"],
        "total":        batch["total"],
        "processed":    batch["processed"],
        "succeeded":    batch["succeeded"],
        "failed_count": len(batch["failed_ids"]),
        "failed_ids":   batch["failed_ids"][:20],   # cap for payload size
        "current_file": batch["current_file"],
        "image_ids":    batch["image_ids"][-5:],     # last 5 for display
        "started_at":   batch["started_at"],
        "finished_at":  batch["finished_at"],
    })


@app.route("/reports")
def reports():
    """Past reports page with filtering, sorting, and pagination."""
    route_start = time.perf_counter()

    load_cases_cached()
    all_rows = _CACHE["rows_sorted"]

    # Read all filter/sort/pagination params from query string
    q         = request.args.get("q", "").strip()
    band      = request.args.get("band", "").strip()
    urgency   = request.args.get("urgency", "").strip()
    outcome   = request.args.get("outcome", "").strip()
    sort_by   = request.args.get("sort", "").strip()
    page      = _parse_positive_int(request.args.get("page"), 1)
    page_size = _parse_positive_int(request.args.get("page_size"), 50)
    if page_size not in (10, 50, 100):
        page_size = 50

    filtered    = filter_cases(all_rows, q, band, urgency, outcome, sort_by)
    stats       = compute_stats(filtered)
    total       = len(filtered)
    total_pages = max(1, math.ceil(total / page_size))
    page        = min(page, total_pages)
    start_idx   = (page - 1) * page_size
    rows        = filtered[start_idx: start_idx + page_size]
    route_ms    = (time.perf_counter() - route_start) * 1000

    return render_template(
        "reports.html",
        rows=rows,
        stats=stats,
        calib=load_calibration(),
        q=q, band=band, urgency=urgency, outcome=outcome, sort=sort_by,
        page=page,
        page_size=page_size,
        page_start=start_idx,
        total_pages=total_pages,
        total_items=total,
        total_cases=len(all_rows),
        route_compute_ms=route_ms,
        data_refresh_ms=_CACHE["data_last_refresh_ms"],
        data_cache_hit=_CACHE["data_last_cache_hit"],
    )


@app.route("/case/<image_id>")
def case_detail(image_id: str):
    """Individual case report page."""
    cases = load_cases_cached()
    row = cases.get(image_id)
    if not row:
        abort(404)
    payload = load_case_payload(image_id)
    return render_template("detail.html", row=row, payload=payload)


@app.route("/logs")
def logs_page():
    """Execution logs page."""
    entries = load_logs()
    return render_template("logs.html", logs=entries)


@app.route("/logs/view/<path:filename>")
def serve_log(filename: str):
    """Serve a log file (txt or json) for viewing."""
    if not LOGS_DIR.exists():
        abort(404)
    return send_from_directory(LOGS_DIR, filename)


@app.route("/evaluation")
def evaluation():
    load_cases_cached()
    all_rows = _CACHE["rows_sorted"]

    cal_probs = [r.cal_prob for r in all_rows if r.cal_prob is not None]
    bins = [0] * 10
    for p in cal_probs:
        bins[min(int(p * 10), 9)] += 1

    band_data = {}
    for bnd in ("HIGH", "MEDIUM", "LOW"):
        subset   = [r for r in all_rows if r.band.upper() == bnd]
        positive = sum(1 for r in subset if r.is_positive)
        band_data[bnd] = {
            "total":    len(subset),
            "positive": positive,
            "negative": len(subset) - positive,
        }

    return render_template(
        "evaluation.html",
        stats=compute_stats(all_rows),
        calib=load_calibration(),
        norm=load_normalization(),
        bins=bins,
        band_data=band_data,
        total=len(all_rows),
    )


@app.route("/about")
def about():
    return render_template("about.html", calib=load_calibration())


@app.route("/gradcam/<path:filename>")
def serve_gradcam(filename: str):
    if not REPORTS_DIR.exists():
        abort(404)
    return send_from_directory(REPORTS_DIR, filename)


@app.route("/report-json/<path:filename>")
def serve_report_json(filename: str):
    if not REPORTS_DIR.exists():
        abort(404)
    return send_from_directory(REPORTS_DIR, filename)


# ══════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("  ICH Screening Web Application")
    print(f"  Data  ->  {OUTPUT_DIR}")
    print(f"  Logs  ->  {LOGS_DIR}")
    print(f"  Open  ->  http://127.0.0.1:{APP_PORT}")
    print("=" * 60)
    app.run(debug=APP_DEBUG, port=APP_PORT)
