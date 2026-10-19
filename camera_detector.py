"""Real product detection from video. Counts products inside the shelf area each frame; when a product's
stable count drops, it reports a pick to the server as vision evidence.
Install: pip install ultralytics opencv-python requests
Run:     python camera_detector.py --source 0 --model yolov8n.pt --roi 100,80,540,400 --server http://localhost:8000 --key YOUR_DEVICE_API_KEY --show
IMPORTANT: a generic COCO model only knows classes like bottle and apple. To tell Lay's from other chips, train
a custom YOLO model on photos of YOUR products and map its class names in CLASS_TO_SKU below."""
import argparse, statistics, time
from collections import defaultdict, deque

CLASS_TO_SKU = {"bottle": "cola", "apple": "apple", "chips": "chips", "redbull": "rb",
                "sunglasses": "sun", "milk": "milk", "bar": "bar"}   # model class name -> product id

class PickTracker:
    """Turns noisy per-frame counts into pick events: a drop must hold for `smooth` frames to count."""
    def __init__(self, smooth=12):
        self.hist, self.stable, self.smooth = defaultdict(lambda: deque(maxlen=smooth)), {}, smooth
    def update(self, counts, confs):
        picks = []
        for cls in set(counts) | set(self.hist):
            h = self.hist[cls]; h.append(counts.get(cls, 0))
            if len(h) < self.smooth: continue
            now, prev = int(statistics.median(h)), self.stable.get(cls)
            if prev is not None and now < prev:
                conf = max(confs.get(cls, [0.5]))
                picks += [(cls, conf)] * (prev - now)
            self.stable[cls] = now
        return picks

def main():
    import cv2, requests
    from ultralytics import YOLO
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="0"); ap.add_argument("--model", default="yolov8n.pt")
    ap.add_argument("--roi", default=""); ap.add_argument("--server", default="http://localhost:8000")
    ap.add_argument("--key", default=""); ap.add_argument("--conf", type=float, default=0.4); ap.add_argument("--show", action="store_true")
    a = ap.parse_args()
    cap = cv2.VideoCapture(int(a.source) if a.source.isdigit() else a.source)
    model, tracker = YOLO(a.model), PickTracker()
    roi = tuple(int(v) for v in a.roi.split(",")) if a.roi else None
    while cap.isOpened():
        ok, frame = cap.read()
        if not ok: break
        counts, confs = defaultdict(int), defaultdict(list)
        for b in model(frame, conf=a.conf, verbose=False)[0].boxes:
            x1, y1, x2, y2 = b.xyxy[0].tolist(); cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            if roi and not (roi[0] <= cx <= roi[2] and roi[1] <= cy <= roi[3]): continue
            name = model.names[int(b.cls)]
            if name in CLASS_TO_SKU:
                counts[name] += 1; confs[name].append(float(b.conf))
                if a.show: cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), (34, 197, 94), 2)
        for cls, conf in tracker.update(counts, confs):
            sku = CLASS_TO_SKU[cls]; print(f"pick seen: {sku} ({conf:.2f})")
            try: requests.post(a.server + "/api/vision", json={"sku": sku, "conf": round(conf, 2)}, headers={"X-API-Key": a.key}, timeout=3)
            except requests.RequestException as e: print("server unreachable:", e)
        if a.show:
            if roi: cv2.rectangle(frame, roi[:2], roi[2:], (245, 158, 11), 2)
            cv2.imshow("TrustCart camera", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"): break
if __name__ == "__main__": main()
