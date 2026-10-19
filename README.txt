TRUSTCART: full-stack scan-and-go loss prevention
==================================================
FASTEST WAY TO SEE EVERYTHING (one command)
  pip install -r requirements.txt
  python run_model.py
  Starts the server, runs a digital twin (virtual shelves, cameras, shoppers, staff), prints scored results and the logins for every role.

ROLES (sign in at /login and pick your tab; the server checks the role)
  Customer  /shopper   create your own account. Basket, live scan view, scan by barcode or by photo (AI), store map check-in,
                       reminders ("did you forget to scan..."), dispute button, family cart code, English/Hindi/Marathi, UPI checkout, works offline.
  Staff     /staff     only the cases escalated after an ignored or disputed reminder, plus theft-risk reviews.
  Manager   /app       light dashboard: Dashboard, Live Monitoring, Alerts & Incidents, Customers, Products & Inventory, How it works
                       (three signals one verdict + live process funnel), Reports (CSV/PDF), AI Model (view), Settings.
  Admin     /app       dark dashboard: everything the manager has plus Users & Roles and training the AI Model.
  First start prints generated passwords for admin, manager and staff (or set ADMIN_PASSWORD, MANAGER_PASSWORD, STAFF_PASSWORD).

THE MODEL YOU CAN TRAIN
  python train_model.py            trains the product recognition model from seed_images/ and anything in dataset/<product>/
  Admin > AI Model                 upload photos per product, press Retrain, test any image. Customers use it via Scan Product > photo.
  The shipped product model was retrained from the supplied 9 product classes (714 augmented samples).
  Held-out cross-render accuracy is 60% (3/5), so it is an MVP model, not a production accuracy claim.
  The shipped custom camera detector is trained on 6,112 synthetic/augmented detection samples generated from the supplied product images.
  For real-store deployment, retrain with real annotated shelf/camera frames.

END-TO-END STAFF DEMO
  Open /staff after signing in as staff. Use Run Unscanned Demo or Run Mismatch Demo.
  The dashboard creates deterministic simulation data that exercises: camera evidence -> pick event -> scan database -> mismatch engine -> risk score -> staff alert -> evidence trail -> resolution.
  This is for MVP demonstration only; it does not represent real supermarket CCTV validation.

THE RULES ENGINE (three signals, one verdict)  phone scan + shelf weight + camera -> Unscanned Item, Scan Mismatch, Possible Theft Risk,
  Barcode Swap, Phantom Scan, or nothing. Every alert keeps a trail: detected > nudged > escalated > resolved.
  Shoppers check in at a shelf (store map, or a QR code on the shelf that opens /shopper?aisle=A1) so reminders reach the right person.

DIGITAL TWIN AND TESTS
  python digital_twin.py --server http://localhost:8000 --key DEVICE_KEY --admin-password ... --staff-password ...
  pytest -q tests                  24 automated tests: roles, auth, rules, trips, process flow, reports, settings, model training.
  Twin results measure the software under assumed sensor noise, NOT real-world accuracy.

DEPLOY
  Render.com : push this folder to GitHub > New > Blueprint (render.yaml). Set ADMIN_PASSWORD, MANAGER_PASSWORD, STAFF_PASSWORD. 1 GB disk for the database and models.
  Docker     : put SECRET_KEY, DEVICE_API_KEY, ADMIN_PASSWORD, MANAGER_PASSWORD, STAFF_PASSWORD in .env then: docker compose up --build
  Any VPS    : uvicorn server:app --host 0.0.0.0 --port 8000 behind https (Caddy or nginx), with COOKIE_SECURE=1.
  Phone camera and barcode scanning need https.

BEFORE GOING LIVE
  [x] hashed passwords, signed HttpOnly cookies, role checks on the server, login and sign-up rate limits, customers can only touch their own trip
  [ ] set SECRET_KEY, DEVICE_API_KEY, COOKIE_SECURE=1; back up /data; shopper events are auto-deleted after the retention period (Settings)
  [ ] one store per deployment. Multi-store, single sign-on, audit logs, password reset by email and a privacy/legal review are NOT built.

HARDWARE (untested on real devices)
  shelf_node.ino      ESP32 + HX711 load cell -> POST /api/shelf (Wi-Fi, SERVER, SHELF_ID like shelf-A1, DEVICE_KEY, calibrate CAL)
  custom_detector.py  Python + trained HOG/SVM product detector -> stable tracking -> POST /api/vision.
  camera_detector.py  Legacy optional camera adapter; the shipped MVP uses custom_detector.py.

DASHBOARD / AI PIPELINE UPDATE
  The UI now preserves the three supplied layouts: dark Admin dashboard, light Customer dashboard, and light Store Manager dashboard.
  ui_reference/ contains the exact supplied screenshots used as visual references.
  assets/live_store_reference.png and assets/user_live_scan_reference.png are the supplied camera-view crops used as the default demo feed.
  custom_detector.py implements Camera video -> custom product detector -> stable product tracking -> pick event -> /api/vision.
  train_custom_detector.py trains the shipped CPU-friendly custom product detector. The supplied training set is synthetic because the ZIP had no real bounding-box annotations.
  TRUSTCART_AI_PIPELINE.md contains the complete setup and pipeline notes.
