#!/usr/bin/env python3
"""TrustCart digital twin: a virtual store that drives the REAL server through the same HTTP endpoints the hardware uses.
  Virtual ESP32 + HX711 shelves (same settle logic and thresholds as shelf_node.ino, with sensor noise)
  Virtual cameras (misses some pickups, noisy confidence), virtual shopper phones (scan, answer nudges, checkout),
  a virtual staff handheld, and ground-truth scoring of what the system decided.
Usage: python digital_twin.py --server http://localhost:8000 --admin-password ... --shoppers 3 --jobs 6 [--key DEVICE_API_KEY]
NOTE: results measure the software rules under the noise assumed below. They are NOT real-world accuracy figures."""
import argparse, json, os, random, statistics, threading, time
import requests

G = {"chips": 50, "cola": 520, "rb": 270, "sun": 40, "milk": 1030, "apple": 180, "bar": 60, "banana": 1000, "bread": 400}
SHELF = {"chips": "shelf-A1", "cola": "shelf-A2", "rb": "shelf-A3", "sun": "shelf-B1", "milk": "shelf-B2", "apple": "shelf-C1", "bar": "shelf-C2", "banana": "shelf-C3", "bread": "shelf-D1"}
aisle = lambda sku: SHELF[sku].split("-")[1]          # shelf check-in code, e.g. "A1" (a QR code on each shelf)
EXPECT = {"honest": None, "putback": None, "forgot": "Unscanned Item", "mismatch": "Scan Mismatch", "theft": "Possible Theft Risk", "swap": "Barcode Swap", "phantom": "Phantom Scan"}
MIX = {"honest": .40, "forgot": .16, "mismatch": .10, "theft": .08, "swap": .09, "phantom": .09, "putback": .08}
NOISE_G, CAM_MISS = 2.0, 0.08      # assumed sensor noise (grams, per reading) and chance the camera misses a pickup

class Shelf(threading.Thread):
    """Virtual ESP32 + load cell, mirroring shelf_node.ino (MIN_DELTA 15 g, settle 3 g for 0.7 s)."""
    def __init__(s, tw, sku):
        super().__init__(daemon=True); s.tw, s.sku, s.unit = tw, sku, G[sku]; s.true = s.unit * 60.0; s.base = s.last = s.true; s.settled = time.time()
    def read(s):
        return statistics.fmean(s.true + random.gauss(0, NOISE_G) + (random.choice([-1, 1]) * random.uniform(20, 40) if random.random() < .01 else 0) for _ in range(3))
    def run(s):
        s.base = s.last = s.read()
        while not s.tw.stop.is_set():
            w, now = s.read(), time.time()
            if abs(w - s.last) > 3: s.settled, s.last = now, w
            if now - s.settled > .7 and abs(s.last - s.base) >= 15:
                d, s.base = s.last - s.base, s.last; s.tw.post("/api/shelf", {"shelf_id": SHELF[s.sku], "delta_g": round(d, 1)}, True)
            time.sleep(.1)
    def move(s, n):                                   # n > 0 removes items, n < 0 puts them back (hand wobble included)
        target = s.true - n * s.unit
        for i in range(6): s.true += (target - s.true) / (6 - i) + random.uniform(-6, 6) * (i < 5); time.sleep(.08)
        s.true = target

