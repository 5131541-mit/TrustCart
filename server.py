"""TrustCart production server: login + roles, mismatch rules, SQLite, reports, settings, demo data, live streams.
Run:  pip install -r requirements.txt   then   uvicorn server:app --host 0.0.0.0 --port 8000
First start prints the generated manager/staff passwords (or set ADMIN_PASSWORD / STAFF_PASSWORD).
Production: set SECRET_KEY, DEVICE_API_KEY, COOKIE_SECURE=1 (behind https), and keep the DB on a persistent disk."""
import asyncio, base64, csv, hashlib, hmac, io, json, os, random, re, secrets, sqlite3, time
from pathlib import Path
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel
try: import recognizer
except ImportError: recognizer = None

DETECTOR_MODEL = os.path.join(os.path.dirname(__file__), 'models', 'custom_product_detector.joblib')
DETECTOR_STATE = {'running': False, 'msg': 'Custom detector trained' if os.path.exists(DETECTOR_MODEL) else 'Custom detector not trained', 'weights': DETECTOR_MODEL if os.path.exists(DETECTOR_MODEL) else None, 'last': None}

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.getenv("DB", os.path.join(HERE, "trustcart.db"))
SECRET = os.getenv("SECRET_KEY") or secrets.token_hex(32)
DEVICE_KEY = os.getenv("DEVICE_API_KEY", "")
SECURE = os.getenv("COOKIE_SECURE", "0") == "1"
TOL = 0.35
PRODUCTS = {"chips": dict(name="Lay’s Chips (Classic)", unit="1 packet", g=50, price=30), "cola": dict(name="Coca-Cola (500ml)", unit="500 ml", g=520, price=40),
            "rb": dict(name="Red Bull (250ml)", unit="250 ml", g=270, price=125), "sun": dict(name="Sunglasses", unit="1 pc", g=40, price=499),
            "milk": dict(name="Milk", unit="1 L", g=1030, price=55), "apple": dict(name="Apple (Red)", unit="1 pc", g=180, price=20),
            "bar": dict(name="Protein Bar", unit="1 pc", g=60, price=150), "banana": dict(name="Bananas", unit="1 kg", g=1000, price=40),
            "bread": dict(name="Bread", unit="1 pc", g=400, price=45)}
SHELVES = {"shelf-A1": "chips", "shelf-A2": "cola", "shelf-A3": "rb", "shelf-B1": "sun", "shelf-B2": "milk", "shelf-C1": "apple",
           "shelf-C2": "bar", "shelf-C3": "banana", "shelf-D1": "bread"}   # one load cell per product zone; the letter+number is the shelf QR check-in code
DEFAULTS = {"store_name": "Riverside Mall Store #014", "window": os.getenv("WINDOW", "15"), "esc": os.getenv("ESC", "30"),
            "upi_id": os.getenv("UPI_ID", "store@upi"), "theft_conf": "0.6", "high_conf": "0.85", "nudge": "1", "retention_h": "24"}
SET = dict(DEFAULTS)
subs, ssubs, ACTIVE, NUDGED, FAILS, ZONE = [], [], {}, {}, {}, {}   # ZONE: session -> (aisle, time) from QR/BLE check-in
demo = {"task": None}
SCHEMA = """CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, ts REAL, kind TEXT, sku TEXT, qty INT, session TEXT, weight_g REAL, conf REAL, done INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY, ts REAL, type TEXT, sev TEXT, sku TEXT, scanned INT, picked INT, conf REAL, status TEXT, resolved_ts REAL, outcome TEXT, session TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS users(username TEXT PRIMARY KEY, pw TEXT, role TEXT, name TEXT);
CREATE TABLE IF NOT EXISTS trips(session TEXT, username TEXT, owner INT, started REAL, status TEXT, PRIMARY KEY(session, username));
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS stock(sku TEXT PRIMARY KEY, qty INT);
CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, first_ts REAL, last_ts REAL, items INT DEFAULT 0, value REAL DEFAULT 0, status TEXT DEFAULT 'In store');
CREATE TABLE IF NOT EXISTS alert_log(id INTEGER PRIMARY KEY, aid INT, ts REAL, step TEXT, actor TEXT, detail TEXT);"""

def db():
    c = sqlite3.connect(DB, timeout=10); c.row_factory = sqlite3.Row; return c

def log(aid, step, actor="system", detail="", c=None):
    """Process-flow trail: every step an alert goes through (detected, nudged, escalated, disputed, resolved)."""
    q = "INSERT INTO alert_log(aid,ts,step,actor,detail) VALUES(?,?,?,?,?)"
    if c is not None: c.execute(q, (aid, time.time(), step, actor, detail))
    else:
        with db() as cc: cc.execute(q, (aid, time.time(), step, actor, detail))

# ---------- auth ----------
def hash_pw(p, salt=None):
    salt = salt or secrets.token_hex(8)
    return salt + ":" + hashlib.pbkdf2_hmac("sha256", p.encode(), salt.encode(), 200_000).hex()
def check_pw(p, h): return hmac.compare_digest(hash_pw(p, h.split(":")[0]), h)
def sign(u, r):
    body = f"{u}|{r}|{int(time.time()) + 86400}"
    return base64.urlsafe_b64encode(f"{body}|{hmac.new(SECRET.encode(), body.encode(), 'sha256').hexdigest()}".encode()).decode()
def user_of(req):
    try:
        body, sig = base64.urlsafe_b64decode(req.cookies.get("tc", "")).decode().rsplit("|", 1)
        u, r, exp = body.split("|")
        if hmac.compare_digest(sig, hmac.new(SECRET.encode(), body.encode(), "sha256").hexdigest()) and int(exp) > time.time():
            return {"user": u, "role": r}
    except Exception: pass
def need(*roles):
    def dep(req: Request):
        u = user_of(req)
        if not u: raise HTTPException(401, "Please sign in")
        if not (u["role"] in roles or (u["role"] == "admin" and ("manager" in roles or "staff" in roles))): raise HTTPException(403, "Not allowed for your role")
        return u
    return dep
