"""
Standalone Inference Script — Improved ICH Screening (2.5D, 5-fold Ensemble)
=============================================================================
Reads raw DICOM CT brain slices, reproduces improved preprocessing from the
improvement notebooks, runs 5-fold EfficientNet-B4 ensemble inference, applies
saved calibration, and generates:
  • Per-image JSON reports (fixed schema)
  • Slice-level CSV summary
  • Patient-level CSV summary

No command-line arguments — all paths are configured in the CONFIG section.

Requirements:
  pip install torch timm pydicom opencv-python-headless numpy pandas scikit-learn

Usage:
  python run_inference.py
"""

import datetime
import json
import pickle
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import cv2
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import timm

# Try importing pydicom — needed for DICOM input mode
has_pydicom = False
pydicom = None
try:
    import pydicom
    import pydicom.multival

    # Some anonymized datasets contain non-standard UID strings like "ID_xxx".
    # Ignore only this known noisy warning from pydicom.
    warnings.filterwarnings(
        "ignore",
        message=r"Invalid value for VR UI:",
        category=UserWarning,
        module=r"pydicom\.valuerep",
    )
    has_pydicom = True
except ImportError:
    pass

# ══════════════════════════════════════════════════════════════════════════
#  CONFIG — edit these paths before running
# ══════════════════════════════════════════════════════════════════════════

SCRIPT_DIR = Path(__file__).resolve().parent

# Model artifacts (required)
FOLD_MODEL_PATHS = [SCRIPT_DIR / f"best_model_fold{i}.pth" for i in range(5)]
CALIB_PARAMS_PATH = SCRIPT_DIR / "calibration_params.json"
ISOTONIC_MODELS_PATH = SCRIPT_DIR / "isotonic_models.pkl"
NORM_STATS_PATH = SCRIPT_DIR / "normalization_stats.json"

# Input — folder containing .dcm files
DICOM_INPUT_DIR = Path(r"D:\major8thsem\stage_2_test")

# Optional labels (only for quick validation against known RSNA IDs)
MANIFEST_PATH = SCRIPT_DIR / "manifest.csv"

# Output
OUTPUT_DIR = SCRIPT_DIR / "outputs"

# Architecture constants (must match training notebooks)
BACKBONE = "tf_efficientnet_b4"
IMG_SIZE = 380
IN_CHANNELS = 9
N_CLASSES = 6
DROPOUT = 0.4
DROP_PATH = 0.2

# Output / triage behavior
PATIENT_AGG_METHOD = "topk_mean"  # one of: max, mean, noisy_or, topk_mean
PATIENT_TOPK = 3
DECISION_THRESHOLD = None  # None -> use threshold_at_spec90 from calibration JSON
FOLD_SELECTION = "ensemble"  # "ensemble" or an integer fold id: 0..4
GENERATE_HEATMAPS = True

WINDOWS = [
    (40, 80),  # brain
    (75, 215),  # subdural
    (40, 380),  # soft tissue / bone
]

SUBTYPES = [
    "any",
    "epidural",
    "intraparenchymal",
    "intraventricular",
    "subarachnoid",
    "subdural",
]

OUTCOME_POSITIVE = "Hemorrhage indicator detected"
OUTCOME_NEGATIVE = "No hemorrhage indicator detected"

BAND_LABELS = {
    "HIGH": "High confidence",
    "MEDIUM": "Moderate confidence",
    "LOW": "Low confidence",
}

TRIAGE_ACTIONS = {
    ("POSITIVE", "HIGH"): "Urgent radiologist review recommended",
    ("POSITIVE", "MEDIUM"): "Prioritised radiologist review recommended",
    ("POSITIVE", "LOW"): "Radiologist review recommended — low confidence",
    ("NEGATIVE", "HIGH"): "Standard workflow — no urgent action",
    ("NEGATIVE", "MEDIUM"): "Standard workflow — manual review if clinically indicated",
    ("NEGATIVE", "LOW"): "Manual review recommended — model uncertainty high",
}

