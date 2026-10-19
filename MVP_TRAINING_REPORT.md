# TrustCart — Final MVP Training & Verification Report

## AI components

### 1. Product recognition model
- Model: `models/product_model.joblib`
- Classes: apple, banana, bar, bread, chips, cola, milk, rb, sun
- Augmented training samples: 714
- Augmented validation accuracy: 100%
- Cross-render held-out accuracy: 60% (3/5)

### 2. Camera product detector
- Model: `models/custom_product_detector.joblib`
- Method: HOG features + LinearSVC
- Product classes: 9
- Background class: yes
- Training samples: 5,011
- Validation samples: 1,101
- Total generated training/validation examples: 6,112
- Validation accuracy on the generated MVP dataset: 100%
- Dataset source: supplied TrustCart product images + synthetic shelf scenes

## End-to-end MVP flow

Camera video → product detection → stable tracking → pick event → scan database → AI mismatch engine → staff/manager alert → resolution → analytics.

## Verification

- Automated tests: 24 passed
- Server/API role/auth/rules/process/report/model-training tests: passed
- Training command: `python train_all_models.py`
- Server: `uvicorn server:app --host 0.0.0.0 --port 8000`
- Camera demo: `python custom_detector.py --source 0 --server http://localhost:8000 --show`

## Dashboard role mapping

- Customer: `/shopper` — basket, scanning, store map, checkout, recent activity.
- Staff: `/staff` — escalated incidents and reviews.
- Store Manager: `/app` — store operations, live monitoring, alerts, inventory, reports.
- Admin: `/app` with admin role — platform administration, users/roles, AI training, system/device controls.

## Dataset limitation

This is a trained MVP, not a real-store validated production model. No real annotated supermarket camera footage was supplied. The camera detector therefore uses synthetic shelf scenes generated from the supplied product images. Real deployment requires annotated camera/shelf data, a separate real-world test set, and measured false-positive/false-negative performance.