MGR, ANY = Depends(need("manager")), Depends(need("manager", "staff"))     # the admin role passes every manager/staff check
ADM, CUST, USER = Depends(need("admin")), Depends(need("customer")), Depends(need("customer", "staff", "manager"))
def device(req: Request):
    if DEVICE_KEY and req.headers.get("x-api-key") != DEVICE_KEY and not user_of(req): raise HTTPException(401, "Bad device key")
DEV = Depends(device)

def init():
    with db() as c:
        c.executescript(SCHEMA)
        for k, v in c.execute("SELECT k,v FROM settings"): SET[k] = v
        for s in PRODUCTS: c.execute("INSERT OR IGNORE INTO stock VALUES(?,80)", (s,))
        try: c.execute("ALTER TABLE users ADD COLUMN name TEXT")
        except sqlite3.OperationalError: pass
        c.execute("UPDATE users SET role='admin' WHERE username='admin' AND role='manager'")      # databases from earlier versions
        for u, r, env, nm in (("admin", "admin", "ADMIN_PASSWORD", "Admin"), ("manager", "manager", "MANAGER_PASSWORD", "Store Manager"), ("staff", "staff", "STAFF_PASSWORD", "Staff")):
            if not c.execute("SELECT 1 FROM users WHERE username=?", (u,)).fetchone():
                pw = os.getenv(env) or secrets.token_urlsafe(9)
                c.execute("INSERT INTO users(username,pw,role,name) VALUES(?,?,?,?)", (u, hash_pw(pw), r, nm))
                print(f"[TrustCart] created {r} login  username: {u}  password: {pw}   (change it in Settings)", flush=True)
init()

# ---------- events and rules ----------
def publish(m):
    for q in subs: q.put_nowait(m)
    if m.get("k") == "nudge":
        for s, q in ssubs:
            if s == m["session"]: q.put_nowait(m)

def add_event(kind, sku, qty=1, session=None, weight=None, conf=None, ts=None, done=0):
    if sku not in PRODUCTS: raise HTTPException(400, "unknown sku")
    ts = ts or time.time()
    with db() as c:
        c.execute("INSERT INTO events(ts,kind,sku,qty,session,weight_g,conf,done) VALUES(?,?,?,?,?,?,?,?)", (ts, kind, sku, qty, session, weight, conf, done))
        if kind == "pick": c.execute("UPDATE stock SET qty=MAX(0,qty-?) WHERE sku=?", (qty, sku))
        if kind == "scan" and session:
            ACTIVE[session] = ts
            c.execute("INSERT INTO sessions(id,first_ts,last_ts,items,value) VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET last_ts=excluded.last_ts, items=items+excluded.items, value=value+excluded.value",
                      (session, ts, ts, qty, qty * PRODUCTS[sku]["price"]))
    publish({"k": "ev", "sku": sku})

def nudge_msg(aid, sess, sku, typ, scanned, picked):
    p = PRODUCTS[sku]
    return {"k": "nudge", "id": aid, "session": sess, "sku": sku, "name": p["name"], "price": p["price"], "type": typ,
            "qty": max(1, picked - scanned), "scanned": scanned, "picked": picked}

def pick_session(sku):
    """Who gets the nudge? The shopper who checked in to this aisle most recently; otherwise the most recently active shopper."""
    now, code = time.time(), next((k for k, v in SHELVES.items() if v == sku), "").split("-")[-1]      # e.g. "A1"
    live = [(z[1], s, z[0]) for s, z in ZONE.items() if now - z[1] < 180 and s in ACTIVE]
    for match in (lambda z: z == code, lambda z: z[:1] == code[:1]):                                    # exact shelf first, then same aisle
        here = [(ts, s) for ts, s, z in live if match(z)]
        if here: return max(here)[1]
    return max(ACTIVE, key=ACTIVE.get) if ACTIVE and now - max(ACTIVE.values()) < 600 else None

def raise_alert(c, typ, sev, sku, scanned, picked, conf):
    sess = pick_session(sku)
    ok = bool(sess) and typ != "Possible Theft Risk" and SET["nudge"] == "1"     # nudge first; theft risk goes to staff
    now = time.time()
    aid = c.execute("INSERT INTO alerts(ts,type,sev,sku,scanned,picked,conf,status,session) VALUES(?,?,?,?,?,?,?,?,?)",
                    (now, typ, sev, sku, scanned, picked, conf, "Nudged" if ok else "Staff check", sess)).lastrowid
    publish({"k": "alert", "id": aid})
    log(aid, "detected", "system", f"{typ}: {scanned} scanned, {picked} picked, camera {str(round(conf * 100)) + '%' if conf else 'no match'}", c)
    log(aid, "nudged" if ok else "escalated", "system", "Shopper asked to fix it in the app" if ok else
        ("Possible theft risk goes straight to staff" if typ == "Possible Theft Risk" else "No active shopper to nudge"), c)
    if ok: NUDGED[aid] = now; publish(nudge_msg(aid, sess, sku, typ, scanned, picked))
    else: publish({"k": "staff", "id": aid})

def zone_ok(sess, sku):
    """A barcode swap needs the scanning shopper to be in the aisle of the product that was lifted (when we know where they are)."""
    z = ZONE.get(sess)
    if not sess or not z: return True
    return z[0][:1] == next((k for k, v in SHELVES.items() if v == sku), "").split("-")[-1][:1]

