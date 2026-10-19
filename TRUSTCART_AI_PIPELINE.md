# TrustCart — Dashboard + AI Pipeline

## Three UI roles

- **Admin:** `/app` with the dark dashboard layout matching `ui_reference/admin_dark_dashboard_reference.png`.
- **Customer:** `/shopper` with the light shopper dashboard matching `ui_reference/customer_dashboard_reference.png`.
- **Store Manager:** `/app` with the light operational dashboard matching `ui_reference/manager_dashboard_reference.png`.
- **Staff:** `/staff` handles escalated incidents and theft-risk reviews.

The supplied reference images are retained under `ui_reference/` so the layout can be compared during development.

## AI / computer-vision pipeline

```text
Camera video
    ↓
Custom product detector (YOLO)
    ↓
Product tracking / stable-count tracker
    ↓
Pick event
    ↓
TrustCart /api/vision
    ↓
Event + scan database
    ↓
AI mismatch / reconciliation engine
    ↓
Low risk → log
Medium risk → shopper nudge / review
High risk → staff alert
    ↓
Staff resolution + alert trail
```

### Important distinction

The existing `recognizer.py` model is a **product-recognition classifier** trained from product photos. The custom camera detector is a separate YOLO pipeline and requires **real annotated shelf/camera images**. Dashboard screenshots must not be used as fake bounding-box training labels.

## Train the existing product-recognition model

```bash
python train_model.py
```

or use **Admin → AI Model**.

## Train the custom camera detector

1. Add real camera/shelf images to `detector_dataset/images/train` and `detector_dataset/images/val`.
2. Add matching YOLO labels to `detector_dataset/labels/train` and `detector_dataset/labels/val`.
3. Check `detector_dataset/data.yaml`.
4. Run:

```bash
python train_custom_detector.py --epochs 50
```

The best weights are copied to:

```text
models/custom_product_detector.pt
```

Then run the real camera pipeline:

```bash
python custom_detector.py --source 0 --weights models/custom_product_detector.pt --server http://localhost:8000 --key YOUR_DEVICE_API_KEY --show
```

## What is connected already

- `/api/vision` receives pick evidence.
- Existing reconciliation logic compares scan/pick/vision/shelf signals.
- Alerts are persisted in SQLite.
- Staff receives escalated alerts.
- Admin can view recognition-model training status.
- Admin can start custom detector training when annotated data exists.
- The three supplied dashboard layouts are retained as visual references.
