# B4 Model Performance Report

## Project Title
AI-Assisted Intracranial Hemorrhage Screening from Non-Contrast CT using EfficientNet-B4 with 2.5D Context and Multi-Label Learning

## Document Metadata
- Student Project: Major Project Submission
- Model Focus: EfficientNet-B4
- Pipeline Type: 2.5D (9-channel) multi-label screening
- Evaluation Style: 5-fold OOF slice-level metrics with triage analysis
- Report Length Target: 15-20 pages when formatted in standard academic layout

---

## Executive Summary
This report presents the full technical and performance assessment of the EfficientNet-B4 pipeline developed for intracranial hemorrhage (ICH) screening from CT images. The system is designed as a screening and triage aid, not a standalone diagnostic engine. The B4 pathway extends baseline design by incorporating richer spatial context through 2.5D input, multi-label outputs for hemorrhage subtypes, calibration-aware decisioning, and triage-centric reporting.

The model was evaluated in out-of-fold (OOF) settings with subgroup reporting and triage distribution analysis. The any-class slice-level AUC is 0.95052, with strong subtype performance and high intraventricular discrimination (AUC 0.97196). Per-fold uncertainty is low (mean any-AUC 0.95102 plus/minus 0.00230), indicating stable training behavior under GroupKFold validation.

Although a historical baseline showed slightly higher any-only AUC in one earlier protocol, the B4 design adds significant practical value through:
- Multi-label subtype support
- Better clinical interpretability
- Triage-band stratification
- Fold-robust uncertainty estimation
- Scalable architecture for future deployment upgrades

This report includes methods, evaluation metrics, interpretation, risks, limitations, and deployment guidance.

---

## 1. Introduction
Intracranial hemorrhage is a time-sensitive radiological emergency in which delayed detection can lead to severe disability or death. In high-throughput emergency environments, AI-supported prioritization can reduce time-to-review for suspicious cases. However, purely accuracy-focused solutions are insufficient for clinical workflows unless they also provide reliability indicators and structured outputs that support radiologist decisions.

The B4 model in this project was developed to improve operational usefulness, not just headline AUC. The key premise is that hemorrhage patterns often span adjacent slices and multiple subtype manifestations; therefore, a context-aware and multi-label approach is better aligned with practical reading conditions than isolated binary single-slice scoring.

---

## 2. Problem Statement
The project addresses the following practical gaps in ICH AI screening:
- Binary-only formulations miss subtype granularity required for useful case communication.
- Single-slice input ignores local anatomical continuity.
- Single-split validation can overstate confidence in model performance.
- Output without triage semantics has limited workflow impact.

Goal for B4 track:
- Build a robust, fold-validated, context-aware, multi-label screening model with triage-oriented outputs and explicit safety framing.

---

## 3. Model and Data Pipeline

### 3.1 Backbone
- EfficientNet-B4
- Approximate parameter scale: 19M

### 3.2 Input Design
- 2.5D representation
- 9-channel tensor constructed from neighboring slices and CT windows

### 3.3 Output Targets
The model predicts six labels:
- any
- epidural
- intraparenchymal
- intraventricular
- subarachnoid
- subdural

### 3.4 Validation Strategy
- 5-fold GroupKFold OOF evaluation
- Per-fold AUC tracking for uncertainty estimation

### 3.5 Calibration and Triage
- Isotonic calibration used in improved path
- Triage bands include HIGH, MEDIUM, UNCERTAIN, LOW

---

## 4. Experimental Configuration
The B4 pipeline was executed under the following report-aligned configuration:
- Model family: EfficientNet-B4
- Input formulation: 2.5D, 9 channels
- Task formulation: Multi-label classification (6 classes)
- Validation protocol: 5-fold OOF
- Reporting scope: Slice-level metrics, per-fold uncertainty, triage distributions, representative case summaries

---

## 5. Quantitative Results

### 5.1 Slice-Level OOF Metrics