class Twin:
    def __init__(s, url, key, adm_pw, stf_pw, seed):
        s.url, s.key, s.rng = url.rstrip("/"), key, random.Random(seed); random.seed(seed)
        s.adm, s.stf = requests.Session(), requests.Session()
        for ses, u, p in ((s.adm, "admin", adm_pw), (s.stf, "staff", stf_pw)):
            r = ses.post(s.url + "/api/login", json={"username": u, "password": p}); r.raise_for_status()
        cfg = s.adm.get(s.url + "/api/settings").json(); s.W, s.ESC = float(cfg["window"]), float(cfg["esc"])
        s.stop, s.lock, s.busy, s.jobs, s.checkouts = threading.Event(), threading.Lock(), {}, [], []
        s.shelves = {k: Shelf(s, k) for k in G}
        for sh in s.shelves.values(): sh.start()
        threading.Thread(target=s.staff, daemon=True).start()
    def post(s, path, body, device=False):
        try: return requests.post(s.url + path, json=body, headers={"X-API-Key": s.key} if device else {}, timeout=5)
        except requests.RequestException: return None
    def camera(s, sku):                               # virtual edge camera
        if random.random() < CAM_MISS: return
        time.sleep(random.uniform(.2, .7)); s.post("/api/vision", {"sku": sku, "conf": round(min(.99, max(.5, random.gauss(.9, .06))), 2)}, True)
    def reserve(s, n, hold):
        while not s.stop.is_set():
            with s.lock:
                free = [k for k in G if s.busy.get(k, 0) < time.time()]
                if len(free) >= n:
                    pick = s.rng.sample(free, n)
                    for k in pick: s.busy[k] = time.time() + hold
                    return pick
            time.sleep(.5)
    def staff(s):                                     # virtual staff handheld: spot-checks whatever reaches the queue
        while not s.stop.is_set():
            try:
                for a in s.stf.get(s.url + "/api/alerts?limit=100").json():
                    if a["status"] == "Staff check": time.sleep(random.uniform(1, 2.5)); s.stf.post(s.url + f"/api/alerts/{a['id']}/resolve?outcome=recovered")
            except Exception: pass
            time.sleep(1)

def shopper(tw, idx, n_jobs):
    cs = requests.Session(); name = f"tw{idx}x{random.randint(1000, 9999)}"          # each virtual shopper is a real customer account
    cs.post(tw.url + "/api/register", json={"username": name, "password": "password123", "name": name}).raise_for_status()
    sid, W = cs.post(tw.url + "/api/trip").json()["session"], tw.W
    def cp(path, body):
        try: return cs.post(tw.url + path, json=body, timeout=5)
        except requests.RequestException: return None
    reg = lambda z=None: cp("/api/session", {"session": sid, "zone": z})
    def respond(d):                                   # virtual shopper phone answering a nudge
        r = random.random(); act = "added" if r < .75 else "putback" if r < .90 else None
        if act: cp(f"/api/alerts/{d['id']}/respond", {"action": act, "session": sid})
    def listen():
        try:
            with cs.get(tw.url + "/api/shopper/stream", params={"session": sid}, stream=True, timeout=None) as r:
                for line in r.iter_lines():
                    if line.startswith(b"data:") and json.loads(line[5:]).get("k") == "nudge": threading.Timer(random.uniform(1, 2.5), respond, [json.loads(line[5:])]).start()
        except Exception: pass
    threading.Thread(target=listen, daemon=True).start(); reg()
    for _ in range(n_jobs):
        sc = random.choices(list(MIX), list(MIX.values()))[0]
        ks = tw.reserve(2 if sc == "swap" else 1, W * 4 + 12)
        if not ks: return
        sku, other = ks[0], ks[-1]
        if sc == "swap" and abs(G[sku] - G[other]) / G[sku] <= .35: sc, other = "mismatch", sku
        job = {"sid": sid, "sku": sku, "sc": sc, "exp": EXPECT[sc], "t0": time.time()}; reg(aisle(sku)); time.sleep(.3)
        sh, pause = tw.shelves[sku], lambda: time.sleep(random.uniform(.3 * W, .8 * W))
        scan = lambda k, q=1: cp("/api/scan", {"sku": k, "qty": q, "session": sid})
        if sc == "phantom": scan(sku)
        else:
            if sc != "theft": threading.Thread(target=tw.camera, args=(sku,), daemon=True).start()
            sh.move(2 if sc == "mismatch" else 1); pause()
            if sc == "honest": scan(sku)
            elif sc == "mismatch": scan(sku, 1)
            elif sc == "swap": scan(other)
            elif sc == "putback": time.sleep(.4); sh.move(-1)
        job["t_end"] = time.time(); tw.jobs.append(job); time.sleep(W * 1.5 + 2)
    for _ in range(25):                               # checkout (blocked while items are being verified or flagged)
        r = cp("/api/checkout", {"session": sid})
        if r is not None and r.status_code == 200: tw.checkouts.append((sid, True)); return
        time.sleep(2)
    tw.checkouts.append((sid, False))