DISCLAIMER = (
    "This report is produced by an AI-assisted screening tool and does NOT "
    "constitute a medical diagnosis. All screening findings must be reviewed "
    "and confirmed by a qualified, licensed medical professional before any "
    "clinical decision is made. The system is intended solely as a "
    "decision-support aid in a screening workflow and is not cleared for "
    "standalone diagnostic use."
)

IST = ZoneInfo("Asia/Kolkata")


# ══════════════════════════════════════════════════════════════════════════
#  DICOM + PREPROCESSING
# ══════════════════════════════════════════════════════════════════════════


def _to_scalar(val) -> float:
    if has_pydicom and isinstance(val, (list, pydicom.multival.MultiValue)):
        return float(val[0])
    return float(val)


def apply_window(img_hu: np.ndarray, wc: float, ww: float) -> np.ndarray:
    lo = wc - ww / 2
    hi = wc + ww / 2
    return np.clip((img_hu - lo) / (hi - lo), 0.0, 1.0)


def load_single_dicom_3ch(dcm_path: Path, size: int = IMG_SIZE) -> np.ndarray:
    if not has_pydicom or pydicom is None:
        raise RuntimeError("pydicom is not installed. Run: pip install pydicom")
    dcm = pydicom.dcmread(str(dcm_path))
    img = dcm.pixel_array.astype(np.float32)

    slope = _to_scalar(getattr(dcm, "RescaleSlope", 1))
    inter = _to_scalar(getattr(dcm, "RescaleIntercept", 0))
    img = img * slope + inter

    channels = []
    for wc, ww in WINDOWS:
        ch = apply_window(img, wc, ww)
        ch = cv2.resize(ch, (size, size), interpolation=cv2.INTER_AREA)
        channels.append(ch)

    return np.stack(channels, axis=-1).astype(np.float32)  # (H, W, 3) in [0,1]


def build_adjacency(dicom_dir: Path) -> pd.DataFrame:
    if not has_pydicom or pydicom is None:
        raise RuntimeError("pydicom is not installed. Run: pip install pydicom")
    records: List[dict] = []
    for dcm_path in sorted(dicom_dir.glob("*.dcm")):
        image_id = dcm_path.stem
        try:
            dcm = pydicom.dcmread(str(dcm_path), stop_before_pixels=True)
            patient_id = str(getattr(dcm, "PatientID", "UNKNOWN"))
            series_uid = str(getattr(dcm, "SeriesInstanceUID", "UNKNOWN_SERIES"))

            ipp = getattr(dcm, "ImagePositionPatient", None)
            if ipp is not None and len(ipp) >= 3:
                z_pos = float(ipp[2])
            else:
                z_pos = float(getattr(dcm, "SliceLocation", 0.0))
        except Exception:
            patient_id = "UNKNOWN"
            series_uid = "UNKNOWN_SERIES"
            z_pos = 0.0

        records.append(
            {
                "image_id": image_id,
                "patient_id": patient_id,
                "series_uid": series_uid,
                "z_pos": z_pos,
                "dcm_path": str(dcm_path),
            }
        )

    if not records:
        return pd.DataFrame(columns=["image_id", "patient_id", "series_uid", "z_pos", "dcm_path", "prev_image_id", "next_image_id"])

    df = pd.DataFrame(records)
    df = df.sort_values(["patient_id", "series_uid", "z_pos"]).reset_index(drop=True)
    df["prev_image_id"] = df.groupby(["patient_id", "series_uid"])["image_id"].shift(1)
    df["next_image_id"] = df.groupby(["patient_id", "series_uid"])["image_id"].shift(-1)
    return df


def build_9ch_for_row(row: pd.Series, image_path_map: Dict[str, Path], mean_9: np.ndarray, std_9: np.ndarray) -> np.ndarray:
    center_id = row["image_id"]
    prev_id = row["prev_image_id"] if pd.notna(row.get("prev_image_id")) else None
    next_id = row["next_image_id"] if pd.notna(row.get("next_image_id")) else None

    center_arr = load_single_dicom_3ch(image_path_map[center_id], size=IMG_SIZE)

    if prev_id is not None and prev_id in image_path_map:
        prev_arr = load_single_dicom_3ch(image_path_map[prev_id], size=IMG_SIZE)
    else:
        prev_arr = center_arr

    if next_id is not None and next_id in image_path_map:
        next_arr = load_single_dicom_3ch(image_path_map[next_id], size=IMG_SIZE)
    else:
        next_arr = center_arr

    img_9ch = np.concatenate([prev_arr, center_arr, next_arr], axis=-1).astype(np.float32)
    img_9ch = (img_9ch - mean_9.reshape(1, 1, -1)) / (std_9.reshape(1, 1, -1) + 1e-7)
    return img_9ch


