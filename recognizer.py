"""Product recognition model: features + training + inference.
Features: HSV colour histogram + 4x4 mean colour grid + gradient-orientation histogram (shape).  Classifier: scaled logistic regression.
Train:  python train_model.py          (uses seed_images/ plus any photos in dataset/<sku>/ uploaded from the admin screen)
Honest note: the seed photos come from design mock-ups. Real accuracy needs real photos of your own products, taken in your shelves' lighting."""
import base64, io, json, os, threading, time
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
SEED = os.path.join(HERE, "seed_images")
DATA = os.getenv("DATASET_DIR", os.path.join(HERE, "dataset"))
MODELS = os.getenv("MODEL_DIR", os.path.join(HERE, "models"))
SZ, N_AUG = 64, 60
STATE = {"running": False, "msg": "idle", "last": None}
_model = {"clf": None, "card": None}

def feats(img):
    im = img.convert("RGB").resize((SZ, SZ))
    hsv = np.asarray(im.convert("HSV")).reshape(-1, 3)
    h = np.histogramdd(hsv, bins=(12, 4, 4), range=((0, 256),) * 3)[0].ravel(); h = h / h.sum()
    grid = (np.asarray(im, dtype=np.float32) / 255).reshape(4, 16, 4, 16, 3).mean((1, 3)).ravel()
    g = np.asarray(im.convert("L"), dtype=np.float32); gy, gx = np.gradient(g)
    mag, ang = np.hypot(gx, gy), ((np.arctan2(gy, gx) + np.pi) / (2 * np.pi) * 8).astype(int) % 8
    o = np.array([np.bincount(ang[i*16:(i+1)*16, j*16:(j+1)*16].ravel(), weights=mag[i*16:(i+1)*16, j*16:(j+1)*16].ravel(), minlength=8)
                  for i in range(4) for j in range(4)]).ravel(); o = o / (o.sum() + 1e-6)
    return np.concatenate([h, grid, o]).astype(np.float32)

def augment(img, rng):
    w, h = img.size; s = rng.uniform(.75, 1); cw, ch = int(w * s), int(h * s); x, y = rng.integers(0, w - cw + 1), rng.integers(0, h - ch + 1)
    im = img.crop((x, y, x + cw, y + ch)).rotate(rng.uniform(-15, 15), resample=Image.BILINEAR, fillcolor=(235, 235, 235)).resize((SZ, SZ))
    im = ImageEnhance.Brightness(im).enhance(rng.uniform(.7, 1.3)); im = ImageEnhance.Contrast(im).enhance(rng.uniform(.75, 1.25))
    im = ImageEnhance.Color(im).enhance(rng.uniform(.75, 1.25))
    return im.filter(ImageFilter.GaussianBlur(rng.uniform(0, 1.2))) if rng.random() < .4 else im

def collect():
    base = {}
    for root in (SEED, DATA):
        if not os.path.isdir(root): continue
        for sku in sorted(os.listdir(root)):
            d = os.path.join(root, sku)
            if sku == "heldout" or not os.path.isdir(d): continue
            for f in sorted(os.listdir(d)):
                if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                    try: base.setdefault(sku, []).append(Image.open(os.path.join(d, f)).convert("RGB"))
                    except Exception: pass
    return base

def train():
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    import joblib
    STATE.update(running=True, msg="collecting images")
    try:
        base, rng = collect(), np.random.default_rng(0)
        if len(base) < 2: raise ValueError("need images for at least 2 products")
        X, y, X_val, y_val = [], [], [], []
        for sku, imgs in base.items():
            for k, im in enumerate(imgs):
                X.append(feats(im)); y.append(sku)
                for i in range(N_AUG):
                    (X_val, y_val)[0].append(feats(augment(im, rng))) if i % 6 == 0 else X.append(feats(augment(im, rng)))
                    (y_val if i % 6 == 0 else y).append(sku)
        STATE["msg"] = f"training on {len(X)} samples"
        clf = make_pipeline(StandardScaler(), LogisticRegression(max_iter=4000, C=2.0)).fit(np.array(X), y)
        val = float(np.mean(clf.predict(np.array(X_val)) == np.array(y_val)))
        held, hits = [], 0
        hd = os.path.join(SEED, "heldout")
        if os.path.isdir(hd):
            for f in sorted(os.listdir(hd)):
                sku, p = f.split(".")[0], clf.predict_proba([feats(Image.open(os.path.join(hd, f)).convert("RGB"))])[0]
                top = clf.classes_[int(p.argmax())]; hits += top == sku; held.append({"truth": sku, "predicted": top, "confidence": round(float(p.max()), 2)})
        card = {"classes": list(clf.classes_), "photos": {k: len(v) for k, v in base.items()}, "n_train": len(X), "trained_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "augmented_val_accuracy": round(val, 3), "cross_render_test": held, "cross_render_accuracy": round(hits / len(held), 2) if held else None,
                "note": "Cross-render test = product shots from a different design mock-up that the model never saw. It has only a handful of samples."}
        os.makedirs(MODELS, exist_ok=True); joblib.dump(clf, os.path.join(MODELS, "product_model.joblib")); json.dump(card, open(os.path.join(MODELS, "product_model.json"), "w"), indent=1)
        _model.update(clf=clf, card=card); STATE.update(msg="done", last=card); return card
    except Exception as e: STATE["msg"] = f"failed: {e}"; raise
    finally: STATE["running"] = False

def train_async():
    if STATE["running"]: return False
    STATE.update(running=True, msg="starting")
    threading.Thread(target=lambda: _safe(train), daemon=True).start(); return True
def _safe(f):
    try: f()
    except Exception: pass

def load():
    if _model["clf"] is None:
        for d in (MODELS, os.path.join(HERE, "models")):                 # a retrained model wins; the shipped model is the fallback
            p = os.path.join(d, "product_model.joblib")
            if os.path.exists(p):
                import joblib; _model["clf"] = joblib.load(p); _model["card"] = json.load(open(os.path.join(d, "product_model.json"))); break
    return _model["clf"]
def card(): load(); return _model["card"]

def decode(b64):
    if "," in b64[:80]: b64 = b64.split(",", 1)[1]
    raw = base64.b64decode(b64, validate=False)
    if len(raw) > 3_000_000: raise ValueError("image too large (max 3 MB)")
    im = Image.open(io.BytesIO(raw)); im.load(); return im.convert("RGB")

def predict(img, k=3):
    clf = load()
    if clf is None: return None
    p = clf.predict_proba([feats(img)])[0]; order = np.argsort(p)[::-1][:k]
    return [{"sku": str(clf.classes_[i]), "prob": round(float(p[i]), 3)} for i in order]

def save_photo(sku, b64):
    im = decode(b64); d = os.path.join(DATA, sku); os.makedirs(d, exist_ok=True); im.thumbnail((256, 256))
    n = len(os.listdir(d)) + 1; im.save(os.path.join(d, f"u{n}_{int(time.time())}.jpg"), quality=90); return n
