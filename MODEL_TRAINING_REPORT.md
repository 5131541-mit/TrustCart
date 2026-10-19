# TrustCart — Final MVP AI Training Report

## Models trained

### 1. Product recognition model
- File: `models/product_model.joblib`
- Method: feature extraction + StandardScaler + Logistic Regression
- Classes: 9 TrustCart products
- Training samples after augmentation: 714
- Augmented validation accuracy: 100%
- Cross-render held-out test: 60% (3/5)

### 2. Custom camera product detector
- File: `models/custom_product_detector.joblib`
- Method: HOG + LinearSVC with a background class, plus trained-product template proposals for CPU-friendly localization
- Classes: 9 products + background
- Training samples: 6,112
- Validation accuracy: 100% on the generated validation split
- Training source: supplied TrustCart seed images + programmatically generated synthetic shelf scenes

## Important limitation
The supplied ZIP did not contain real, bounding-box-labelled shelf/camera footage. Therefore the camera detector was trained on a synthetic detection set generated from the supplied product images. This makes the detector a **trained MVP component**, not evidence of production-grade camera accuracy.

For a real store deployment, add hundreds/thousands of real shelf/camera frames with product bounding boxes and retrain. Keep a separate test set from different stores, lighting, distances and occlusion conditions.

## Full TrustCart AI/event pipeline

Camera Video → Custom Product Detector → Product Tracking → Pick Event → Scan Database → AI Mismatch Engine → Staff Alert → Resolution → Analytics

The mismatch engine itself is deterministic business logic, not a trainable model. It combines scan, vision and optional shelf-weight events using confidence thresholds.

## Verification
- Python compilation: passed
- Existing automated tests: **24 passed**
- Product model training: completed
- Custom detector training: completed