# ══════════════════════════════════════════════════════════════════════════
#  MODEL + CALIBRATION
# ══════════════════════════════════════════════════════════════════════════


def build_model(
    backbone: str = BACKBONE,
    in_ch: int = IN_CHANNELS,
    n_cls: int = N_CLASSES,
    dropout: float = DROPOUT,
    drop_path: float = DROP_PATH,
) -> nn.Module:
    model = timm.create_model(
        backbone,
        pretrained=False,
        num_classes=0,
        drop_rate=dropout,
        drop_path_rate=drop_path,
    )

    old_conv = model.conv_stem
    new_conv = nn.Conv2d(
        in_ch,
        old_conv.out_channels,
        kernel_size=old_conv.kernel_size,
        stride=old_conv.stride,
        padding=old_conv.padding,
        bias=(old_conv.bias is not None),
    )
    k = max(in_ch // 3, 1)
    with torch.no_grad():
        new_conv.weight.copy_(old_conv.weight.repeat(1, k, 1, 1) / k)
        if old_conv.bias is not None:
            new_conv.bias.copy_(old_conv.bias)
    model.conv_stem = new_conv

    n_feat = model.num_features
    model.classifier = nn.Sequential(nn.Dropout(p=dropout), nn.Linear(n_feat, n_cls))
    return model


def _find_gradcam_target_layer(model: nn.Module) -> nn.Module:
    # Prefer the last semantic convolutional stage for EfficientNet-like models.
    if hasattr(model, "conv_head") and isinstance(model.conv_head, nn.Module):
        return model.conv_head
    conv_layers = [m for m in model.modules() if isinstance(m, nn.Conv2d)]
    if not conv_layers:
        raise RuntimeError("No convolutional layer found for Grad-CAM target")
    return conv_layers[-1]


class GradCAM:
    def __init__(self, model: nn.Module):
        self.model = model
        self.activations = None
        self.gradients = None
        target = _find_gradcam_target_layer(model)
        self._fh = target.register_forward_hook(self._forward_hook)
        self._bh = target.register_full_backward_hook(self._backward_hook)

    def _forward_hook(self, _module, _inputs, output):
        self.activations = output

    def _backward_hook(self, _module, _grad_input, grad_output):
        self.gradients = grad_output[0]

    def remove(self):
        self._fh.remove()
        self._bh.remove()

    def generate(self, input_tensor: torch.Tensor, class_idx: int = 0) -> Tuple[np.ndarray, np.ndarray]:
        self.model.zero_grad(set_to_none=True)
        use_amp = bool(input_tensor.is_cuda)
        with torch.enable_grad():
            with torch.cuda.amp.autocast(enabled=use_amp):
                output = self.model(input_tensor)
            target = output[:, class_idx].sum()
            target.backward()

        logits = output.detach().cpu().numpy().astype(np.float32)
        if logits.ndim == 1:
            logits = logits[None, :]

        if self.activations is None or self.gradients is None:
            cam = np.zeros((logits.shape[0], IMG_SIZE, IMG_SIZE), dtype=np.float32)
            return (logits[0], cam[0]) if logits.shape[0] == 1 else (logits, cam)

        acts = self.activations.detach()
        grads = self.gradients.detach()
        weights = grads.mean(dim=(2, 3), keepdim=True)
        cam = torch.relu((weights * acts).sum(dim=1)).cpu().numpy().astype(np.float32)
        if cam.ndim == 2:
            cam = cam[None, ...]

        for idx in range(cam.shape[0]):
            if cam[idx].size == 0 or float(cam[idx].max()) <= 0.0:
                cam[idx] = np.zeros((IMG_SIZE, IMG_SIZE), dtype=np.float32)
            else:
                cam[idx] = (cam[idx] - cam[idx].min()) / (cam[idx].max() - cam[idx].min() + 1e-8)

        return (logits[0], cam[0]) if logits.shape[0] == 1 else (logits, cam)


def make_overlay(orig_rgb_u8: np.ndarray, cam: np.ndarray, alpha: float = 0.45) -> np.ndarray:
    cam_r = cv2.resize(cam, (orig_rgb_u8.shape[1], orig_rgb_u8.shape[0]), interpolation=cv2.INTER_LINEAR)
    heat_u8 = np.uint8(np.clip(cam_r, 0.0, 1.0) * 255.0)
    heat_bgr = cv2.applyColorMap(heat_u8, cv2.COLORMAP_JET)
    heat_rgb = cv2.cvtColor(heat_bgr, cv2.COLOR_BGR2RGB)
    return (alpha * heat_rgb + (1 - alpha) * orig_rgb_u8).astype(np.uint8)


def load_models(device: str, fold_selection=None) -> Tuple[List[nn.Module], List[int]]:
    models = []
    loaded_folds: List[int] = []

    if fold_selection is None:
        fold_selection = FOLD_SELECTION

    if isinstance(fold_selection, str) and fold_selection.lower() == "ensemble":
        fold_indices = list(range(len(FOLD_MODEL_PATHS)))
    elif isinstance(fold_selection, int):
        fold_indices = [fold_selection]
    elif isinstance(fold_selection, str) and fold_selection.isdigit():
        fold_indices = [int(fold_selection)]
    else:
        raise ValueError('FOLD_SELECTION must be "ensemble" or an integer fold id (0..4).')

    for fold_idx in fold_indices:
        if fold_idx < 0 or fold_idx >= len(FOLD_MODEL_PATHS):
            print(f"  ⚠ Invalid fold index: {fold_idx} (skipping)")
            continue
        path = FOLD_MODEL_PATHS[fold_idx]
        if not path.exists():
            print(f"  ⚠ Missing fold checkpoint: {path.name} (skipping)")
            continue
        model = build_model()
        state = torch.load(str(path), map_location=device)
        model.load_state_dict(state, strict=True)
        model = model.to(device)
        model.eval()
        models.append(model)
        loaded_folds.append(fold_idx)
    return models, loaded_folds


def sigmoid_np(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def apply_calibration(raw_logits: np.ndarray, calib_cfg: dict, iso_models) -> np.ndarray:
    best_method = calib_cfg.get("best_method", "temperature")
    temperature = float(calib_cfg.get("temperature", 1.0))

    if best_method == "isotonic" and iso_models is not None:
        raw_probs = sigmoid_np(raw_logits)
        cal_probs = np.zeros_like(raw_probs, dtype=np.float32)
        for i, subtype in enumerate(SUBTYPES):
            model_i = None
            if isinstance(iso_models, dict):
                model_i = iso_models.get(subtype)
                if model_i is None:
                    model_i = iso_models.get(i)
            elif isinstance(iso_models, (list, tuple)) and i < len(iso_models):
                model_i = iso_models[i]

            if model_i is not None:
                cal_probs[i] = float(np.clip(model_i.predict([raw_probs[i]])[0], 0.0, 1.0))
            else:
                cal_probs[i] = float(raw_probs[i])
        return cal_probs

    return sigmoid_np(raw_logits / max(temperature, 1e-6)).astype(np.float32)


def patient_aggregate(values: np.ndarray, method: str, topk: int) -> float:
    if len(values) == 0:
        return 0.0
    if method == "max":
        return float(np.max(values))
    if method == "mean":
        return float(np.mean(values))
    if method == "noisy_or":
        return float(1.0 - np.prod(1.0 - np.clip(values, 0.0, 1.0)))
    if method == "topk_mean":
        k = min(max(int(topk), 1), len(values))
        top_vals = np.sort(values)[-k:]
        return float(np.mean(top_vals))
    raise ValueError(f"Unknown PATIENT_AGG_METHOD: {method}")


# ══════════════════════════════════════════════════════════════════════════
#  REPORT HELPERS
# ══════════════════════════════════════════════════════════════════════════


def build_slice_report(
    image_id: str,
    patient_id: str,
    probs: Dict[str, float],
    calib_cfg: dict,
    threshold: float,
    loaded_folds: List[int],
    report_image_path: Optional[str] = None,
    heatmap_path: Optional[str] = None,
    true_label: Optional[int] = None,
) -> dict:
    cal_any = probs["any"]
    high_thr = float(calib_cfg.get("triage_high_thresh", 0.7))
    low_thr = float(calib_cfg.get("triage_low_thresh", 0.3))

    if cal_any >= high_thr:
        band = "HIGH"
    elif cal_any >= low_thr:
        band = "MEDIUM"
    else:
        band = "LOW"

    is_positive = cal_any >= threshold
    outcome_key = "POSITIVE" if is_positive else "NEGATIVE"

    now_ist = datetime.datetime.now(IST)
    report = {
        "report_id": f"RPT_{now_ist.strftime('%Y%m%d_%H%M%S')}_{image_id[-8:]}",
        "generated_at": now_ist.isoformat(),
        "image_id": image_id,
        "patient_id": patient_id,
        "ground_truth_any": int(true_label) if true_label is not None else "N/A",
        "screening_module": {
            "version": "2.0",
            "architecture": BACKBONE,
            "input_type": "2.5D (9ch: prev+center+next)",
            "ensemble": "ensemble" if len(loaded_folds) > 1 else "single-fold",
            "folds_used": loaded_folds,
            "calibration_method": calib_cfg.get("best_method", "temperature"),
        },
        "prediction": {
            "screening_outcome": OUTCOME_POSITIVE if is_positive else OUTCOME_NEGATIVE,
            "decision_threshold_any": round(float(threshold), 6),
            "confidence_band": band,
            "confidence_band_label": BAND_LABELS[band],
            **{f"calibrated_prob_{k}": round(float(v), 6) for k, v in probs.items()},
        },
        "triage": {
            "action": TRIAGE_ACTIONS[(outcome_key, band)],
            "urgency": "URGENT" if (is_positive and band == "HIGH") else "STANDARD",
        },
        "disclaimer": DISCLAIMER,
    }

    if report_image_path or heatmap_path:
        report["explainability"] = {
            "method": "Gradient-weighted Class Activation Mapping (Grad-CAM)",
            "image_path": report_image_path,
            "heatmap_path": heatmap_path,
            "note": (
                "Highlighted regions indicate areas with greatest influence on the "
                "screening decision. These are not confirmed anatomical findings."
            ),
        }

    return report


# ══════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════


def main():
    print("=" * 72)
    print("  ICH SCREENING — Improved 2.5D Inference")
    print("=" * 72)

    if not has_pydicom:
        print("ERROR: pydicom is not installed. Run: pip install pydicom")
        return

    if not DICOM_INPUT_DIR.exists():
        print(f"ERROR: DICOM input folder not found: {DICOM_INPUT_DIR}")
        print("  Create this folder and place .dcm files inside it.")
        return

    for path in [CALIB_PARAMS_PATH, NORM_STATS_PATH]:
        if not path.exists():
            print(f"ERROR: Required file missing: {path}")
            return

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"\n  Device            : {device}")

    with open(NORM_STATS_PATH, "r", encoding="utf-8") as f:
        norm = json.load(f)
    mean_9 = np.asarray(norm["mean_9ch"], dtype=np.float32)
    std_9 = np.asarray(norm["std_9ch"], dtype=np.float32)

    with open(CALIB_PARAMS_PATH, "r", encoding="utf-8") as f:
        calib_cfg = json.load(f)

    iso_models = None
    if ISOTONIC_MODELS_PATH.exists():
        with open(ISOTONIC_MODELS_PATH, "rb") as f:
            iso_models = pickle.load(f)

    threshold = (
        float(DECISION_THRESHOLD)
        if DECISION_THRESHOLD is not None
        else float(calib_cfg.get("threshold_at_spec90", 0.5))
    )

    print(f"  Backbone          : {BACKBONE}")
    print(f"  Input             : {IN_CHANNELS}ch @ {IMG_SIZE}x{IMG_SIZE}")
    print(f"  Calibration       : {calib_cfg.get('best_method', 'temperature')}")
    print(f"  Decision threshold: {threshold:.6f}")

    models, loaded_folds = load_models(device, fold_selection=FOLD_SELECTION)
    if not models:
        print("ERROR: No fold checkpoints could be loaded.")
        return
    print(f"  Fold models loaded: {len(models)} (folds: {loaded_folds})")
    gradcam_objects = [GradCAM(m) for m in models] if GENERATE_HEATMAPS else []

    adjacency_df = build_adjacency(DICOM_INPUT_DIR)
    if adjacency_df.empty:
        print(f"ERROR: No .dcm files found in {DICOM_INPUT_DIR}")
        return

    image_path_map = {
        Path(p).stem: Path(p)
        for p in adjacency_df["dcm_path"].tolist()
    }

    label_map: Dict[str, int] = {}
    if MANIFEST_PATH.exists():
        try:
            manifest = pd.read_csv(MANIFEST_PATH)
            if "image_id" in manifest.columns and "any" in manifest.columns:
                label_map = dict(zip(manifest["image_id"], manifest["any"]))
                print(f"  Manifest labels   : loaded {len(label_map)} rows")
        except Exception as exc:
            print(f"  ⚠ Manifest load skipped: {exc}")

    reports_dir = OUTPUT_DIR / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'─' * 72}")
    print(f"  Processing {len(adjacency_df)} DICOM slices")
    print(f"{'─' * 72}\n")

    slice_rows = []
    report_summary_rows = []
    patient_probs: Dict[str, List[float]] = {}

    for i, row in adjacency_df.iterrows():
        image_id = row["image_id"]
        patient_id = row["patient_id"]

        try:
            img_9ch = build_9ch_for_row(row, image_path_map, mean_9=mean_9, std_9=std_9)
        except Exception as exc:
            print(f"  [{i+1}/{len(adjacency_df)}] SKIP {image_id}: {exc}")
            continue

        tensor = torch.from_numpy(img_9ch).permute(2, 0, 1).unsqueeze(0).to(device)

        fold_logits = []
        fold_cams = []
        if GENERATE_HEATMAPS:
            for model, cam_obj in zip(models, gradcam_objects):
                logits, cam = cam_obj.generate(tensor, class_idx=0)
                fold_logits.append(logits)
                fold_cams.append(cam)
        else:
            with torch.no_grad():
                for model in models:
                    logits = model(tensor).squeeze(0).detach().cpu().numpy().astype(np.float32)
                    fold_logits.append(logits)

        mean_logits = np.mean(np.stack(fold_logits, axis=0), axis=0)
        raw_probs = sigmoid_np(mean_logits)
        cal_probs = apply_calibration(mean_logits, calib_cfg, iso_models)

        probs_dict = {name: float(cal_probs[j]) for j, name in enumerate(SUBTYPES)}

        # Save a per-slice visualization image (windowed center slice) for report artifacts.
        preview_path = reports_dir / f"{image_id}_preview.png"
        heatmap_path = reports_dir / f"{image_id}_gradcam.png"
        try:
            center_rgb = load_single_dicom_3ch(Path(row["dcm_path"]), size=IMG_SIZE)
            center_rgb_u8 = (np.clip(center_rgb, 0.0, 1.0) * 255.0).astype(np.uint8)
            cv2.imwrite(str(preview_path), cv2.cvtColor(center_rgb_u8, cv2.COLOR_RGB2BGR))
            if GENERATE_HEATMAPS:
                if fold_cams:
                    mean_cam = np.mean(np.stack(fold_cams, axis=0), axis=0)
                else:
                    mean_cam = np.zeros((IMG_SIZE, IMG_SIZE), dtype=np.float32)
                overlay_rgb = make_overlay(center_rgb_u8, mean_cam, alpha=0.45)
                cv2.imwrite(str(heatmap_path), cv2.cvtColor(overlay_rgb, cv2.COLOR_RGB2BGR))
            report_image_path = str(preview_path)
            report_heatmap_path = str(heatmap_path) if GENERATE_HEATMAPS else ""
        except Exception:
            report_image_path = ""
            report_heatmap_path = ""

        true_any = label_map.get(image_id)
        rep = build_slice_report(
            image_id=image_id,
            patient_id=patient_id,
            probs=probs_dict,
            calib_cfg=calib_cfg,
            threshold=threshold,
            loaded_folds=loaded_folds,
            report_image_path=report_image_path,
            heatmap_path=report_heatmap_path,
            true_label=int(true_any) if true_any is not None else None,
        )

        report_path = reports_dir / f"{image_id}_report.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(rep, f, separators=(",", ":"), ensure_ascii=True)

        slice_rows.append(
            {
                "image_id": image_id,
                "patient_id": patient_id,
                "true_any": int(true_any) if true_any is not None else "",
                "pred_any": int(probs_dict["any"] >= threshold),
                "cal_any": round(probs_dict["any"], 6),
                "raw_any": round(float(raw_probs[0]), 6),
                **{f"cal_{name}": round(float(probs_dict[name]), 6) for name in SUBTYPES[1:]},
                "confidence_band": rep["prediction"]["confidence_band"],
                "triage_action": rep["triage"]["action"],
                "urgency": rep["triage"]["urgency"],
            }
        )

        report_summary_rows.append(
            {
                "image_id": image_id,
                "true_label": int(true_any) if true_any is not None else "",
                "screening_outcome": rep["prediction"]["screening_outcome"],
                "raw_prob": round(float(raw_probs[0]), 6),
                "cal_prob": round(float(probs_dict["any"]), 6),
                "confidence_band": rep["prediction"]["confidence_band"],
                "triage_action": rep["triage"]["action"],
                "urgency": rep["triage"]["urgency"],
                "image_path": report_image_path,
                "heatmap_path": report_heatmap_path,
            }
        )

        patient_probs.setdefault(patient_id, []).append(probs_dict["any"])

        status = "[+] POS" if probs_dict["any"] >= threshold else "[-] NEG"
        print(
            f"  [{i+1}/{len(adjacency_df)}] {image_id}  →  {status}  "
            f"cal_any={probs_dict['any']:.4f}"
        )

    if not slice_rows:
        print("\nERROR: No slices were processed successfully.")
        return

    slice_df = pd.DataFrame(slice_rows)
    slice_csv_path = OUTPUT_DIR / "slice_predictions.csv"
    slice_df.to_csv(slice_csv_path, index=False)

    report_summary_df = pd.DataFrame(report_summary_rows)
    report_summary_csv_path = OUTPUT_DIR / "report_summary.csv"
    report_summary_df.to_csv(report_summary_csv_path, index=False)

    patient_rows = []
    for pid, vals in patient_probs.items():
        arr = np.asarray(vals, dtype=np.float32)
        agg_prob = patient_aggregate(arr, PATIENT_AGG_METHOD, PATIENT_TOPK)
        patient_rows.append(
            {
                "patient_id": pid,
                "n_slices": int(len(arr)),
                "agg_method": PATIENT_AGG_METHOD,
                "agg_any_probability": round(float(agg_prob), 6),
                "pred_any": int(agg_prob >= threshold),
            }
        )

    patient_df = pd.DataFrame(patient_rows)
    patient_csv_path = OUTPUT_DIR / "patient_predictions.csv"
    patient_df.to_csv(patient_csv_path, index=False)

    for cam_obj in gradcam_objects:
        cam_obj.remove()

    n_pos = int((slice_df["pred_any"] == 1).sum())
    n_total = len(slice_df)
    n_urgent = int((slice_df["urgency"] == "URGENT").sum())

    print(f"\n{'═' * 72}")
    print("  INFERENCE COMPLETE")
    print(f"{'═' * 72}")
    print(f"  Slices processed   : {n_total}")
    print(f"  Positive slices    : {n_pos}")
    print(f"  Urgent escalations : {n_urgent}")
    print(f"  Patients processed : {len(patient_df)}")
    print("\n  Outputs:")
    print(f"    JSON reports     : {reports_dir}")
    print(f"    Report images    : {reports_dir}")
    print(f"    Report summary   : {report_summary_csv_path}")
    print(f"    Slice CSV        : {slice_csv_path}")
    print(f"    Patient CSV      : {patient_csv_path}")
    print(f"{'═' * 72}")


if __name__ == "__main__":
    main()