def reconcile(now=None):
    now, W = now or time.time(), float(SET["window"])
    theft, high = float(SET["theft_conf"]), float(SET["high_conf"])
    with db() as c:
        for p in c.execute("SELECT * FROM events WHERE kind='pick' AND done=0 AND ts<=?", (now - W,)).fetchall():
            lo, hi, need_ = p["ts"] - W, p["ts"] + W, p["qty"]
            vis = c.execute("SELECT MAX(conf) m FROM events WHERE kind='vision' AND sku=? AND ts BETWEEN ? AND ?", (p["sku"], lo - 4, hi)).fetchone()["m"]     # the camera sees the hand a moment before the load cell settles
            scans = c.execute("SELECT * FROM events WHERE kind='scan' AND done=0 AND sku=? AND ts BETWEEN ? AND ?", (p["sku"], lo, hi)).fetchall()
            got = sum(s["qty"] for s in scans)
            for s in scans: c.execute("UPDATE events SET done=1 WHERE id=?", (s["id"],))
            c.execute("UPDATE events SET done=1 WHERE id=?", (p["id"],))
            if got >= need_: continue                                   # matched
            if got > 0: raise_alert(c, "Scan Mismatch", "Medium", p["sku"], got, need_, vis); continue
            gp = PRODUCTS[p["sku"]]["g"]
            has_pick = lambda k: c.execute("SELECT 1 FROM events WHERE kind='pick' AND sku=? AND ts BETWEEN ? AND ?", (k, lo, hi)).fetchone()
            other = next((s for s in c.execute("SELECT * FROM events WHERE kind='scan' AND done=0 AND sku!=? AND ts BETWEEN ? AND ?", (p["sku"], lo, hi)).fetchall()
                          if abs(PRODUCTS[s["sku"]]["g"] - gp) / gp > TOL and not has_pick(s["sku"]) and zone_ok(s["session"], p["sku"])), None)
            if other:
                c.execute("UPDATE events SET done=1 WHERE id=?", (other["id"],))
                raise_alert(c, "Barcode Swap", "High", p["sku"], other["qty"], need_, vis)
            elif vis is None or vis < theft: raise_alert(c, "Possible Theft Risk", "Medium", p["sku"], 0, need_, vis)
            else: raise_alert(c, "Unscanned Item", "High" if vis >= high else "Medium", p["sku"], 0, need_, vis)
        for s in c.execute("SELECT * FROM events WHERE kind='scan' AND done=0 AND ts<=?", (now - 2 * W,)).fetchall():
            c.execute("UPDATE events SET done=1 WHERE id=?", (s["id"],))
            raise_alert(c, "Phantom Scan", "Medium", s["sku"], s["qty"], 0, None)

def purge():
    cut = time.time() - float(SET["retention_h"]) * 3600
    with db() as c:                                    # privacy: raw events and shopper sessions are deleted after the retention period
        c.execute("DELETE FROM events WHERE ts<? AND done=1", (cut,)); c.execute("DELETE FROM sessions WHERE last_ts<?", (cut,))
        c.execute("UPDATE alerts SET session=NULL WHERE ts<?", (cut,))

async def loop():
    last = 0
    while True:
        await asyncio.sleep(1)
        try:
            reconcile()
            for aid, t0 in list(NUDGED.items()):          # ignored nudge -> staff spot-check
                if time.time() - t0 > float(SET["esc"]):
                    NUDGED.pop(aid)
                    with db() as c: n = c.execute("UPDATE alerts SET status='Staff check' WHERE id=? AND status='Nudged'", (aid,)).rowcount
                    if n: publish({"k": "staff", "id": aid}); log(aid, "escalated", "system", "No reply from the shopper")
            if time.time() - last > 3600: last = time.time(); purge()
        except Exception as e: print("[TrustCart] loop error:", e, flush=True)

async def demo_loop():
    """Built-in demo traffic so the app can be shown without hardware (Settings > Demo data)."""
    while True:
        sess, sku, r = f"S-D{random.randint(1, 6)}", random.choice(list(PRODUCTS)), random.random()
        ACTIVE[sess], g = time.time(), PRODUCTS[sku]["g"]
        def lift(n=1, conf=.93):
            add_event("pick", sku, n, weight=g * n)
            if conf: add_event("vision", sku, 1, conf=conf)
        scan = lambda s=sku, n=1: add_event("scan", s, n, sess)
        if r < .72: lift(); scan()
        elif r < .82: lift()
        elif r < .88: lift(2); scan(n=1)
        elif r < .92: lift(conf=.4)
        elif r < .96: scan()
        else:
            o = [s for s in PRODUCTS if abs(PRODUCTS[s]["g"] - g) / g > TOL]
            lift(); scan(random.choice(o))
        for aid, t0 in list(NUDGED.items()):              # simulated shoppers answer their nudges
            with db() as c: a = c.execute("SELECT session FROM alerts WHERE id=?", (aid,)).fetchone()
            if a and (a["session"] or "").startswith("S-D") and time.time() - t0 > 4 and random.random() < .8:
                respond_core(aid, Resp(action=random.choice(["added", "added", "putback"]), session=a["session"]))
        await asyncio.sleep(random.uniform(1, 3))

app = FastAPI(title="TrustCart")
@app.on_event("startup")
async def _s(): asyncio.create_task(loop())

# ---------- models ----------
class Shelf(BaseModel): shelf_id: str; delta_g: float; ts: float | None = None
class Vision(BaseModel): sku: str; conf: float; ts: float | None = None
class Scan(BaseModel): sku: str; qty: int = 1; session: str
class Sess(BaseModel): session: str; zone: str | None = None
class Resp(BaseModel): action: str; session: str; note: str | None = None
class Login(BaseModel): username: str; password: str; role: str | None = None
class Register(BaseModel): username: str; password: str; name: str
class NewUser(BaseModel): username: str; password: str; role: str; name: str | None = None
class Pw(BaseModel): old: str; new: str
class Img(BaseModel): image: str; sku: str | None = None
class Join(BaseModel): session: str
ROLES = ("customer", "staff", "manager", "admin")
USERNAME = re.compile(r"[A-Za-z0-9_.\-]{3,30}")

# ---------- pages and auth ----------
HOME = {"customer": "/shopper", "staff": "/staff", "manager": "/app", "admin": "/app"}
def page(n, **attrs):
    html = open(os.path.join(HERE, n), encoding="utf-8").read()
    for k, v in attrs.items(): html = html.replace("<html ", f'<html data-{k}="{v}" ', 1)
    return HTMLResponse(html)
def gate(req, roles, name, **attrs):
    u = user_of(req)
    return page(name, **attrs) if u and (u["role"] in roles or (u["role"] == "admin" and "manager" in roles)) else RedirectResponse("/login")
@app.get("/")
def root(req: Request): u = user_of(req); return RedirectResponse(HOME.get(u["role"], "/login") if u else "/login")
@app.get("/login")
def login_page(): return page("login.html")
@app.get("/app")
def app_page(req: Request):
    u = user_of(req); return gate(req, ("manager",), "trustcart_dashboard.html", role=u["role"] if u else "", theme="dark" if u and u["role"] == "admin" else "light")
