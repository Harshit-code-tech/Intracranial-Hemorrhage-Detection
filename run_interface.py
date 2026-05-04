"""Compatibility adapter for the web app inference API.

This module bridges the Flask app's expected interface to the improved
inference utilities in download_imp/run_inference.py.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import torch

try:
    from groq import Groq
except ImportError:
    Groq = None

try:
    import cloudinary
    import cloudinary.uploader
    import cloudinary.api
except ImportError:
    cloudinary = None

from download_imp import run_inference as core

ARCH = core.BACKBONE
IMG_SIZE = core.IMG_SIZE
SUBTYPES = core.SUBTYPES


def _parse_fold_selection(value: str | None) -> str | int:
    """Parse fold selection from env-style values.

    Accepted values: "ensemble", "best", or an integer fold id.
    """
    raw = (value or "ensemble").strip().lower()
    if raw in ("", "ensemble", "all"):
        return "ensemble"
    if raw == "best":
        # From B4 performance report per-fold any-AUC table.
        return 4
    if raw.isdigit():
        return int(raw)
    return "ensemble"


class _Compose:
    def __init__(self, transforms: list[Any]):
        self.transforms = transforms

    def __call__(self, x: np.ndarray) -> torch.Tensor:
        out = x
        for t in self.transforms:
            out = t(out)
        return out


class _ToPILImage:
    def __call__(self, x: np.ndarray) -> np.ndarray:
        # The web app pipeline does not require PIL specifically.
        return x


class _ToTensor:
    def __call__(self, x: np.ndarray) -> torch.Tensor:
        arr = np.asarray(x, dtype=np.float32)
        if arr.ndim != 3:
            raise ValueError("Expected HWC image array")
        # Convert HWC -> CHW
        return torch.from_numpy(np.transpose(arr, (2, 0, 1)))


class _Normalize:
    def __init__(self, mean: list[float], std: list[float]):
        self.mean = torch.tensor(mean, dtype=torch.float32).view(-1, 1, 1)
        self.std = torch.tensor(std, dtype=torch.float32).view(-1, 1, 1)

    def __call__(self, x: torch.Tensor) -> torch.Tensor:
        return (x - self.mean) / (self.std + 1e-7)


class T:
    Compose = _Compose
    ToPILImage = _ToPILImage
    ToTensor = _ToTensor
    Normalize = _Normalize


def build_model(_arch: str | None = None):
    return core.build_model()


def load_runtime_models(device: str, fold_selection: str | None = None):
    """Load one or many fold models for web inference."""
    parsed = _parse_fold_selection(fold_selection)
    models, loaded_folds = core.load_models(device, fold_selection=parsed)
    grad_cams = [GradCAM(m) for m in models]
    return models, grad_cams, loaded_folds


class GradCAM(core.GradCAM):
    def __init__(self, model, _arch: str | None = None):
        super().__init__(model)


def dicom_to_rgb(dcm_path: str, size: int = IMG_SIZE) -> np.ndarray:
    return core.load_single_dicom_3ch(Path(dcm_path), size=size)


def infer_single(
    img_rgb: np.ndarray,
    model,
    grad_cam: GradCAM,
    transform,
    device: str,
    temperature: float,
) -> dict[str, Any]:
    return infer_batch([img_rgb], model, grad_cam, transform, device, temperature)[0]


def infer_batch(
    images_rgb: list[np.ndarray],
    model,
    grad_cam: GradCAM,
    transform,
    device: str,
    temperature: float,
) -> list[dict[str, Any]]:
    # Build 3ch tensor from the app's transform pipeline, then tile to 9ch
    # because the trained model expects 2.5D channels.
    if device == "cuda":
        with torch.inference_mode():
            t3 = torch.stack([transform(img) for img in images_rgb], dim=0).to(device)
    else:
        t3 = torch.stack([transform(img) for img in images_rgb], dim=0).to(device)
    t9 = torch.cat([t3, t3, t3], dim=1)
    if isinstance(model, list) and isinstance(grad_cam, list):
        fold_logits = []
        fold_cams = []
        for _m, cam_obj in zip(model, grad_cam):
            logits_i, cam_i = cam_obj.generate(t9, class_idx=0)
            fold_logits.append(logits_i)
            fold_cams.append(cam_i)
        logits = np.mean(np.stack(fold_logits, axis=0), axis=0)
        cam = np.mean(np.stack(fold_cams, axis=0), axis=0)
    else:
        logits, cam = grad_cam.generate(t9, class_idx=0)

    raw_probs = core.sigmoid_np(logits)
    cal_probs = core.sigmoid_np(logits / max(float(temperature), 1e-6))

    results = []
    for idx in range(len(images_rgb)):
        results.append({
            "raw_logits": logits[idx],
            "raw_probs": raw_probs[idx],
            "cal_probs": cal_probs[idx],
            "raw_prob_any": float(raw_probs[idx][0]),
            "cal_prob_any": float(cal_probs[idx][0]),
            "cam": cam[idx],
        })
    return results


def generate_medical_summary(inference: dict[str, Any], calib_cfg: dict[str, Any], report: dict[str, Any]) -> str:
    """Generate a medical summary using Groq LLM API."""
    if not Groq:
        return "LLM integration not available (groq package not installed)."
        
    groq_api_key = os.environ.get("GROQ_API_KEY")
    if not groq_api_key:
        return "LLM integration not configured (Missing GROQ_API_KEY)."

    try:
        client = Groq(api_key=groq_api_key)
        
        prob = float(inference.get("cal_prob_any", 0.0))
        threshold = float(calib_cfg.get("threshold_at_spec90", 0.5))
        is_positive = prob >= threshold
        
        triage = report.get("triage", {})
        action = triage.get("action", "Unknown")
        urgency = triage.get("urgency", "Unknown")
        
        prompt = f"""
        You are an expert AI medical assistant analyzing a CT scan for Intracranial Hemorrhage.
        
        Scan Results:
        - Probability of Hemorrhage: {prob:.2%}
        - Decision Threshold: {threshold:.2%}
        - AI Assessment: {"Positive for Hemorrhage" if is_positive else "Negative for Hemorrhage"}
        - Urgency: {urgency}
        - Recommended Action: {action}
        
        Based on this data, write a concise, professional 3-sentence medical triage summary. 
        Focus strictly on the AI's findings. Do not hallucinate patient data.
        """
        
        model_name = os.environ.get("LLM_MODEL", "llama-3.1-8b-instant")
        response = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model=model_name,
            temperature=0.2,
            max_tokens=150,
        )
        
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"Failed to generate LLM summary: {str(e)}"


def build_report(
    image_id: str,
    inference: dict[str, Any],
    calib_cfg: dict[str, Any],
    reports_dir: Path,
    img_rgb: np.ndarray,
    true_label: int | None = None,
) -> dict[str, Any]:
    reports_dir.mkdir(parents=True, exist_ok=True)

    preview_path = reports_dir / f"{image_id}_preview.png"
    heatmap_path = reports_dir / f"{image_id}_gradcam.png"

    rgb_u8 = (np.clip(img_rgb, 0.0, 1.0) * 255.0).astype(np.uint8)
    cv2.imwrite(str(preview_path), cv2.cvtColor(rgb_u8, cv2.COLOR_RGB2BGR))

    overlay_rgb = core.make_overlay(rgb_u8, inference["cam"], alpha=0.45)
    cv2.imwrite(str(heatmap_path), cv2.cvtColor(overlay_rgb, cv2.COLOR_RGB2BGR))

    probs_dict = {
        name: float(inference["cal_probs"][idx])
        for idx, name in enumerate(SUBTYPES)
    }
    threshold = float(calib_cfg.get("threshold_at_spec90", 0.5))

    report = core.build_slice_report(
        image_id=image_id,
        patient_id="UNKNOWN",
        probs=probs_dict,
        calib_cfg=calib_cfg,
        threshold=threshold,
        loaded_folds=[0],
        report_image_path=str(preview_path),
        heatmap_path=str(heatmap_path),
        true_label=true_label,
    )

    report.setdefault("prediction", {})
    report["prediction"]["decision_threshold"] = report["prediction"].get("decision_threshold_any", threshold)
    report["prediction"]["raw_probability"] = round(float(inference["raw_prob_any"]), 6)
    report["prediction"]["calibrated_probability"] = round(float(inference["cal_prob_any"]), 6)

    report["llm_summary"] = generate_medical_summary(inference, calib_cfg, report)

    groq_api_key = os.environ.get("GROQ_API_KEY")
    if Groq and groq_api_key:
        report["llm_provider"] = "groq"
        report["llm_model"] = os.environ.get("LLM_MODEL", "llama-3.1-8b-instant")

    # Cloudinary Integration
    cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME")
    api_key = os.environ.get("CLOUDINARY_API_KEY")
    api_secret = os.environ.get("CLOUDINARY_API_SECRET")
    
    if cloudinary and cloud_name and api_key and api_secret:
        try:
            cloudinary.config(
                cloud_name=cloud_name,
                api_key=api_key,
                api_secret=api_secret,
                secure=True
            )
            
            # Upload preview
            preview_res = cloudinary.uploader.upload(str(preview_path), folder="ich_previews")
            report["cloudinary_preview_url"] = preview_res.get("secure_url")
            
            # Upload heatmap
            heatmap_res = cloudinary.uploader.upload(str(heatmap_path), folder="ich_heatmaps")
            report["cloudinary_heatmap_url"] = heatmap_res.get("secure_url")
            
            # Delete local copies to save space since we have them in the cloud
            preview_path.unlink(missing_ok=True)
            heatmap_path.unlink(missing_ok=True)
            
        except Exception as e:
            print(f"Cloudinary upload failed: {e}")

    return report