def score(tw):
    time.sleep(W2 := 2 * tw.W + tw.ESC * 0 + 4)
    al = tw.adm.get(tw.url + "/api/alerts?limit=500").json(); rows, lat = {}, []; routed = [0, 0]
    for j in tw.jobs:
        c = [a for a in al if a["sku"] == j["sku"] and j["t0"] - 1 <= a["ts"] <= j["t_end"] + 3 * tw.W + 6]
        r = rows.setdefault(j["sc"], {"n": 0, "ok": 0, "other": {}})
        r["n"] += 1
        if j["exp"] is None: r["ok"] += (not c); [r["other"].__setitem__(a["type"], r["other"].get(a["type"], 0) + 1) for a in c]
        else:
            hit = [a for a in c if a["type"] == j["exp"]]; r["ok"] += bool(hit)
            if not hit: k = c[0]["type"] if c else "no alert"; r["other"][k] = r["other"].get(k, 0) + 1
            else:
                if j["sc"] != "phantom": lat.append(hit[0]["ts"] - j["t_end"])
                routed[1] += 1; routed[0] += (hit[0]["session"] == j["sid"])
    if os.getenv("TWIN_DEBUG"):
        for j in tw.jobs:
            near = [(a["type"], a["sku"], round(a["ts"] - j["t0"], 1)) for a in al if j["t0"] - 1 <= a["ts"] <= j["t_end"] + 3 * tw.W + 6]
            print("  job", j["sc"], j["sku"], j["sid"][-4:], "expected", j["exp"], "| alerts in window:", near)
    st = tw.adm.get(tw.url + "/api/stats").json()
    print(f"\nDIGITAL TWIN RESULTS  ({len(tw.jobs)} shopper actions, window {tw.W:g}s, camera miss rate {CAM_MISS:.0%}, sensor noise {NOISE_G:g} g)")
    print(f"{'scenario':10} {'actions':>7} {'correct':>8}  what else happened")
    for k, r in sorted(rows.items()): print(f"{k:10} {r['n']:>7} {r['ok']:>5}/{r['n']:<2}  {r['other'] or ''}")
    good = [r for k, r in rows.items() if EXPECT[k] is None]; bad = [r for k, r in rows.items() if EXPECT[k]]
    fa = sum(r["n"] - r["ok"] for r in good); n_good = sum(r["n"] for r in good); tp = sum(r["ok"] for r in bad); n_bad = sum(r["n"] for r in bad)
    print(f"false alarms on honest/put-back baskets: {fa}/{n_good}   correct verdict on problem cases: {tp}/{n_bad} ({tp / max(1, n_bad):.0%})")
    print(f"nudge reached the right shopper: {routed[0]}/{routed[1]}   median detection delay: {statistics.median(lat):.1f}s" if lat else "no latency data")
    print(f"checkouts completed: {sum(ok for _, ok in tw.checkouts)}/{len(tw.checkouts)}   alerts today: {st['today']['alerts']}   process flow: {st['flow']}")
    json.dump({"rows": rows, "flow": st["flow"], "false_alarms": fa, "honest_cases": n_good, "correct_problem_cases": tp, "problem_cases": n_bad}, open("twin_report.json", "w"), indent=1)

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--server", default="http://localhost:8000"); ap.add_argument("--key", default=os.getenv("DEVICE_API_KEY", ""))
    ap.add_argument("--admin-password", default=os.getenv("ADMIN_PASSWORD", "")); ap.add_argument("--staff-password", default=os.getenv("STAFF_PASSWORD", ""))
    ap.add_argument("--shoppers", type=int, default=3); ap.add_argument("--jobs", type=int, default=6); ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args(); tw = Twin(a.server, a.key, a.admin_password, a.staff_password, a.seed); time.sleep(1.5)
    ts = [threading.Thread(target=shopper, args=(tw, i + 1, a.jobs)) for i in range(a.shoppers)]
    [t.start() for t in ts]; [t.join() for t in ts]; score(tw); tw.stop.set()