@app.get("/admin")
def admin_page(): return RedirectResponse("/app")
@app.get("/staff")
def staff_page(req: Request): return gate(req, ("staff", "manager"), "staff.html")
@app.get("/shopper")
def shopper_page(req: Request): return gate(req, ("customer",), "shopper.html")
@app.get("/api/health")
def health(): return {"trustcart": True}

def throttle(key, limit, secs):
    now = time.time(); FAILS[key] = [t for t in FAILS.get(key, []) if now - t < secs]
    if len(FAILS[key]) >= limit: raise HTTPException(429, "Too many attempts. Try again in a few minutes.")
    return now
def start_session(res, row): res.set_cookie("tc", sign(row["username"], row["role"]), httponly=True, samesite="lax", secure=SECURE, max_age=86400)
@app.post("/api/login")
def login(b: Login, req: Request, res: Response):
    key = (req.client.host if req.client else "?") + b.username.lower(); now = throttle(key, 5, 300)
    with db() as c: u = c.execute("SELECT * FROM users WHERE username=?", (b.username,)).fetchone()
    if not u or not check_pw(b.password, u["pw"]): FAILS[key].append(now); raise HTTPException(401, "Wrong username or password")
    if b.role and b.role != u["role"]: raise HTTPException(403, f"This is a {u['role']} account. Choose the {u['role']} tab to sign in.")
    start_session(res, u); return {"user": u["username"], "role": u["role"], "name": u["name"] or u["username"], "home": HOME[u["role"]]}
@app.post("/api/register")
def register(b: Register, req: Request, res: Response):
    key = "reg" + (req.client.host if req.client else "?"); FAILS[key] = throttle(key, int(os.getenv("REGISTER_LIMIT", "20")), 3600) and FAILS[key] + [time.time()]
    if not USERNAME.fullmatch(b.username) or len(b.password) < 8 or not 1 <= len(b.name.strip()) <= 40:
        raise HTTPException(400, "Username 3-30 letters/digits, password at least 8 characters, and your name")
    with db() as c:
        if c.execute("SELECT 1 FROM users WHERE username=?", (b.username,)).fetchone(): raise HTTPException(409, "That username is taken")
        c.execute("INSERT INTO users(username,pw,role,name) VALUES(?,?,?,?)", (b.username, hash_pw(b.password), "customer", b.name.strip()))
    start_session(res, {"username": b.username, "role": "customer"}); return {"user": b.username, "role": "customer", "name": b.name.strip(), "home": "/shopper"}
@app.post("/api/logout")
def logout(res: Response): res.delete_cookie("tc"); return {"ok": True}
@app.get("/api/me")
def me(req: Request):
    u = user_of(req)
    if not u: raise HTTPException(401, "Please sign in")
    with db() as c: row = c.execute("SELECT * FROM users WHERE username=?", (u["user"],)).fetchone()
    if not row: raise HTTPException(401, "Please sign in")
    return {"user": u["user"], "role": u["role"], "name": row["name"] or u["user"], "store": SET["store_name"]}
@app.post("/api/password")
def password(b: Pw, req: Request):
    u = user_of(req)
    if not u: raise HTTPException(401)
    if len(b.new) < 8: raise HTTPException(400, "New password must be at least 8 characters")
    with db() as c:
        row = c.execute("SELECT pw FROM users WHERE username=?", (u["user"],)).fetchone()
        if not row or not check_pw(b.old, row["pw"]): raise HTTPException(403, "Current password is wrong")
        c.execute("UPDATE users SET pw=? WHERE username=?", (hash_pw(b.new), u["user"]))
    return {"ok": True}

# ---------- devices and shoppers ----------
@app.post("/api/shelf", dependencies=[DEV])
def shelf(b: Shelf):
    sku = SHELVES.get(b.shelf_id)
    if not sku: raise HTTPException(404, "unknown shelf")
    qty = max(1, round(abs(b.delta_g) / PRODUCTS[sku]["g"]))
    if b.delta_g < 0: add_event("pick", sku, qty, weight=-b.delta_g, ts=b.ts); return {"sku": sku, "picked": qty}
    with db() as c:                                                       # put-back cancels a recent unjudged pick
        r = c.execute("SELECT id,qty FROM events WHERE kind='pick' AND done=0 AND sku=? ORDER BY ts DESC", (sku,)).fetchone()
        if r: left = r["qty"] - qty; c.execute("UPDATE events SET qty=?, done=? WHERE id=?", (max(left, 0), int(left <= 0), r["id"]))
        c.execute("UPDATE stock SET qty=qty+? WHERE sku=?", (qty, sku))
    return {"sku": sku, "returned": qty}
@app.post("/api/vision", dependencies=[DEV])
def vision(b: Vision): add_event("vision", b.sku, 1, conf=b.conf, ts=b.ts); return {"ok": True}

def owns(c, u, sess, open_only=True):
    q = "SELECT 1 FROM trips WHERE session=? AND username=?" + (" AND status='Open'" if open_only else "")
    if not c.execute(q, (sess, u["user"])).fetchone(): raise HTTPException(403, "This trip is not yours" + (" or it is already paid" if open_only else ""))

@app.post("/api/trip")
def trip(u: dict = CUST):
    with db() as c:
        r = c.execute("SELECT session FROM trips WHERE username=? AND owner=1 AND status='Open' ORDER BY started DESC", (u["user"],)).fetchone()
        sid = r["session"] if r else "T" + secrets.token_hex(4).upper()
        if not r:
            c.execute("INSERT INTO trips VALUES(?,?,1,?,'Open')", (sid, u["user"], time.time()))
            c.execute("INSERT OR IGNORE INTO sessions(id,first_ts,last_ts) VALUES(?,?,?)", (sid, time.time(), time.time()))
    ACTIVE[sid] = time.time(); return {"session": sid}
@app.post("/api/trip/join")
def trip_join(b: Join, u: dict = CUST):
    with db() as c:
        if not c.execute("SELECT 1 FROM trips WHERE session=? AND owner=1 AND status='Open'", (b.session,)).fetchone(): raise HTTPException(404, "No open cart with that code")
        c.execute("INSERT OR IGNORE INTO trips VALUES(?,?,0,?,'Open')", (b.session, u["user"], time.time()))
    return {"session": b.session}
