"""Sends realistic shopper behaviour to the REAL endpoints (shelf, vision, scan), so you can demo or test
the server without hardware.  Usage: python simulate_store.py [http://localhost:8000] [rounds]"""
import os, random, sys, time, requests
URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 20
SH = {"chips": ("shelf-A1", 50), "cola": ("shelf-A2", 520), "rb": ("shelf-A3", 270), "sun": ("shelf-B1", 40),
      "milk": ("shelf-B2", 1030), "apple": ("shelf-C1", 180), "bar": ("shelf-C2", 60)}
KEY = {"X-API-Key": os.getenv("DEVICE_API_KEY", "")}   # set DEVICE_API_KEY if the server has one
post = lambda p, j: requests.post(URL + p, json=j, headers=KEY, timeout=5)
def lift(sku, n=1, conf=0.93):
    post("/api/shelf", {"shelf_id": SH[sku][0], "delta_g": -SH[sku][1] * n})
    if conf: post("/api/vision", {"sku": sku, "conf": conf})
def scan(sku, n=1): post("/api/scan", {"sku": sku, "qty": n, "session": "S-sim"})
for i in range(N):
    sku, r = random.choice(list(SH)), random.random()
    if r < .70: lift(sku); scan(sku)                                   # honest shopper
    elif r < .80: lift(sku)                                            # forgot to scan
    elif r < .87: lift(sku, 2); scan(sku, 1)                           # scanned 1, took 2
    elif r < .92: lift(sku, conf=0.4)                                  # unclear camera view
    elif r < .96: scan(sku)                                            # scan without lifting
    else:
        other = random.choice([s for s in SH if abs(SH[s][1] - SH[sku][1]) / SH[sku][1] > .35]); lift(sku); scan(other)
    time.sleep(random.uniform(.5, 2))
