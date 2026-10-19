"""TrustCart trained lightweight product detector.

Pipeline: camera frame -> HOG/SVM product detector -> stable count tracker -> pick event -> /api/vision.
This is a CPU-friendly MVP detector trained from the supplied product images plus synthetic shelf scenes.
It does not require Ultralytics/YOLO.
"""
from __future__ import annotations
import argparse, os, time, statistics, json
from collections import defaultdict, deque
import cv2, joblib, numpy as np

CLASS_TO_SKU = {"chips":"chips","cola":"cola","rb":"rb","sun":"sun","milk":"milk","apple":"apple","bar":"bar","banana":"banana","bread":"bread"}
MODEL_DEFAULT = os.path.join(os.path.dirname(__file__), "models", "custom_product_detector.joblib")

class TrainedProductDetector:
    def __init__(self, model_path=MODEL_DEFAULT):
        self.model_path=model_path
        self.card=None
        self.clf=None
        if os.path.exists(model_path): self.load(model_path)
    def load(self,path=None):
        path=path or self.model_path
        pack=joblib.load(path); self.clf=pack["classifier"]; self.card=pack["card"]; self.templates=pack.get("templates",{}); return self
    @staticmethod
    def feat(img):
        im=cv2.resize(img,(64,64),interpolation=cv2.INTER_AREA)
        gray=cv2.cvtColor(im,cv2.COLOR_BGR2GRAY)
        gx=cv2.Sobel(gray,cv2.CV_32F,1,0,ksize=3); gy=cv2.Sobel(gray,cv2.CV_32F,0,1,ksize=3)
        mag,ang=cv2.cartToPolar(gx,gy,angleInDegrees=False)
        bins=(ang*(9/(2*np.pi))).astype(np.int32)%9
        cells=[]
        for yy in range(0,64,8):
            for xx in range(0,64,8):
                h=np.bincount(bins[yy:yy+8,xx:xx+8].ravel(),weights=mag[yy:yy+8,xx:xx+8].ravel(),minlength=9)
                cells.append(h/(h.sum()+1e-6))
        hsv=cv2.cvtColor(im,cv2.COLOR_BGR2HSV)
        hist=cv2.calcHist([hsv],[0,1],None,[12,4],[0,180,0,256]).flatten(); hist/=hist.sum()+1e-6
        return np.concatenate([np.asarray(cells).ravel(),hist]).astype(np.float32)
    def predict_patch(self,img):
        x=self.feat(img).reshape(1,-1); scores=self.clf.decision_function(x)
        if scores.ndim==1:
            idx=0 if scores[0]>=0 else 1
            labels=self.clf.classes_; label=labels[idx]; margin=float(abs(scores[0]))
        else:
            idx=int(np.argmax(scores[0])); label=self.clf.classes_[idx]; margin=float(scores[0,idx])
        return str(label),margin
    def detect(self,frame,conf=0.75,max_detections=12,roi=None):
        # Template proposals + trained HOG/SVM confirmation. This keeps inference CPU-friendly
        # while using the trained classifier for the final product decision.
        h,w=frame.shape[:2]
        if roi:
            x0,y0,x1,y1=roi; crop=frame[y0:y1,x0:x1]; ox,oy=x0,y0
        else: crop=frame; ox=oy=0
        proposals=[]
        for cls,tpl in getattr(self,'templates',{}).items():
            if tpl is None: continue
            gray=cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY); tgray=cv2.cvtColor(tpl,cv2.COLOR_BGR2GRAY)
            for scale in (0.65,0.8,1.0,1.2,1.4):
                tw=max(24,int(tgray.shape[1]*scale)); th=max(24,int(tgray.shape[0]*scale))
                if tw>=gray.shape[1] or th>=gray.shape[0]: continue
                t=cv2.resize(tgray,(tw,th),interpolation=cv2.INTER_AREA)
                r=cv2.matchTemplate(gray,t,cv2.TM_CCOEFF_NORMED)
                ys,xs=np.where(r>=0.48)
                # Keep the strongest few proposals for this scale/class.
                vals=sorted([(float(r[y,x]),int(x),int(y)) for y,x in zip(ys,xs)],reverse=True)[:8]
                for score,x,y in vals: proposals.append((x+ox,y+oy,x+tw+ox,y+th+oy,cls,score))
        # Verify proposals with the trained classifier and apply NMS.
        verified=[]
        for x1,y1,x2,y2,cls,match in proposals:
            x1=max(0,x1); y1=max(0,y1); x2=min(w,x2); y2=min(h,y2)
            if x2-x1<24 or y2-y1<24: continue
            pred,margin=self.predict_patch(frame[y1:y2,x1:x2])
            if pred!=cls or margin<conf: continue
            score=0.65*match+0.35*min(1.0,margin/(margin+1.0))
            verified.append((x1,y1,x2,y2,pred,score))
        verified.sort(key=lambda d:d[5],reverse=True); out=[]
        for d in verified:
            x1,y1,x2,y2,cls,score=d; keep=True
            for q in out:
                if q[4]!=cls: continue
                ix=max(0,min(x2,q[2])-max(x1,q[0])); iy=max(0,min(y2,q[3])-max(y1,q[1])); inter=ix*iy
                iou=inter/max(1,(x2-x1)*(y2-y1)+(q[2]-q[0])*(q[3]-q[1])-inter)
                if iou>0.35: keep=False; break
            if keep: out.append(d)
            if len(out)>=max_detections: break
        return out