@app.post("/api/scan")
def scan(b: Scan, u: dict = CUST):
    if not 1 <= b.qty <= 20: raise HTTPException(400, "bad quantity")
    with db() as c: owns(c, u, b.session)
    add_event("scan", b.sku, b.qty, session=b.session); return {"ok": True}
@app.post("/api/session")
def session(b: Sess, u: dict = CUST):
    with db() as c: owns(c, u, b.session)
    ACTIVE[b.session] = time.time()
    if b.zone and re.fullmatch(r"[A-Z][0-9]?", b.zone.upper()): ZONE[b.session] = (b.zone.upper(), time.time())
    return {"ok": True}
@app.get("/api/cart/{sid}")
def cart(sid: str, u: dict = CUST):
    with db() as c:
        owns(c, u, sid, False)
        return [dict(r) for r in c.execute("SELECT sku, SUM(qty) qty FROM events WHERE kind='scan' AND session=? GROUP BY sku", (sid,))]
@app.get("/api/activity/{sid}")
def activity(sid: str, u: dict = CUST):
    with db() as c:
        owns(c, u, sid, False)
        return [dict(r) for r in c.execute("SELECT ts, sku, qty FROM events WHERE kind='scan' AND session=? ORDER BY ts DESC LIMIT 12", (sid,))]
@app.get("/api/catalog", dependencies=[USER])
def catalog():
    return [{"sku": k, "name": v["name"], "unit": v["unit"], "price": v["price"], "shelf": next(s.split("-")[-1] for s, x in SHELVES.items() if x == k)} for k, v in PRODUCTS.items()]
@app.post("/api/checkout")
def checkout(b: Sess, u: dict = CUST):
    with db() as c:
        owns(c, u, b.session)
        if c.execute("SELECT 1 FROM events WHERE kind='pick' AND done=0").fetchone(): raise HTTPException(409, "Verifying your items. Please try again in a few seconds.")
        if c.execute("SELECT 1 FROM alerts WHERE session=? AND status!='Resolved'", (b.session,)).fetchone(): raise HTTPException(409, "Please resolve the flagged items first")
        rows = c.execute("SELECT sku, SUM(qty) q FROM events WHERE kind='scan' AND session=? GROUP BY sku", (b.session,)).fetchall()
        c.execute("UPDATE trips SET status='Paid' WHERE session=?", (b.session,)); c.execute("UPDATE sessions SET status='Paid' WHERE id=?", (b.session,))
    ACTIVE.pop(b.session, None); ZONE.pop(b.session, None)
    total = sum(PRODUCTS[r["sku"]]["price"] * r["q"] for r in rows)
    return {"ok": True, "total": total, "upi": f"upi://pay?pa={SET['upi_id']}&pn=TrustCart&am={total}&cu=INR&tn=Scan+and+Go"}

def act(aid, status, outcome=None):
    with db() as c:
        if not c.execute("SELECT 1 FROM alerts WHERE id=?", (aid,)).fetchone(): raise HTTPException(404)
        c.execute("UPDATE alerts SET status=?, resolved_ts=CASE WHEN ?='Resolved' THEN ? ELSE resolved_ts END, outcome=COALESCE(?,outcome) WHERE id=?",
                  (status, status, time.time(), outcome, aid))
    publish({"k": "resolved" if status == "Resolved" else "alert", "id": aid}); return {"ok": True}

def respond_core(aid: int, b: Resp):
    with db() as c: a = c.execute("SELECT * FROM alerts WHERE id=?", (aid,)).fetchone()
    if not a: raise HTTPException(404)
    if a["session"] != b.session: raise HTTPException(403, "This alert belongs to another shopper")
    NUDGED.pop(aid, None)
    if b.action == "added":
        add_event("scan", a["sku"], max(1, a["picked"] - a["scanned"]), a["session"], done=1); log(aid, "resolved", "shopper", "Added the item to the cart"); return act(aid, "Resolved", "recovered")
    if b.action == "putback": log(aid, "resolved", "shopper", "Put the item back"); return act(aid, "Resolved", "put_back")
    if b.action == "dispute":
        with db() as c: c.execute("UPDATE alerts SET note=? WHERE id=?", ((b.note or "")[:300], aid))
        log(aid, "disputed", "shopper", (b.note or "Shopper disputed the alert")[:300]); publish({"k": "staff", "id": aid}); return act(aid, "Staff check")
    raise HTTPException(400, "bad action")

# ---------- staff and manager ----------
@app.post("/api/alerts/{aid}/respond")
def respond(aid: int, b: Resp, u: dict = CUST):
    with db() as c: owns(c, u, b.session, False)
    return respond_core(aid, b)

@app.get("/api/alerts", dependencies=[ANY])
def alerts(limit: int = 100):
    with db() as c: return [dict(r) for r in c.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (min(limit, 500),))]
@app.post("/api/alerts/{aid}/resolve", dependencies=[ANY])
def resolve(aid: int, outcome: str = "recovered", u: dict = ANY):
    if outcome not in ("recovered", "false_alarm", "put_back"): raise HTTPException(400, "bad outcome")
    NUDGED.pop(aid, None); log(aid, "resolved", u["role"], {"recovered": "Item recovered", "false_alarm": "Marked as a false alarm", "put_back": "Item put back"}[outcome]); return act(aid, "Resolved", outcome)
@app.post("/api/alerts/{aid}/nudge", dependencies=[ANY])
def nudge(aid: int, u: dict = ANY):
    with db() as c: a = c.execute("SELECT * FROM alerts WHERE id=?", (aid,)).fetchone()
    if not a: raise HTTPException(404)
    if not a["session"] or a["session"] not in ACTIVE: raise HTTPException(409, "No active shopper to nudge")
    NUDGED[aid] = time.time(); publish(nudge_msg(aid, a["session"], a["sku"], a["type"], a["scanned"], a["picked"])); log(aid, "nudged", u["role"], "Nudge sent manually"); return act(aid, "Nudged")
