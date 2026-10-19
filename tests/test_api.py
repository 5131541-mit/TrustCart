"""Full-stack tests: roles, auth, customer trips, rules, process flow, reports, settings, model.  Run:  pip install pytest httpx  then  pytest -q"""
import base64, io, itertools, os, sys, tempfile, time
os.environ.update(DB=os.path.join(tempfile.mkdtemp(), "t.db"), ADMIN_PASSWORD="Admin#12345", MANAGER_PASSWORD="Manager#12345", STAFF_PASSWORD="Staff#12345",
                  DEVICE_API_KEY="devkey", REGISTER_LIMIT="1000", WINDOW="1", ESC="1000", DATASET_DIR=tempfile.mkdtemp(), MODEL_DIR=tempfile.mkdtemp())
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
import server

K, N = {"X-API-Key": "devkey"}, itertools.count(1)
def judge(): server.reconcile(time.time() + 100)

@pytest.fixture(scope="module")
def web():
    with TestClient(server.app) as c: yield c
def login(user, pw, role=None):
    c = TestClient(server.app); r = c.post("/api/login", json={"username": user, "password": pw, "role": role}); assert r.status_code == 200, r.text; return c
@pytest.fixture
def adm(web): return login("admin", "Admin#12345", "admin")
@pytest.fixture
def mgr(web): return login("manager", "Manager#12345", "manager")
@pytest.fixture
def stf(web): return login("staff", "Staff#12345", "staff")
def customer(zone=None):
    c = TestClient(server.app); name = f"shopper{next(N)}"
    assert c.post("/api/register", json={"username": name, "password": "password123", "name": name.title()}).status_code == 200
    sid = c.post("/api/trip").json()["session"]
    if zone: c.post("/api/session", json={"session": sid, "zone": zone})
    return c, sid
@pytest.fixture(autouse=True)
def clean():
    with server.db() as c:
        for t in ("events", "alerts", "alert_log", "sessions", "trips"): c.execute(f"DELETE FROM {t}")
    server.ACTIVE.clear(); server.ZONE.clear(); server.NUDGED.clear(); yield

def lift(web, shelf, g, conf=None, sku=None):
    web.post("/api/shelf", json={"shelf_id": shelf, "delta_g": -g}, headers=K)
    if conf: web.post("/api/vision", json={"sku": sku, "conf": conf}, headers=K)
def alerts(c): return c.get("/api/alerts").json()

# ----- authentication and roles -----
def test_role_matrix(web, adm, mgr, stf):
    cust, _ = customer()
    assert web.get("/api/alerts").status_code == 401
    assert cust.get("/api/alerts").status_code == 403 and cust.get("/api/settings").status_code == 403 and cust.get("/api/users").status_code == 403
    assert stf.get("/api/alerts").status_code == 200 and stf.get("/api/settings").status_code == 403 and stf.get("/api/users").status_code == 403
    assert mgr.get("/api/settings").status_code == 200 and mgr.get("/api/report").status_code == 200 and mgr.get("/api/users").status_code == 403
    assert adm.get("/api/users").status_code == 200 and adm.get("/api/alerts").status_code == 200 and adm.get("/api/model").status_code == 200
    assert cust.get("/api/catalog").status_code == 200 and len(cust.get("/api/catalog").json()) == 9
    assert mgr.post("/api/trip").status_code == 403                      # only customers shop
def test_login_checks_the_chosen_role(web):
    r = web.post("/api/login", json={"username": "staff", "password": "Staff#12345", "role": "manager"}); assert r.status_code == 403 and "staff account" in r.json()["detail"]
    assert web.post("/api/login", json={"username": "staff", "password": "bad"}).status_code == 401
def test_register_rules(web):
    ok = {"username": "newbie1", "password": "password123", "name": "Newbie"}
    assert web.post("/api/register", json={**ok, "password": "short"}).status_code == 400
    assert web.post("/api/register", json={**ok, "username": "a b"}).status_code == 400
    assert TestClient(server.app).post("/api/register", json=ok).status_code in (200, 409)
    assert TestClient(server.app).post("/api/register", json=ok).status_code == 409
    c = TestClient(server.app); r = c.post("/api/login", json={"username": "newbie1", "password": "password123", "role": "customer"}); assert r.json()["home"] == "/shopper"
def test_pages_by_role(web, adm, mgr, stf):
    cust, _ = customer()
    assert web.get("/shopper", follow_redirects=False).status_code in (302, 307) and web.get("/app", follow_redirects=False).status_code in (302, 307)
    assert cust.get("/shopper").status_code == 200 and cust.get("/app", follow_redirects=False).status_code in (302, 307) and cust.get("/staff", follow_redirects=False).status_code in (302, 307)
    assert 'data-theme="light"' in mgr.get("/app").text and 'data-theme="dark"' in adm.get("/app").text and 'data-role="admin"' in adm.get("/app").text
    assert stf.get("/staff").status_code == 200 and stf.get("/app", follow_redirects=False).status_code in (302, 307)
    assert cust.get("/", follow_redirects=False).headers["location"] == "/shopper" and mgr.get("/", follow_redirects=False).headers["location"] == "/app"
