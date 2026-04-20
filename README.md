# Major Project Documentation

## Project Structure and Setup

This repository is organized into clear functional sections:

- `app.py`: Flask web application for upload, inference, browsing reports, and logs
- `run_interface.py`: Compatibility adapter between the web app and model inference code
- `download_imp/`: Model artifacts and core inference implementation
- `templates/`: Jinja2 HTML templates for the web UI
- `static/`: CSS assets
- `logs/` and `uploads/`: Runtime folders created automatically

### Quick Start

1. Create and activate a Python virtual environment.
1. Install dependencies:

  ```bash
  pip install -r requirements.txt
  ```

1. Create environment file:

  ```bash
  cp .env.example .env
  ```

1. Run the web app:

  ```bash
  python app.py
  ```

1. Open:

  ```text
  http://127.0.0.1:7860
  ```

### Notes

- Model and calibration files are expected under `download_imp/`.
- Generated reports are written to `download_imp/outputs/reports/`.
- `.gitignore` excludes runtime/generated files to keep version control clean.

### Fold Selection

The web app supports configurable fold selection via `.env` / environment variable:

```bash
ICH_FOLD_SELECTION=ensemble
```

Supported values:

- `ensemble` (default): loads all available folds and averages predictions
- `best`: uses the best single fold from the performance report summary
- `0` to `4`: force a specific fold

Based on [B4_Performance_Report.md](B4_Performance_Report.md), per-fold any-AUC indicates fold `3` as the strongest single fold in that table.

### GitHub Artifact Policy

Model checkpoints and heavy binary artifacts are intentionally ignored for GitHub:

- `download_imp/*.pth`
- `download_imp/*.pkl`
- `download_imp/outputs/`

This keeps the repository lightweight and reproducible while allowing local/model-private inference.

### Publish Model To Hugging Face

1. Create a token with `Write` access: `https://huggingface.co/settings/tokens`
1. Set credentials in `.env` (or export in shell):

  ```bash
  HF_TOKEN=your_hf_token
  HF_REPO_ID=your-username/ich-b4-model
  ```

1. Upload model artifacts:

  ```bash
  python scripts/upload_to_huggingface.py --repo-id "$HF_REPO_ID" --private
  ```

Published model repository:

- [Hugging Face Model Repo](https://huggingface.co/HarshCode/eff_b4_brain)

### Host On GitHub Pages

1. Ensure your default branch is `main`.
1. Push this repository with `.github/workflows/pages.yml` and `docs/`.
1. In GitHub repository settings:
   1. Open `Settings -> Pages`
   1. Set source to `GitHub Actions`

The site will be published automatically after push.

## AI-Assisted CT-Based Intracranial Hemorrhage Detection with Explainability and Clinical Reporting

---

## 1. Introduction

**Intracranial hemorrhage (ICH)** is a life-threatening neurological condition caused by bleeding within the skull. It is one of the most critical forms of stroke and requires immediate medical attention. Delays in detection and intervention significantly increase the risk of mortality and long-term neurological damage.

**Computed Tomography (CT)** imaging is the primary diagnostic tool used in emergency settings for detecting intracranial hemorrhage due to its speed, availability, and high sensitivity to bleeding. However, accurate interpretation of CT scans requires experienced radiologists and must often be performed under time pressure, especially in emergency departments with high patient volumes.

Artificial intelligence has shown promise in assisting medical image interpretation. However, many AI-based solutions function as **black-box models** and focus solely on prediction accuracy, limiting their trustworthiness and clinical adoption. There is a need for an AI-assisted system that supports early screening while providing transparent and interpretable outputs to aid clinical decision-making.

---

## 2. Problem Statement

Intracranial hemorrhage detection from CT brain scans is a critical yet time-sensitive task in emergency medical care. Although CT imaging is effective for identifying hemorrhage, timely and accurate interpretation depends heavily on the availability of skilled radiologists. In high-pressure or resource-constrained environments, delays or misinterpretations can adversely affect patient outcomes.

Existing AI-based approaches for hemorrhage detection often emphasize binary predictions without sufficient explainability or clinical context. This lack of transparency makes it difficult for healthcare professionals to rely on AI outputs during screening and prioritization.

**The problem addressed in this project** is the absence of an explainable, AI-assisted screening system that can detect intracranial hemorrhage from CT scans while providing interpretable visual evidence and structured clinical explanations to support medical professionals.

> **Note:** The system is intended strictly as a screening and assistive tool, not as a diagnostic replacement for certified medical practitioners.

---

## 3. Objectives

### Primary Objectives

- To develop an AI-based system capable of detecting intracranial hemorrhage from CT brain images
- To classify CT scans into clearly defined categories: **hemorrhage present** or **absent**
- To assist emergency screening by prioritizing high-risk cases

### Secondary Objectives

- To integrate visual explainability techniques that highlight regions influencing the model's predictions
- To generate structured, human-readable clinical reports summarizing findings
- To evaluate model reliability with emphasis on **false-negative reduction**
- To ensure ethical deployment as a decision-support system rather than a diagnostic authority

---

## 4. Scope of the Project

### Included

✅ CT brain image preprocessing and normalization  
✅ Binary classification of intracranial hemorrhage presence  
✅ Explainability using activation-based heatmaps  
✅ Confidence-aware screening and structured reporting

### Excluded

❌ Stroke subtype classification beyond hemorrhage detection  
❌ Treatment recommendation or diagnosis  
❌ Real-time clinical deployment  
❌ Integration with hospital information systems

---

## 5. Dataset Description

The project utilizes publicly available CT brain imaging datasets from Kaggle, such as:

**Primary datasets:**
- RSNA Intracranial Hemorrhage Detection Dataset
- CT Brain Hemorrhage Dataset

These datasets contain labeled CT scan images indicating the presence or absence of intracranial hemorrhage, with some datasets also providing hemorrhage subtype annotations.

Using public datasets ensures reproducibility, ethical compliance, and feasibility within academic constraints.

---

## 6. Methodology

### 6.1 Data Preprocessing

Preprocessing is essential to improve image quality and model performance:

- Conversion of DICOM images to standardized formats where required
- Image resizing to fixed resolution
- **CT windowing (brain window)** to enhance hemorrhage visibility
- Intensity normalization
- Noise reduction and artifact handling
- Data augmentation to improve generalization and mitigate class imbalance

### 6.2 Model Development

A **convolutional neural network (CNN)** is implemented using transfer learning.

- Pretrained architectures such as **ResNet** or **EfficientNet** are adapted for CT image analysis
- The final classification layer is modified for **binary output**:
  - **Class 0**: No intracranial hemorrhage
  - **Class 1**: Intracranial hemorrhage present
- The model is trained using supervised learning with appropriate loss functions

### 6.3 Evaluation Metrics

Model performance is evaluated using clinically relevant metrics:

- **Sensitivity (Recall)** – prioritized to reduce missed hemorrhage cases
- Specificity
- Precision
- Confusion matrix analysis
- Receiver Operating Characteristic (ROC) curve

> **Note:** Accuracy alone is not treated as a sufficient indicator of clinical usefulness.

---

## 7. Explainability Module

To ensure transparency and trustworthiness:

- **Gradient-weighted Class Activation Mapping (Grad-CAM)** will be applied
- Heatmaps are generated to visualize regions contributing to predictions
- Highlighted areas are analyzed in relation to known hemorrhage patterns in CT images

This module allows clinicians to visually verify AI decisions rather than relying solely on binary outputs.

### 7.1 Explainability Quality Assurance

To ensure the reliability of explainability outputs:

- **Sanity checks** will be implemented (e.g., occlusion/perturbation tests) to verify Grad-CAM is not highlighting irrelevant borders, text markers, or artifacts
- Failure cases where heatmaps are misleading will be documented
- For a sample of True Positives, False Negatives, and False Positives, Grad-CAM overlays will include brief qualitative notes comparing:
  - What the model highlights
  - What a clinician would expect to see
- This ensures visual evidence aligns with clinical reasoning

---

## 8. Confidence-Aware Screening

Instead of a simple binary output, the system incorporates prediction confidence:

- **High-confidence hemorrhage detection** → urgent attention
- **Low-confidence predictions** → manual review recommendation

This approach reflects real-world screening workflows and reduces over-reliance on automated decisions.

### 8.1 Confidence Calibration

To ensure clinicians can trust the confidence scores:

- **Calibration techniques** will be applied (e.g., temperature scaling or isotonic regression)
- Both **raw probability** and **calibrated confidence** will be reported
- Expected Calibration Error (ECE) will be evaluated
- Three confidence bands will be defined:
  - **High confidence**: urgent attention required
  - **Medium confidence**: standard review
  - **Low confidence**: manual review recommended
- Error rates will be analyzed across each confidence band to support triage decisions

---

## 9. Clinical Report Generation

A structured report generation module converts model outputs into human-readable explanations. Each report includes:

- Screening outcome summary
- Prediction confidence
- Visual explainability reference
- Clinical interpretation phrased as decision support

The report avoids diagnostic claims and emphasizes assistive screening.

### 9.1 Report Schema and Specifications

To prevent diagnostic claims and ensure consistency:

- A **fixed schema** will be defined with specific fields and allowed phrases
- Reports will be locked down with rules to ensure they never make diagnostic claims
- Each report field will have:
  - **Screening outcome**: "Hemorrhage detected" or "No hemorrhage detected" (not "diagnosed")
  - **Confidence level**: Numeric probability + calibrated confidence band
  - **Visual evidence**: Reference to Grad-CAM heatmap image
  - **Recommended action**: "Urgent radiologist review recommended" or "Standard review workflow"
  - **System disclaimer**: Clear statement that this is a screening tool, not a diagnostic device
- Standardized phrasing ensures clinical safety and legal compliance

---

## 10. System Architecture Overview

```
1. CT Brain Image Input
   ↓
2. Image Preprocessing Module
   ↓
3. CNN-Based Hemorrhage Detection Model
   ↓
4. Explainability Module (Grad-CAM)
   ↓
5. Confidence Assessment
   ↓
6. Structured Clinical Report Generator
   ↓
7. Output for Medical Review
```

---

## 11. Technology Stack

### Programming Language
- **Python**

### Libraries and Frameworks
- **TensorFlow** or **PyTorch** (Deep Learning)
- **OpenCV** (Image Processing)
- **NumPy**, **Pandas** (Data Handling)
- **Matplotlib** (Visualization)

### Development Platform
- **Kaggle Notebooks**
- Jupyter Notebook

---

## 12. Feasibility and Resources

The project is **fully feasible** using free computational resources:

- Kaggle provides free GPU access suitable for CNN training
- Transfer learning minimizes training time
- All tools used are open-source
- No specialized hardware is required locally

**Kaggle provides:**
- Free GPU access (time-limited but sufficient)
- Adequate RAM and storage for medical imaging datasets
- Stable notebook environment for training and evaluation

**Constraints:**
- Training time per session is limited
- Efficient model selection and batch sizing are necessary

These constraints align well with transfer learning-based approaches.

---

## 13. Ethical Considerations

- The system is designed strictly as a **screening and decision-support tool**
- It does not provide diagnosis or treatment recommendations
- Limitations and potential biases are explicitly documented
- Human oversight is required for all clinical decisions
- Dataset usage complies with public research licenses
- Model limitations and potential biases will be documented

---

## 14. Assumptions and Risks

### Assumptions
- Public datasets are representative of real-world CT brain images
- Hemorrhage labels are clinically reliable and accurately annotated

### Potential Risks
- Class imbalance may bias predictions toward non-hemorrhage cases
- Overfitting due to dataset limitations
- Misinterpretation of AI outputs by non-expert users
- False negatives could delay critical interventions

### Clinical Risk Evaluation Protocol

To address these risks systematically:

- **Target operating points** will be defined (e.g., "maximize sensitivity subject to acceptable specificity")
- **False negative analysis**: Pre-commit to reviewing all false negative cases with detailed inspection:
  - Inspect FN scans with Grad-CAM overlays
  - Document typical failure patterns (e.g., small bleeds, beam-hardening artifacts, post-operative changes)
  - Use findings to refine preprocessing or model architecture
- Each component of the system architecture will be evaluated using the framework:
  - **Inputs** → **Outputs** → **Metrics** → **Failure Modes** → **Mitigations**
- This structured approach ensures risks are systematically addressed before deployment

Mitigation strategies will be implemented during development and documented in evaluation.

---

## 15. Proposed Experiments and Ablation Studies

To validate design decisions and optimize performance, the following experiments will be conducted:

### 15.1 Preprocessing Ablations

**Goal**: Justify the preprocessing pipeline choices

**Experiments**:
- Brain windowing: ON vs OFF
- Different normalization strategies (min-max, z-score, percentile-based)
- Data augmentation: ON vs OFF

**Evaluation metrics**:
- Sensitivity (primary)
- ROC-AUC
- Expected Calibration Error (ECE)

**Outcome**: Select preprocessing configuration that maximizes sensitivity while maintaining calibration

### 15.2 Model Architecture Comparison

**Goal**: Choose the optimal backbone architecture

**Experiments**:
- ResNet-50 vs EfficientNet-B0
- Same train/validation split for fair comparison
- Evaluate with fixed hyperparameters

**Selection criteria**:
- Sensitivity at fixed specificity (e.g., 95% specificity)
- Inference time (important for screening/prioritization)
- Model size and computational requirements

**Outcome**: Select architecture based on clinical utility and deployment feasibility

### 15.3 Confidence-Aware Triage Study

**Goal**: Validate the three-band confidence system

**Experiments**:
- Define thresholds for high/medium/low confidence bands
- Analyze case distribution across bands
- Compute error rates (sensitivity, specificity, FN rate) per band

**Metrics**:
- Percentage of cases in each band
- False negative rate by confidence level
- Positive predictive value by confidence level

**Outcome**: Demonstrate that high-confidence predictions are more reliable and support triage workflow

### 15.4 Explainability Evaluation

**Goal**: Validate that Grad-CAM provides clinically useful visualizations

**Experiments**:
- Generate Grad-CAM overlays for sample cases:
  - True Positives (correct hemorrhage detection)
  - False Negatives (missed hemorrhages)
  - False Positives (false alarms)
- Qualitative analysis comparing:
  - What the model highlights
  - What clinicians would expect to see
- Document cases where heatmaps are misleading or incorrect

**Outcome**: Ensure explainability module provides trustworthy visual evidence

### 15.5 Calibration Study

**Goal**: Improve confidence reliability

**Experiments**:
- Train baseline model and measure calibration (ECE, reliability diagram)
- Apply temperature scaling and isotonic regression
- Compare raw probabilities vs calibrated confidence

**Outcome**: Deploy calibrated confidence scores that clinicians can trust

---

## 16. Expected Outcomes and Deliverables

### 16.1 Expected Outcomes

**Model Performance**:
- A trained CNN model achieving high sensitivity (target: >95%) for intracranial hemorrhage detection
- Specificity maintained at clinically acceptable levels (target: >85%)
- Calibrated confidence scores with low Expected Calibration Error (ECE < 0.05)
- Comprehensive performance evaluation across all metrics (sensitivity, specificity, precision, F1-score, ROC-AUC)

**Explainability**:
- Reliable Grad-CAM visualizations highlighting hemorrhage regions
- Validated explainability outputs through sanity checks
- Documented failure modes and typical misclassification patterns

**Clinical Utility**:
- Confidence-based triage system validated across three bands (high/medium/low)
- Demonstrated reduction in false negatives through systematic review
- Structured reports that support clinical decision-making without making diagnostic claims

**Research Insights**:
- Evidence-based justification for preprocessing choices through ablation studies
- Model architecture comparison showing optimal choice for screening scenarios
- Understanding of model limitations and failure patterns

### 16.2 Project Deliverables

**1. Trained Model**
- Final CNN model weights saved in standard format (`.h5` or `.pth`)
- Model configuration file documenting architecture and hyperparameters
- Training history and learning curves

**2. Preprocessing Pipeline**
- Complete data preprocessing module for CT scan normalization
- DICOM handling utilities where applicable
- Data augmentation scripts

**3. Explainability Module**
- Grad-CAM implementation integrated with the model
- Visualization generation scripts
- Sanity check utilities for validation

**4. Confidence Calibration Module**
- Calibration implementation (temperature scaling/isotonic regression)
- Scripts for computing calibrated confidence scores
- Calibration evaluation metrics

**5. Clinical Report Generator**
- Structured report generation system with fixed schema
- Template-based reporting following clinical safety guidelines
- Sample reports demonstrating different scenarios

**6. Evaluation Framework**
- Complete evaluation scripts for all metrics
- Confusion matrix and ROC curve generation
- Ablation study implementation and results

**7. Documentation**
- Project report (this README and extended documentation)
- Code documentation and inline comments
- User guide for running the system
- Dataset description and preprocessing details

**8. Jupyter Notebooks**
- Data exploration and preprocessing notebook
- Model training and evaluation notebook
- Explainability demonstration notebook
- Report generation examples notebook

**9. Results and Analysis**
- Performance metrics across all experiments
- Ablation study results with visualizations
- Failure case analysis with Grad-CAM overlays
- Calibration plots and reliability diagrams

**10. Presentation Materials**
- Project presentation slides
- Demo video (optional)
- Key visualizations and results summary

---

## 17. Limitations and Future Work

### Current Limitations

- **Dataset Scope**: Limited to publicly available datasets which may not fully represent all clinical scenarios
- **Binary Classification**: Does not classify hemorrhage subtypes (epidural, subdural, subarachnoid, etc.)
- **Single Slice Analysis**: May not utilize full 3D volumetric information from CT scans
- **No Real-Time Deployment**: System is designed for research and demonstration, not clinical deployment
- **Limited Clinical Validation**: Evaluation based on dataset labels, not verified by multiple radiologists

### Future Enhancements

**Clinical Extensions**:
- Multi-class classification for hemorrhage subtypes
- Integration of volumetric (3D) analysis using 3D CNNs
- Temporal analysis for follow-up scan comparison
- Integration with radiology workflow systems (PACS)

**Technical Improvements**:
- Ensemble models combining multiple architectures
- Uncertainty quantification using Bayesian deep learning
- Active learning for continuous model improvement
- Real-time inference optimization for clinical deployment

**Validation and Deployment**:
- Prospective clinical validation with radiologist verification
- Multi-center evaluation for generalizability
- Regulatory compliance pathway (FDA/CE marking)
- Production-ready deployment with monitoring

**Enhanced Explainability**:
- Multiple explainability methods comparison (Grad-CAM++, SHAP, attention mechanisms)
- Interactive visualization tools for clinicians
- Textual explanation generation describing detected features

---

## 18. Conclusion

This project aims to develop an AI-assisted screening system for intracranial hemorrhage detection that prioritizes **clinical utility**, **explainability**, and **ethical deployment**. By combining deep learning with transparency mechanisms, confidence calibration, and structured reporting, the system is designed to support—not replace—clinical decision-making.

The systematic approach to risk evaluation, comprehensive ablation studies, and focus on false negative reduction ensure that the system addresses real clinical needs while acknowledging its limitations as a screening tool.

Through this project, we demonstrate that responsible AI in healthcare requires not just prediction accuracy, but also interpretability, calibration, careful validation, and explicit acknowledgment of system boundaries.

---