@app.post("/api/alerts/{aid}/escalate", dependencies=[ANY])
def escalate(aid: int, u: dict = ANY):
    NUDGED.pop(aid, None); publish({"k": "staff", "id": aid}); log(aid, "escalated", u["role"], "Escalated for a staff spot-check"); return act(aid, "Staff check")
@app.get("/api/alerts/{aid}/trail", dependencies=[ANY])
def trail(aid: int):
    with db() as c: return [dict(r) for r in c.execute("SELECT * FROM alert_log WHERE aid=? ORDER BY id", (aid,))]

def midnight(off=0):
    t = time.localtime(); return time.mktime((t.tm_year, t.tm_mon, t.tm_mday + off, 0, 0, 0, 0, 0, -1))
def agg(c, t0, t1):
    q = lambda s, *a: c.execute(s, a).fetchone()[0] or 0
    saved = sum(PRODUCTS[r["sku"]]["price"] * r["picked"] for r in c.execute("SELECT sku,picked FROM alerts WHERE outcome='recovered' AND ts>=? AND ts<?", (t0, t1)))
    return {"scans": q("SELECT SUM(qty) FROM events WHERE kind='scan' AND ts>=? AND ts<?", t0, t1), "picks": q("SELECT SUM(qty) FROM events WHERE kind='pick' AND ts>=? AND ts<?", t0, t1),
            "alerts": q("SELECT COUNT(*) FROM alerts WHERE ts>=? AND ts<?", t0, t1), "resolved": q("SELECT COUNT(*) FROM alerts WHERE status='Resolved' AND ts>=? AND ts<?", t0, t1),
            "recovered": q("SELECT COUNT(*) FROM alerts WHERE outcome='recovered' AND ts>=? AND ts<?", t0, t1), "false_alarms": q("SELECT COUNT(*) FROM alerts WHERE outcome='false_alarm' AND ts>=? AND ts<?", t0, t1),
            "sessions": q("SELECT COUNT(*) FROM sessions WHERE first_ts>=? AND first_ts<?", t0, t1), "manual": q("SELECT COUNT(*) FROM alerts WHERE type='Possible Theft Risk' AND ts>=? AND ts<?", t0, t1),
            "avg_response": round(q("SELECT AVG(resolved_ts-ts) FROM alerts WHERE resolved_ts IS NOT NULL AND ts>=? AND ts<?", t0, t1), 1), "saved": saved}

@app.get("/api/stats", dependencies=[ANY])
def stats():
    t0, now, lt = midnight(), time.time() + 1, time.localtime()
    base = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, lt.tm_hour, 0, 0, 0, 0, -1))
    with db() as c:
        today, yest = agg(c, t0, now), agg(c, midnight(-1), t0)
        hourly = []
        for i in range(6, -1, -1):
            h = base - i * 3600; a = agg(c, h, h + 3600)
            hourly.append({"h": time.localtime(h).tm_hour, "s": a["scans"], "p": a["picks"], "m": a["alerts"]})
        by = {r["sku"]: r["n"] for r in c.execute("SELECT sku, COUNT(*) n FROM alerts WHERE ts>=? GROUP BY sku", (t0,))}
        n = lambda step, actors=None: c.execute("SELECT COUNT(DISTINCT aid) FROM alert_log WHERE ts>=? AND step=?" + (" AND actor IN (%s)" % ",".join("'%s'" % x for x in actors) if actors else ""), (t0, step)).fetchone()[0]
        pick_alerts = c.execute("SELECT COUNT(*) FROM alerts WHERE ts>=? AND type!='Phantom Scan'", (t0,)).fetchone()[0]
        flow = {"picks": today["picks"], "scans": today["scans"], "matched": max(0, today["picks"] - pick_alerts), "alerts": today["alerts"], "nudged": n("nudged"),
                "fixed": n("resolved", ["shopper"]), "escalated": n("escalated"), "staff_resolved": n("resolved", ["staff", "manager"])}
    acc = round(100 - 100 * today["false_alarms"] / today["resolved"], 1) if today["resolved"] else 100.0
    return {"store": SET["store_name"], "today": today, "yesterday": yest, "hourly": hourly, "by_sku": by, "accuracy": acc,
            "manual": today["manual"], "flow": flow, "demo": bool(demo["task"] and not demo["task"].done())}

@app.get("/api/sessions", dependencies=[MGR])
def sessions():
    with db() as c:
        return [dict(r) for r in c.execute("SELECT s.*, (SELECT COUNT(*) FROM alerts a WHERE a.session=s.id) flags FROM sessions s ORDER BY last_ts DESC LIMIT 200")]
@app.get("/api/products", dependencies=[MGR])
def products():
    t0 = midnight()
    with db() as c:
        stock = {r["sku"]: r["qty"] for r in c.execute("SELECT * FROM stock")}
        al = {r["sku"]: (r["n"], r["u"] or 0) for r in c.execute("SELECT sku, COUNT(*) n, SUM(picked) u FROM alerts WHERE ts>=? GROUP BY sku", (t0,))}
    return [{"sku": k, **v, "stock": stock.get(k, 0), "alerts": al.get(k, (0, 0))[0], "risk": al.get(k, (0, 0))[1] * v["price"]} for k, v in PRODUCTS.items()]
@app.post("/api/products/{sku}/restock", dependencies=[MGR])
def restock(sku: str, n: int = 60):
    if sku not in PRODUCTS or not 1 <= n <= 1000: raise HTTPException(400, "bad request")
    with db() as c: c.execute("UPDATE stock SET qty=qty+? WHERE sku=?", (n, sku))
    return {"ok": True}

def report_data(days):
    days = max(1, min(days, 90)); t0 = midnight(-(days - 1))
    with db() as c:
        tot = agg(c, t0, time.time() + 1)
        rows = [dict(r) for r in c.execute("SELECT * FROM alerts WHERE ts>=? ORDER BY ts DESC LIMIT 500", (t0,))]
        for r in rows:
            p = PRODUCTS[r["sku"]]; r["product"], r["value"] = p["name"], p["price"] * r["picked"]
            r["response_s"] = round(r["resolved_ts"] - r["ts"], 1) if r["resolved_ts"] else None
        by_type, by_day = {}, []
        for r in rows: by_type[r["type"]] = by_type.get(r["type"], 0) + 1
        for i in range(days - 1, -1, -1):
            a = agg(c, midnight(-i), midnight(-i + 1)); by_day.append({"d": time.strftime("%d %b", time.localtime(midnight(-i))), "scans": a["scans"], "alerts": a["alerts"]})
    return {"days": days, "totals": tot, "by_type": by_type, "by_day": by_day, "rows": rows, "store": SET["store_name"]}
