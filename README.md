# Intracranial Hemorrhage Detection

AI-assisted screening system for intracranial hemorrhage (ICH) from head CT (DICOM) images.

This project provides a Flask web interface for:

- uploading single or batch DICOM scans,
- running model inference,
- viewing Grad-CAM visualizations,
- browsing past reports and logs,
- reviewing calibration and evaluation summaries.

## Project Overview

Intracranial hemorrhage is a time-critical emergency finding in neuroimaging. This repository focuses on a practical screening workflow with explainability and structured report output.

The system is built for decision support and triage assistance, not standalone diagnosis.

## Model and Artifacts

Model weights and related inference artifacts are hosted on Hugging Face:

- [Hugging Face Model Repository](https://huggingface.co/HarshCode/eff_b4_brain)

## Detailed Performance Report

Detailed performance and B4-specific analysis are documented separately in:

- [B4_Performance_Report.md](B4_Performance_Report.md)

## GitHub Pages Setup

For step-by-step GitHub Pages setup (project site and username.github.io site), see:

- [GITHUB_PAGES_DOCUMENT.md](GITHUB_PAGES_DOCUMENT.md)

## Repository Structure

- `app.py`: Flask application entry point
- `run_interface.py`: adapter layer between app and inference implementation
- `download_imp/`: inference code and local artifact layout
- `templates/`: HTML templates (Jinja2)
- `static/`: styles and static assets
- `docs/`: GitHub Pages content

## Requirements

- Python 3.10+ (3.12 works)
- pip
- virtual environment (recommended)

Install dependencies:

```bash
pip install -r requirements.txt
```

## Environment Setup

Create local environment file from template:

```bash
cp .env.example .env
```

Important variables in `.env`:

- `ICH_APP_DEBUG`: run Flask in debug mode (`1` or `0`)
- `ICH_APP_PORT`: app port (default `7860`)
- `ICH_SECRET_KEY`: Flask secret key
- `ICH_MAX_UPLOAD_MB`: max upload size in MB
- `ICH_FOLD_SELECTION`: `ensemble`, `best`, or fold id (`0` to `4`)
- `ICH_LOCAL_MODE`: enables local directory scanning mode
- `ICH_LOG_LEVEL`: `DEBUG`, `INFO`, `WARNING`, `ERROR`

## Run the Application

```bash
python app.py
```

Open in browser:

```text
http://127.0.0.1:7860
```

## Basic Usage

1. Go to the upload page.
2. Upload one `.dcm`, multiple `.dcm` files, or batch input.
3. Wait for inference and report generation.
4. Review:
   - screening outcome,
   - calibrated probability,
   - confidence band,
   - triage action,
   - Grad-CAM overlay.
5. Use Reports / Logs / Evaluation pages for history and analysis.

## Notes

- Keep heavy model binaries out of GitHub (managed via `.gitignore`).
- Generated report outputs are created during runtime.
- If required artifacts are missing locally, fetch them from the Hugging Face repository linked above.

## Disclaimer

This system is an AI-assisted screening and decision-support tool.
It does **not** provide a medical diagnosis and must be used with qualified clinical review.