| Subtype | AUC | Sens | Spec | F1 | Threshold | Sens@Spec90 | Spec@Sens95 |
|---|---:|---:|---:|---:|---:|---:|---:|
| any | 0.95052 | 0.8556 | 0.9038 | 0.7041 | 0.1622 | 0.8556 | 0.7447 |
| epidural | 0.90027 | 0.8471 | 0.8234 | 0.0400 | 0.0050 | 0.7554 | 0.5025 |
| intraparenchymal | 0.95888 | 0.8868 | 0.8996 | 0.4548 | 0.0556 | 0.8862 | 0.7695 |
| intraventricular | 0.97196 | 0.9371 | 0.9055 | 0.4174 | 0.0436 | 0.9371 | 0.8833 |
| subarachnoid | 0.92206 | 0.8744 | 0.8146 | 0.3112 | 0.0548 | 0.7561 | 0.6988 |
| subdural | 0.93016 | 0.8507 | 0.8584 | 0.4285 | 0.0685 | 0.7860 | 0.6867 |

Observations:
- Best subtype discrimination is intraventricular (AUC 0.97196).
- any-class AUC is strong for screening use.
- Epidural shows expected challenges due to class rarity and imbalance effects.

### 5.2 Per-Fold Uncertainty (Any Label)
- Fold 0: 0.95199
- Fold 1: 0.95106
- Fold 2: 0.95228
- Fold 3: 0.95315
- Fold 4: 0.94662

Aggregate:
- Mean plus/minus Std: 0.95102 plus/minus 0.00230
- Approximate 95 percent CI: [0.94901, 0.95303]

Interpretation:
- Low fold variance indicates robust generalization behavior and reproducibility within the chosen split methodology.

---

## 6. Confusion Matrix Evidence (OOF)
At threshold approximately 0.162 for any-class decisioning, the OOF confusion matrix from provided figure is:
- True Negative: 58,284
- False Positive: 6,203
- False Negative: 1,558
- True Positive: 9,235

Derived notes:
- High true-negative volume indicates broad filtering ability.
- False negatives remain non-zero, therefore mandatory human oversight is required.
- False positives are operationally less harmful than false negatives in screening context, but they increase workload.

---

## 7. Triage Distribution Analysis
Reported 4-band triage distribution:
- HIGH: n=6,707, positives=6,023, prevalence=0.898
- UNCERTAIN: n=816, positives=408, prevalence=0.500
- MEDIUM: n=3,941, positives=1,887, prevalence=0.479
- LOW: n=63,816, positives=2,475, prevalence=0.039

Interpretation:
- HIGH band demonstrates strong concentration of positive findings.
- UNCERTAIN band isolates clinically ambiguous instances and is suitable for prioritised manual attention.
- LOW band maintains low prevalence, supporting triage de-prioritization while retaining safety caveat.

---

## 8. Representative Clinical Case Summaries
The provided high-triage case summaries demonstrate that the model often identifies plausible multi-subtype combinations in positive studies. Example patterns include combinations of subdural with subarachnoid or intraparenchymal signals at high confidence.

Operational takeaway:
- Multi-label output is useful for communication and review focus.
- The model remains a screening aid and should never be used as sole diagnostic authority.

---

## 9. B4 Versus Baseline Context
From supplied comparison text:
- Architecture: B0 baseline vs B4 improved
- Input: 2D 3ch vs 2.5D 9ch
- Formulation: Binary vs Multi-label
- Validation: Single split vs 5-fold GroupKFold

Reported delta (any AUC) in that specific summary:
- Baseline any AUC: 0.95800
- Improved B4 any AUC: 0.95052
- Delta: -0.00748

Important interpretation note:
This delta should not be read as a simple regression claim without protocol harmonization. The improved model is evaluated under a more demanding setup (multi-label plus fold-based uncertainty estimation), and it contributes features not captured by any-only AUC:
- subtype intelligence
- triage-aware utility
- improved methodological rigor in validation

---

## 10. Figure-Based Analysis (From Supplied Images)

### Figure Group A: ROC Curves Per Subtype (OOF)
Insights:
- Strong curve separation from random baseline for all subtypes.
- Intraventricular and intraparenchymal are strongest.
- Epidural remains the most difficult class.