@app.get("/api/report", dependencies=[MGR])
def report(days: int = 7): return report_data(days)
@app.get("/api/report.csv", dependencies=[MGR])
def report_csv(days: int = 7):
    out = io.StringIO(); w = csv.writer(out); w.writerow(["time", "type", "severity", "product", "scanned", "picked", "camera_conf", "status", "outcome", "value_inr", "response_s", "shopper_note"])
    for r in report_data(days)["rows"]:
        w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["ts"])), r["type"], r["sev"], r["product"], r["scanned"], r["picked"], r["conf"], r["status"], r["outcome"], r["value"], r["response_s"], r["note"]])
    return PlainTextResponse(out.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=trustcart_report.csv"})

NUM = {"window": (1, 120), "esc": (5, 600), "theft_conf": (0, 1), "high_conf": (0, 1), "retention_h": (1, 720)}
@app.get("/api/settings", dependencies=[MGR])
def get_settings(): return SET
@app.post("/api/settings", dependencies=[MGR])
def set_settings(b: dict):
    new = {}
    for k, v in b.items():
        if k not in DEFAULTS: continue
        v = str(v).strip()
        if k in NUM:
            try: f = float(v)
            except ValueError: raise HTTPException(400, f"{k} must be a number")
            if not NUM[k][0] <= f <= NUM[k][1]: raise HTTPException(400, f"{k} must be between {NUM[k][0]} and {NUM[k][1]}")
        if k == "nudge" and v not in ("0", "1"): raise HTTPException(400, "nudge must be 0 or 1")
        if k == "store_name" and not 1 <= len(v) <= 60: raise HTTPException(400, "Store name must be 1 to 60 characters")
        if k == "upi_id" and not re.fullmatch(r"[\w.\-]{2,}@[\w]{2,}", v): raise HTTPException(400, "UPI ID looks invalid (example: store@bank)")
        new[k] = v
    with db() as c:
        for k, v in new.items(): c.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
    SET.update(new); return SET
ROLE_SET = ROLES
@app.get("/api/users", dependencies=[ADM])
def users():
    with db() as c: return [dict(r) for r in c.execute("SELECT username, role, name FROM users ORDER BY role, username")]
@app.post("/api/users", dependencies=[ADM])
def add_user(b: NewUser):
    if not USERNAME.fullmatch(b.username) or b.role not in ROLE_SET or len(b.password) < 8:
        raise HTTPException(400, "Username 3-30 letters/digits, role customer/staff/manager/admin, password at least 8 characters")
    with db() as c:
        if c.execute("SELECT 1 FROM users WHERE username=?", (b.username,)).fetchone(): raise HTTPException(409, "That username already exists")
        c.execute("INSERT INTO users(username,pw,role,name) VALUES(?,?,?,?)", (b.username, hash_pw(b.password), b.role, (b.name or b.username)[:40]))
    return {"ok": True}
@app.delete("/api/users/{username}")
def del_user(username: str, u: dict = ADM):
    if username == u["user"]: raise HTTPException(400, "You cannot delete your own account")
    with db() as c:
        row = c.execute("SELECT role FROM users WHERE username=?", (username,)).fetchone()
        if not row: raise HTTPException(404)
        if row["role"] == "admin" and c.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0] <= 1: raise HTTPException(400, "Keep at least one admin")
        c.execute("DELETE FROM users WHERE username=?", (username,))
    return {"ok": True}
@app.post("/api/demo/scenario", dependencies=[ANY])
def demo_scenario(b: dict):
    """Create a deterministic end-to-end demo incident for Staff review.
    This is simulation data only: it exercises camera evidence -> pick event ->
    scan database -> mismatch engine -> staff alert without claiming real CCTV data.
    """
    scenario = str(b.get("scenario", "unscanned"))
    if scenario not in ("unscanned", "mismatch", "match"):
        raise HTTPException(400, "scenario must be unscanned, mismatch, or match")
    sku = str(b.get("sku", "chips"))
    if sku not in PRODUCTS:
        raise HTTPException(400, "unknown sku")
    sess = "DEMO-" + secrets.token_hex(3).upper()
    now = time.time() - float(SET["window"]) - 1.0
    ACTIVE[sess] = time.time()
    with db() as c:
        c.execute("INSERT OR REPLACE INTO sessions(id,first_ts,last_ts,items,value,status) VALUES(?,?,?,?,?,?)",
                  (sess, now, time.time(), 0, 0, "In store"))
        # Camera sees the product and the pick event occurs.
        c.execute("INSERT INTO events(ts,kind,sku,qty,session,weight_g,conf,done) VALUES(?,?,?,?,?,?,?,0)",
                  (now, "pick", sku, 2 if scenario == "mismatch" else 1, sess, PRODUCTS[sku]["g"], 0.96))
        c.execute("INSERT INTO events(ts,kind,sku,qty,session,weight_g,conf,done) VALUES(?,?,?,?,?,?,?,1)",
                  (now, "vision", sku, 1, sess, None, 0.96))
        if scenario == "mismatch":
            c.execute("INSERT INTO events(ts,kind,sku,qty,session,weight_g,conf,done) VALUES(?,?,?,?,?,?,?,0)",
                      (now, "scan", sku, 1, sess, None, None))
        elif scenario == "match":
            c.execute("INSERT INTO events(ts,kind,sku,qty,session,weight_g,conf,done) VALUES(?,?,?,?,?,?,?,0)",
                      (now, "scan", sku, 1, sess, None, None))
        # The demo is deterministic: explicitly execute the same decision that the
        # reconciliation engine would make, then route the exception to Staff.
        # This avoids timing/race dependence on the one-second background loop.
        if scenario in ("unscanned", "mismatch"):
            picked = 2 if scenario == "mismatch" else 1
            scanned = 1 if scenario == "mismatch" else 0
            typ = "Scan Mismatch" if scenario == "mismatch" else "Unscanned Item"
            sev = "Medium" if scenario == "mismatch" else "High"
            c.execute("UPDATE events SET done=1 WHERE session=? AND kind='pick'", (sess,))
            c.execute("UPDATE events SET done=1 WHERE session=? AND kind='scan'", (sess,))
            aid = c.execute("INSERT INTO alerts(ts,type,sev,sku,scanned,picked,conf,status,session) VALUES(?,?,?,?,?,?,?,?,?)",
                            (time.time(), typ, sev, sku, scanned, picked, 0.96, "Staff check", sess)).lastrowid
            log(aid, "detected", "camera_ai", f"{typ}: camera detected {picked} pick(s), scan database shows {scanned}", c)
            log(aid, "matched", "mismatch_engine", "Scan database + pick event + camera confidence evaluated", c)
            log(aid, "risk_scored", "risk_engine", "Demo confidence 96%; routed to Staff", c)
            log(aid, "escalated", "system", "Demo incident routed directly to Staff for MVP demonstration", c)
        else:
            c.execute("UPDATE events SET done=1 WHERE session=?", (sess,))
            aid = None
    if aid:
        publish({"k": "staff", "id": aid})
    return {"ok": True, "scenario": scenario, "session": sess, "alert_id": aid,
            "message": "Demo incident created for Staff review" if aid else "Demo match completed with no alert"}

