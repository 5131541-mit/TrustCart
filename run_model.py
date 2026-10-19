#!/usr/bin/env python3
"""One command to run the whole model on your computer:
   starts the server, trains the recognition model if needed, drives the digital twin (virtual shelves, cameras, shoppers, staff),
   prints scored results, then keeps the server running so you can open every screen.
   python run_model.py [--port 8000] [--shoppers 3] [--jobs 5] [--no-twin]"""
import argparse, os, secrets, subprocess, sys, time
import requests
here = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=8000); ap.add_argument("--shoppers", type=int, default=3)
ap.add_argument("--jobs", type=int, default=5); ap.add_argument("--no-twin", action="store_true"); a = ap.parse_args()
pw = {k: secrets.token_urlsafe(8) for k in ("admin", "manager", "staff")}; key = secrets.token_hex(8); url = f"http://localhost:{a.port}"
db = os.path.join(here, "model_run.db")
if os.path.exists(db): os.remove(db)
env = dict(os.environ, DB=db, ADMIN_PASSWORD=pw["admin"], MANAGER_PASSWORD=pw["manager"], STAFF_PASSWORD=pw["staff"], DEVICE_API_KEY=key, WINDOW="2", ESC="8")
if not os.path.exists(os.path.join(here, "models", "product_model.joblib")): subprocess.run([sys.executable, "train_model.py"], cwd=here, check=True)
srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "server:app", "--port", str(a.port)], cwd=here, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(40):
        try: requests.get(url + "/api/health", timeout=1); break
        except requests.RequestException: time.sleep(.5)
    requests.post(url + "/api/register", json={"username": "demo", "password": "demo12345", "name": "Demo Shopper"})
    print(f"\nTrustCart is running at {url}\n  Customer  demo / demo12345   -> {url}/shopper\n  Staff     staff / {pw['staff']}   -> {url}/staff\n"
          f"  Manager   manager / {pw['manager']}   -> {url}/app   (light dashboard)\n  Admin     admin / {pw['admin']}   -> {url}/app   (dark dashboard, users and AI model)\n  (sign in at {url}/login and pick the matching tab)")
    if not a.no_twin:
        print("\nRunning the digital twin...")
        subprocess.run([sys.executable, "digital_twin.py", "--server", url, "--key", key, "--admin-password", pw["admin"], "--staff-password", pw["staff"],
                        "--shoppers", str(a.shoppers), "--jobs", str(a.jobs)], cwd=here, env=env)
    print("\nServer is still running. Press Ctrl+C to stop.")
    srv.wait()
except KeyboardInterrupt: pass
finally: srv.terminate()