### Figure Group B: Patient-Level ROC Aggregation Comparison
Displayed AUCs are close:
- max: 0.9525
- mean: 0.9471
- noisy_or: 0.9515
- topk_mean: 0.9514

Interpretation:
- Aggregation strategy selection should be aligned with clinical objective (sensitivity floor versus workload constraints).

### Figure Group C: OOF Confusion Matrix
Confirms operational trade-off at selected threshold:
- substantial true-positive recovery
- acceptable but non-trivial false-positive volume

---

## 11. Statistical Reliability and Uncertainty
B4 fold spread is narrow, which improves confidence in performance stability:
- Std ~0.00230 for any-AUC across folds
- CI indicates strong central tendency around 0.951

For final submission, this fold uncertainty evidence is stronger than single-point metrics because it quantifies repeatability.

---

## 12. Clinical Utility Assessment
Strengths of B4 track for screening:
- High any-class discriminatory power
- Multi-label subtype context for communication
- Triage stratification to support operational prioritization
- Suitable for front-end assistance in high-volume settings

Risk points:
- False negatives cannot be eliminated
- Class imbalance (especially epidural) can affect threshold behavior
- External site drift remains possible

Recommendation:
- Use as assistive pre-read or queue-prioritization tool under radiologist supervision.

---

## 13. Error Modes and Risk Discussion
Likely error contributors:
- subtle small bleeds near skull base
- beam-hardening and motion artifacts
- mixed-density presentations
- overlap between non-hemorrhagic hyperdensities and hemorrhage-like patterns

Mitigation path:
- targeted hard-negative mining
- subtype-aware threshold optimization
- study-level fusion with metadata
- human review escalation rules for uncertain band

---

## 14. Deployment Position
Proposed deployment stance for B4:
- Assistive triage only
- Do not auto-clear without human review policy
- Preserve model versioning and threshold logs
- Track drift through periodic calibration audits

---

## 15. Limitations
- External validation across institutions is pending.
- Domain shift across scanner vendors is not fully characterized.
- This report relies on provided OOF and figure summaries; raw per-case audit trails should be archived for final viva.

---

## 16. Ethical and Regulatory Considerations
- Screening tool only, not diagnostic device.
- Human-in-the-loop is mandatory.
- Explanations are supportive visual cues, not confirmed lesion boundaries.
- Clinical decisions must remain with licensed professionals.

---

## 17. Future Work
1. Add explicit subtype prevalence-aware loss weighting refinements.
2. Integrate stronger patient-level calibration and threshold governance.
3. Compare B4 against B3/B5 under unified evaluation scripts.
4. Add prospective-like temporal split testing.
5. Add cost-sensitive thresholding to tune false-positive burden.

---

## 18. Conclusion
The B4 model demonstrates a mature, clinically aligned screening pipeline with robust OOF performance and strong subtype-level discrimination. While any-only AUC comparisons with baseline require protocol-matched interpretation, the B4 system provides broader practical value through multi-label outputs, uncertainty-aware fold validation, and triage-oriented reporting behavior.

This makes B4 a defensible advanced model track for project submission, provided the final defense clearly states that the system is an assistive triage tool and not an autonomous diagnosis engine.

---

## 19. Appendix A: Key Numbers (Quick Reference)
- Any AUC: 0.95052
- Any Sensitivity: 0.8556
- Any Specificity: 0.9038
- Fold Mean AUC: 0.95102
- Fold Std: 0.00230
- OOF confusion (thr approx 0.162): TN 58,284, FP 6,203, FN 1,558, TP 9,235

---

## 20. Appendix B: Figures to Embed in Final PDF
Insert the provided B4 visuals in this order in your final report export:
1. ROC Curves Per Subtype (OOF)
2. Patient-Level ROC Aggregation Comparison
3. OOF Confusion Matrix (threshold approx 0.162)

Suggested captions:
- Figure B4-1: Subtype ROC performance under OOF validation.
- Figure B4-2: Patient-level aggregation strategy comparison.
- Figure B4-3: Any-class confusion matrix at selected screening threshold.