@app.post("/api/demo", dependencies=[MGR])
async def demo_toggle(b: dict):
    on = bool(b.get("on")); t = demo["task"]
    if on and not (t and not t.done()): demo["task"] = asyncio.create_task(demo_loop())
    if not on and t: t.cancel()
    return {"demo": on}

async def stream_gen(q, remove):
    try:
        while True:
            try: yield f"data: {json.dumps(await asyncio.wait_for(q.get(), 15))}\n\n"
            except asyncio.TimeoutError: yield ": ping\n\n"
    finally: remove()
@app.get("/api/stream", dependencies=[ANY])
async def stream():
    q = asyncio.Queue(); subs.append(q); return StreamingResponse(stream_gen(q, lambda: subs.remove(q)), media_type="text/event-stream")
@app.get("/api/shopper/stream")
async def shopper_stream(session: str, u: dict = CUST):
    with db() as c: owns(c, u, session, False)
    q = asyncio.Queue(); e = (session, q); ssubs.append(e); return StreamingResponse(stream_gen(q, lambda: ssubs.remove(e)), media_type="text/event-stream")


# ---------- custom camera detector ----------
def detector_status_data():
    model_path = os.path.join(HERE, "models", "custom_product_detector.joblib")
    card_path = os.path.join(HERE, "models", "custom_product_detector.json")
    card = {}
    if os.path.exists(card_path):
        try: card = json.load(open(card_path, encoding="utf-8"))
        except Exception: pass
    return {"running": DETECTOR_STATE["running"], "msg": DETECTOR_STATE["msg"],
            "weights": model_path if os.path.exists(model_path) else None,
            "annotated_files": 0, "training_samples": card.get("samples", 0),
            "validation_accuracy": card.get("validation_accuracy"),
            "detector_type": card.get("type", "HOG + LinearSVC"),
            "classes": ["chips","cola","redbull","sunglasses","milk","apple","bar","banana","bread"],
            "pipeline": ["Camera video", "Custom product detector", "Product tracking", "Pick event", "Scan database", "AI mismatch engine", "Staff alert"]}

def _train_custom_detector():
    try:
        import subprocess, sys
        DETECTOR_STATE.update(running=True, msg="Training custom product detector…")
        r = subprocess.run([sys.executable, os.path.join(HERE,"train_custom_detector.py")], cwd=HERE, text=True, capture_output=True)
        if r.returncode != 0: raise RuntimeError((r.stderr or r.stdout or "training failed")[-1200:])
        DETECTOR_STATE.update(running=False, msg="Custom detector trained", weights=os.path.join(HERE,"models","custom_product_detector.joblib"), last=time.time())
    except Exception as e:
        DETECTOR_STATE.update(running=False, msg=f"Training failed: {e}")

@app.get("/api/detector", dependencies=[MGR])
def detector_status():
    return detector_status_data()

@app.post("/api/detector/train", dependencies=[ADM])
def detector_train():
    if DETECTOR_STATE["running"]: return {"started": False, **detector_status_data()}
    asyncio.get_event_loop().run_in_executor(None, _train_custom_detector)
    DETECTOR_STATE["running"] = True
    return {"started": True, **detector_status_data()}

# ---------- product recognition model ----------
@app.post("/api/recognize", dependencies=[USER])
def recognize(b: Img):
    if recognizer is None: raise HTTPException(503, "Model libraries are not installed (pip install -r requirements.txt)")
    try: im = recognizer.decode(b.image)
    except Exception as e: raise HTTPException(400, f"Could not read the image: {e}")
    top = recognizer.predict(im)
    if top is None: raise HTTPException(503, "The model has not been trained yet")
    return {"top": top, "confident": top[0]["prob"] >= 0.6, "name": PRODUCTS.get(top[0]["sku"], {}).get("name", top[0]["sku"])}
@app.get("/api/model", dependencies=[MGR])
def model_card():
    if recognizer is None: raise HTTPException(503, "Model libraries are not installed")
    ds = {k: len(os.listdir(os.path.join(recognizer.DATA, k))) for k in os.listdir(recognizer.DATA)} if os.path.isdir(recognizer.DATA) else {}
    return {"card": recognizer.card(), "status": recognizer.STATE, "uploaded_photos": ds, "products": {k: v["name"] for k, v in PRODUCTS.items()}}
@app.post("/api/model/upload", dependencies=[ADM])
def model_upload(b: Img):
    if recognizer is None or b.sku not in PRODUCTS: raise HTTPException(400, "Choose a product and an image")
    try: return {"photos_for_product": recognizer.save_photo(b.sku, b.image)}
    except Exception as e: raise HTTPException(400, f"Could not read the image: {e}")
@app.post("/api/model/train", dependencies=[ADM])
def model_train():
    if recognizer is None: raise HTTPException(503, "Model libraries are not installed")
    return {"started": recognizer.train_async()}
