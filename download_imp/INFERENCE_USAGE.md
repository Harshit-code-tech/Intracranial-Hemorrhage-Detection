# Improved Model Inference — Usage Guide (`download_imp`)

## What this runner does

`run_inference.py` runs the **latest improved model** trained from the notebooks in `improvement/`:

- EfficientNet-B4 backbone (`tf_efficientnet_b4`)
- 2.5D input (prev + center + next slices → 9 channels)
- 6 outputs (`any` + 5 hemorrhage subtypes)
- 5-fold ensemble (`best_model_fold0..4.pth`)
- Saved calibration (`isotonic`/`temperature`) from `calibration_params.json`

Outputs:

- Per-slice JSON report (`outputs/reports/*.json`)
- Slice-level CSV (`outputs/slice_predictions.csv`)
- Patient-level CSV (`outputs/patient_predictions.csv`)

---

## Required files (already in `download_imp/`)

- `best_model_fold0.pth`
- `best_model_fold1.pth`
- `best_model_fold2.pth`
- `best_model_fold3.pth`
- `best_model_fold4.pth`
- `calibration_params.json`
- `isotonic_models.pkl`
- `normalization_stats.json`
- `manifest.csv` (optional at inference time; used only for `true_any` if IDs match)

---

## Python package requirements

```bash
pip install -r requirements.txt
```

Notes:

- `timm` is required for `tf_efficientnet_b4` model construction.
- `scikit-learn` is needed to deserialize and use `isotonic_models.pkl`.

---

## Folder setup

Create this folder and place DICOM files there:

```text
download_imp/
├── run_inference.py
├── run_interface.py
├── best_model_fold0.pth
├── best_model_fold1.pth
├── best_model_fold2.pth
├── best_model_fold3.pth
├── best_model_fold4.pth
├── calibration_params.json
├── isotonic_models.pkl
├── normalization_stats.json
├── manifest.csv
└── dicom_inputs/
    ├── ID_xxx1.dcm
    ├── ID_xxx2.dcm
    └── ...
```

---

## Run commands

From workspace root:

```bash
cd download_imp
python run_inference.py
```

or (same thing):

```bash
python run_interface.py
```

---

## Important behavior

- No CLI arguments; all settings are at top of `run_inference.py` (`CONFIG` section).
- `FOLD_SELECTION` controls checkpoint selection:
    - `"ensemble"` = use all available folds and average logits
    - `0..4` = use one specific fold only
- If `best_method` is `isotonic`, the runner uses `isotonic_models.pkl`.
- Missing prev/next slice in a series is handled exactly like training cache logic: neighbor falls back to center slice.
- Decision threshold defaults to `threshold_at_spec90` from `calibration_params.json` unless overridden in config.

---

## Recommended production checklist

1. Keep all fold checkpoints and calibration files in the same `download_imp/` directory.
2. Verify DICOMs are non-contrast head CT slices before inference.
3. Run once on a small sample and review `slice_predictions.csv` and JSON reports.
4. Have radiologist review all flagged and uncertain cases.