def test_admin_manages_users(adm, mgr):
    assert adm.post("/api/users", json={"username": "cashier1", "password": "longpass99", "role": "staff", "name": "Cashier"}).status_code in (200, 409)
    assert adm.post("/api/users", json={"username": "bad", "password": "longpass99", "role": "owner"}).status_code == 400
    assert adm.delete("/api/users/admin").status_code == 400                                   # cannot delete yourself
    assert any(u["username"] == "cashier1" for u in adm.get("/api/users").json()) and adm.delete("/api/users/cashier1").status_code == 200
def test_device_key_required(web): assert web.post("/api/shelf", json={"shelf_id": "shelf-A1", "delta_g": -50}).status_code == 401

# ----- customer trips -----
def test_trip_ownership_and_family_join(web):
    a, sa = customer(); b, _ = customer()
    assert b.post("/api/scan", json={"sku": "chips", "qty": 1, "session": sa}).status_code == 403
    assert b.get(f"/api/cart/{sa}").status_code == 403
    assert b.post("/api/trip/join", json={"session": sa}).status_code == 200
    assert b.post("/api/scan", json={"sku": "chips", "qty": 1, "session": sa}).status_code == 200
    assert a.get(f"/api/cart/{sa}").json() == [{"sku": "chips", "qty": 1}] and len(a.get(f"/api/activity/{sa}").json()) == 1

# ----- rules -----
def test_matched_sale_and_putback_raise_nothing(web, adm):
    c, sid = customer(); lift(web, "shelf-A2", 520, .95, "cola"); c.post("/api/scan", json={"sku": "cola", "qty": 1, "session": sid}); judge(); assert alerts(adm) == []
    lift(web, "shelf-A1", 50, .95, "chips"); web.post("/api/shelf", json={"shelf_id": "shelf-A1", "delta_g": 50}, headers=K); judge(); assert alerts(adm) == []
@pytest.mark.parametrize("steps,expected,sev", [
    (lambda w, c, s: lift(w, "shelf-A1", 50, .95, "chips"), "Unscanned Item", "High"),
    (lambda w, c, s: lift(w, "shelf-A1", 50, .70, "chips"), "Unscanned Item", "Medium"),
    (lambda w, c, s: lift(w, "shelf-A1", 50), "Possible Theft Risk", "Medium"),
    (lambda w, c, s: (lift(w, "shelf-A3", 540, .9, "rb"), c.post("/api/scan", json={"sku": "rb", "qty": 1, "session": s})), "Scan Mismatch", "Medium"),
    (lambda w, c, s: c.post("/api/scan", json={"sku": "bar", "qty": 1, "session": s}), "Phantom Scan", "Medium"),
    (lambda w, c, s: lift(w, "shelf-D1", 400, .95, "bread"), "Unscanned Item", "High")])
def test_rules(web, adm, steps, expected, sev):
    c, sid = customer(); steps(web, c, sid); judge(); a = alerts(adm); assert len(a) == 1 and a[0]["type"] == expected and a[0]["sev"] == sev
def test_barcode_swap_needs_same_aisle(web, adm):
    c, sid = customer("B2"); lift(web, "shelf-B2", 1030, .9, "milk"); c.post("/api/scan", json={"sku": "chips", "qty": 1, "session": sid}); judge()
    assert [a["type"] for a in alerts(adm) if a["sku"] == "milk"] == ["Barcode Swap"]
def test_swap_not_paired_across_aisles(web, adm):
    c, sid = customer("C2"); lift(web, "shelf-B2", 1030, .9, "milk"); c.post("/api/scan", json={"sku": "chips", "qty": 1, "session": sid}); judge()
    assert [a["type"] for a in alerts(adm) if a["sku"] == "milk"] == ["Unscanned Item"]
def test_nudge_goes_to_shopper_at_the_shelf(web, adm):
    _, sa = customer("A1"); _, sb = customer("B1"); lift(web, "shelf-A1", 50, .95, "chips"); judge(); a = alerts(adm)[0]; assert a["session"] == sa and a["status"] == "Nudged"
def test_respond_checkout_flow(web, adm):
    c, sid = customer("A1"); other, _ = customer(); lift(web, "shelf-A1", 50, .95, "chips")
    assert c.post("/api/checkout", json={"session": sid}).status_code == 409
    judge(); aid = alerts(adm)[0]["id"]; assert c.post("/api/checkout", json={"session": sid}).status_code == 409
    assert other.post(f"/api/alerts/{aid}/respond", json={"action": "putback", "session": sid}).status_code == 403
    assert c.post(f"/api/alerts/{aid}/respond", json={"action": "added", "session": sid}).status_code == 200
    r = c.post("/api/checkout", json={"session": sid}); assert r.status_code == 200 and r.json()["total"] == 30 and "upi://pay" in r.json()["upi"]
    assert c.post("/api/scan", json={"sku": "chips", "qty": 1, "session": sid}).status_code == 403          # trip is paid