class StablePickTracker:
    def __init__(self,smooth=8): self.hist=defaultdict(lambda:deque(maxlen=smooth)); self.stable={}; self.smooth=smooth
    def update(self,counts,confs):
        picks=[]
        for cls in set(counts)|set(self.hist):
            h=self.hist[cls]; h.append(counts.get(cls,0))
            if len(h)<self.smooth: continue
            now=int(statistics.median(h)); prev=self.stable.get(cls)
            if prev is not None and now<prev:
                c=max(confs.get(cls,[0.5])); picks.extend([(cls,c)]*(prev-now))
            self.stable[cls]=now
        return picks

def run(source,weights=MODEL_DEFAULT,server="http://localhost:8000",api_key="",conf=.75,roi=None,show=False):
    import requests
    cap=cv2.VideoCapture(int(source) if str(source).isdigit() else source)
    if not cap.isOpened(): raise RuntimeError(f"Cannot open camera/video source: {source}")
    model=TrainedProductDetector(weights); tracker=StablePickTracker(); roi=tuple(map(int,roi.split(','))) if roi else None
    while True:
        ok,frame=cap.read()
        if not ok: break
        dets=model.detect(frame,conf=conf,roi=roi)
        counts=defaultdict(int); confs=defaultdict(list)
        for x1,y1,x2,y2,cls,score in dets:
            counts[cls]+=1; confs[cls].append(min(0.99,max(0.50,score/(score+1.0))))
            if show:
                cv2.rectangle(frame,(x1,y1),(x2,y2),(34,197,94),2)
                cv2.putText(frame,f"{cls} {score:.2f}",(x1,max(18,y1-4)),cv2.FONT_HERSHEY_SIMPLEX,.5,(34,197,94),2)
        for cls,c in tracker.update(counts,confs):
            sku=CLASS_TO_SKU.get(cls,cls); payload={"sku":sku,"conf":round(float(c),3),"ts":time.time()}
            try:
                r=requests.post(server.rstrip('/')+"/api/vision",json=payload,headers={"X-API-Key":api_key},timeout=3); r.raise_for_status(); print(f"pick event -> {sku} confidence={c:.2f}")
            except Exception as e: print("vision event failed:",e)
        if show:
            if roi: cv2.rectangle(frame,roi[:2],roi[2:],(245,158,11),2)
            cv2.imshow("TrustCart Custom Product Detector",frame)
            if cv2.waitKey(1)&0xFF==ord('q'): break
    cap.release();
    if show: cv2.destroyAllWindows()

def main():
    p=argparse.ArgumentParser(); p.add_argument('--source',default='0'); p.add_argument('--weights',default=MODEL_DEFAULT); p.add_argument('--server',default='http://localhost:8000'); p.add_argument('--key',default=''); p.add_argument('--conf',type=float,default=.75); p.add_argument('--roi',default=''); p.add_argument('--show',action='store_true'); a=p.parse_args()
    if not os.path.exists(a.weights): raise SystemExit(f"Detector model not found: {a.weights}. Run train_custom_detector.py")
    run(a.source,a.weights,a.server,a.key,a.conf,a.roi,a.show)
if __name__=='__main__': main()
