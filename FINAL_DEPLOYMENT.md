# TrustCart — Final MVP Deployment

## Start locally

```bash
python -m pip install -r requirements.txt
uvicorn server:app --host 0.0.0.0 --port 8000
```

Open `/login`.

## Train all AI models

```bash
python train_all_models.py
```

This trains:
1. `models/product_model.joblib` — product recognition
2. `models/custom_product_detector.joblib` — camera product detector

## Camera pipeline

```text
Camera Video
    ↓
Custom Product Detector
    ↓
Stable Product Tracking
    ↓
Pick Event
    ↓
/api/vision
    ↓
Scan Database
    ↓
AI Mismatch Engine
    ↓
Staff / Manager Alert
    ↓
Resolve / Nudge / Escalate
    ↓
Reports & Analytics
```

Run the detector against a webcam:

```bash
python custom_detector.py --source 0 --server http://localhost:8000 --show
```

For a video file:

```bash
python custom_detector.py --source path/to/store_video.mp4 --server http://localhost:8000 --show
```

## Roles

- Customer → `/shopper`
- Staff → `/staff`
- Store Manager → `/app`
- Admin → `/app` with admin role

The server enforces roles; UI navigation is not the security boundary.

## Deployment requirements

Before public deployment, set strong values for `SECRET_KEY`, `DEVICE_API_KEY`, `ADMIN_PASSWORD`, `MANAGER_PASSWORD`, `STAFF_PASSWORD`, and `COOKIE_SECURE=1` behind HTTPS.

## AI limitation

The camera detector is fully trained as an MVP using synthetic shelf scenes generated from the supplied product images because the ZIP did not contain real bounding-box-labelled store footage. The model is therefore a trained prototype, not a validated production computer-vision model.

Real deployment requires a real shelf/camera dataset, bounding-box annotations, a separate test set and measured false-positive/false-negative performance.

## End-to-end MVP demo

The Staff dashboard now includes two deterministic demo controls:

- **Run Unscanned Demo** — Camera evidence + pick event + scan database check → Unscanned Item → High priority Staff alert.
- **Run Mismatch Demo** — Camera evidence + two picked units + one scanned unit → Scan Mismatch → Medium priority Staff alert.

The Staff workflow is:

```text
Camera Video
    ↓
Product Detector
    ↓
Product Tracking
    ↓
Pick Event
    ↓
Scan Database
    ↓
Mismatch Engine
    ↓
Risk Score
    ↓
Staff Alert
    ↓
View Evidence / Resolve
    ↓
Manager Analytics
```

The demo controls create **simulation data only**. They are included so the MVP can be demonstrated without real supermarket footage. They do not claim that the camera detector was validated on real store CCTV.