def test_dispute_goes_to_staff_and_trail(web, adm, stf):
    c, sid = customer("A1"); lift(web, "shelf-A1", 50, .95, "chips"); judge(); aid = alerts(adm)[0]["id"]
    c.post(f"/api/alerts/{aid}/respond", json={"action": "dispute", "session": sid, "note": "I put it back"})
    a = [x for x in stf.get("/api/alerts").json() if x["id"] == aid][0]; assert a["status"] == "Staff check" and a["note"] == "I put it back"
    stf.post(f"/api/alerts/{aid}/resolve?outcome=false_alarm")
    tr = adm.get(f"/api/alerts/{aid}/trail").json(); assert [t["step"] for t in tr] == ["detected", "nudged", "disputed", "resolved"] and tr[-1]["actor"] == "staff"
    assert adm.get("/api/stats").json()["accuracy"] == 0.0
def test_flow_funnel(web, adm):
    c, sid = customer("A1"); lift(web, "shelf-A1", 50, .95, "chips"); judge(); aid = alerts(adm)[0]["id"]
    c.post(f"/api/alerts/{aid}/respond", json={"action": "added", "session": sid}); f = adm.get("/api/stats").json()["flow"]; assert f["alerts"] == 1 and f["nudged"] == 1 and f["fixed"] == 1

# ----- manager features -----
def test_settings_reports_products(web, mgr):
    assert mgr.post("/api/settings", json={"window": 0}).status_code == 400 and mgr.post("/api/settings", json={"upi_id": "nope"}).status_code == 400
    assert mgr.post("/api/settings", json={"store_name": "Test Mall"}).status_code == 200 and mgr.get("/api/stats").json()["store"] == "Test Mall"
    lift(web, "shelf-A1", 50, .95, "chips"); judge()
    r = mgr.get("/api/report?days=7").json(); assert r["totals"]["alerts"] == 1 and len(r["by_day"]) == 7
    c = mgr.get("/api/report.csv?days=7"); assert c.headers["content-type"].startswith("text/csv") and "Unscanned Item" in c.text
    assert mgr.post("/api/products/chips/restock?n=10").status_code == 200 and len(mgr.get("/api/products").json()) == 9
def test_demo_toggle(web):
    with TestClient(server.app) as c:                    # a context-managed client keeps one event loop alive for the demo task
        assert c.post("/api/login", json={"username": "admin", "password": "Admin#12345"}).status_code == 200
        assert c.post("/api/demo", json={"on": True}).status_code == 200; time.sleep(.4); assert c.get("/api/stats").json()["demo"] is True
        c.post("/api/demo", json={"on": False}); time.sleep(.4); assert c.get("/api/stats").json()["demo"] is False
def test_privacy_purge(web):
    server.add_event("scan", "chips", 1, "S-OLD", ts=time.time() - 90 * 3600, done=1); server.purge()
    with server.db() as c: assert c.execute("SELECT COUNT(*) FROM events WHERE session='S-OLD'").fetchone()[0] == 0

# ----- product recognition model -----
def img64(sku, n=1):
    p = os.path.join(server.HERE, "seed_images", sku, f"{n}.png"); return "data:image/png;base64," + base64.b64encode(open(p, "rb").read()).decode()
def test_model_train_upload_recognize(web, adm, mgr):
    c, _ = customer()
    assert web.post("/api/recognize", json={"image": img64("chips")}).status_code == 401
    assert adm.post("/api/model/train").json()["started"] is True
    for _ in range(90):
        s = adm.get("/api/model").json()["status"]
        if not s["running"]: break
        time.sleep(1)
    assert s["msg"] == "done", s
    r = c.post("/api/recognize", json={"image": img64("banana")}); assert r.status_code == 200 and r.json()["top"][0]["sku"] == "banana" and r.json()["name"] == "Bananas"
    assert c.post("/api/recognize", json={"image": "not-an-image"}).status_code == 400
    assert c.post("/api/model/train").status_code == 403 and mgr.post("/api/model/train").status_code == 403 and c.post("/api/model/upload", json={"image": img64("bar"), "sku": "bar"}).status_code == 403
    assert adm.post("/api/model/upload", json={"image": img64("bar"), "sku": "bar"}).json()["photos_for_product"] == 1 and adm.post("/api/model/upload", json={"image": img64("bar"), "sku": "nope"}).status_code == 400
    card = mgr.get("/api/model").json(); assert card["card"]["n_train"] > 100 and card["uploaded_photos"]["bar"] == 1
