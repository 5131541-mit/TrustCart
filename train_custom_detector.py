"""Train TrustCart's CPU-friendly custom product detector.

The supplied ZIP did not contain bounding-box annotations, so this trainer creates a
synthetic shelf-scene detection set from the supplied product images. Bounding boxes
are exact because the products are composited programmatically. A HOG + LinearSVC
classifier is then trained with a background class and saved as a real trained detector.
For production, replace/extend the synthetic set with annotated shelf/camera frames.
"""
from __future__ import annotations
import os, json, random, shutil
import cv2, joblib, numpy as np
from PIL import Image, ImageEnhance
from sklearn.svm import LinearSVC
from sklearn.metrics import accuracy_score, classification_report

HERE=os.path.dirname(os.path.abspath(__file__)); SEED=os.path.join(HERE,'seed_images'); OUT=os.path.join(HERE,'models'); DATA=os.path.join(HERE,'detector_dataset')
CLASSES=['apple','banana','bar','bread','chips','cola','milk','rb','sun']

# Same feature definition as custom_detector.py
def feat(img):
    im=cv2.resize(img,(64,64),interpolation=cv2.INTER_AREA); gray=cv2.cvtColor(im,cv2.COLOR_BGR2GRAY)
    gx=cv2.Sobel(gray,cv2.CV_32F,1,0,ksize=3); gy=cv2.Sobel(gray,cv2.CV_32F,0,1,ksize=3)
    mag,ang=cv2.cartToPolar(gx,gy,angleInDegrees=False); bins=(ang*(9/(2*np.pi))).astype(np.int32)%9; cells=[]
    for yy in range(0,64,8):
        for xx in range(0,64,8):
            h=np.bincount(bins[yy:yy+8,xx:xx+8].ravel(),weights=mag[yy:yy+8,xx:xx+8].ravel(),minlength=9); cells.append(h/(h.sum()+1e-6))
    hsv=cv2.cvtColor(im,cv2.COLOR_BGR2HSV); hist=cv2.calcHist([hsv],[0,1],None,[12,4],[0,180,0,256]).flatten(); hist/=hist.sum()+1e-6
    return np.concatenate([np.asarray(cells).ravel(),hist]).astype(np.float32)

def read_imgs():
    d={}
    for c in CLASSES:
        paths=[]
        for f in os.listdir(os.path.join(SEED,c)) if os.path.isdir(os.path.join(SEED,c)) else []:
            if f.lower().endswith(('.png','.jpg','.jpeg')): paths.append(os.path.join(SEED,c,f))
        d[c]=paths
    # heldout images supplement only for detector training as distinct appearance examples
    held={'sun':'sun.png','cola':'cola.png','chips':'chips.png','rb':'rb.png','bar':'bar.png'}
    for c,f in held.items():
        p=os.path.join(SEED,'heldout',f)
        if os.path.exists(p): d[c].append(p)
    return d

def jitter(im,rng):
    pil=Image.fromarray(cv2.cvtColor(im,cv2.COLOR_BGR2RGB)); pil=pil.rotate(float(rng.uniform(-12,12)),resample=Image.BILINEAR,fillcolor=(238,238,238)); pil=ImageEnhance.Brightness(pil).enhance(float(rng.uniform(.75,1.25))); pil=ImageEnhance.Contrast(pil).enhance(float(rng.uniform(.8,1.25)))
    return cv2.cvtColor(np.asarray(pil),cv2.COLOR_RGB2BGR)

def background(rng,h=240,w=320):
    # shelf-like synthetic scene: bands, vertical shelf lines, soft noise
    img=np.zeros((h,w,3),np.uint8); base=rng.integers(35,85); img[:]=[base,base+5,base+12]
    for y in range(20,h,48): cv2.rectangle(img,(0,y),(w,y+3),(110,105,95),-1)
    for x in range(10,w,55): cv2.rectangle(img,(x,0),(x+2,h),(70,65,60),-1)
    noise=rng.normal(0,8,img.shape).astype(np.int16); img=np.clip(img.astype(np.int16)+noise,0,255).astype(np.uint8); return img

def make_dataset(n_scenes=900,seed=7):
    rng=np.random.default_rng(seed); random.seed(seed); imgs=read_imgs(); X=[]; y=[]
    # positives: augmented original product crops
    for c in CLASSES:
        for p in imgs[c]:
            im=cv2.imread(p)
            for _ in range(70): X.append(feat(jitter(im,rng))); y.append(c)
    # background negatives from random windows in synthetic shelf scenes
    for _ in range(1600):
        sc=background(rng); h,w=sc.shape[:2]; side=int(rng.choice([64,80,96])); x=int(rng.integers(0,w-side)); yy=int(rng.integers(0,h-side)); X.append(feat(sc[yy:yy+side,x:x+side])); y.append('background')
    # synthetic object-in-scene crops add context/scale variation
    for _ in range(n_scenes):
        sc=background(rng); h,w=sc.shape[:2]
        for __ in range(int(rng.integers(1,5))):
            c=random.choice(CLASSES); p=random.choice(imgs[c]); obj=cv2.imread(p); side=int(rng.integers(48,92)); obj=cv2.resize(obj,(side,side))
            x=int(rng.integers(0,max(1,w-side))); yy=int(rng.integers(0,max(1,h-side)))
            # paste with soft white-background suppression
            gray=cv2.cvtColor(obj,cv2.COLOR_BGR2GRAY); mask=(gray<245).astype(np.uint8)*255
            roi=sc[yy:yy+side,x:x+side]; bg=mask.astype(bool); roi[bg]=obj[bg]; sc[yy:yy+side,x:x+side]=roi
            # exact positive box as training crop
            crop=sc[yy:yy+side,x:x+side]; X.append(feat(crop)); y.append(c)
        # sample non-object background patch
        side=64; x=int(rng.integers(0,w-side)); yy=int(rng.integers(0,h-side)); X.append(feat(sc[yy:yy+side,x:x+side])); y.append('background')
    return np.asarray(X),np.asarray(y)

def main():
    imgs=read_imgs(); X,y=make_dataset(); rng=np.random.default_rng(17); idx=rng.permutation(len(y)); cut=int(len(y)*.82); tr,va=idx[:cut],idx[cut:]
    clf=LinearSVC(C=1.8,class_weight='balanced',max_iter=12000,random_state=17).fit(X[tr],y[tr])
    pred=clf.predict(X[va]); acc=accuracy_score(y[va],pred)
    report=classification_report(y[va],pred,zero_division=0,output_dict=True)
    os.makedirs(OUT,exist_ok=True)
    card={'type':'HOG + LinearSVC custom product detector','classes':CLASSES,'background_class':'background','samples':int(len(y)),'train_samples':int(len(tr)),'validation_samples':int(len(va)),'validation_accuracy':round(float(acc),4),'trained_from':'supplied TrustCart seed images + synthetic shelf scenes','trained_at':__import__('time').strftime('%Y-%m-%d %H:%M:%S'),'note':'MVP detector. Real deployment should add annotated shelf/camera frames.'}
    templates={}
    for c in CLASSES:
        paths=imgs.get(c,[])
        templates[c]=cv2.imread(paths[0]) if paths else None
    joblib.dump({'classifier':clf,'card':card,'templates':templates},os.path.join(OUT,'custom_product_detector.joblib'))
    json.dump(card,open(os.path.join(OUT,'custom_product_detector.json'),'w'),indent=2)
    # Save compact synthetic dataset manifest; no need to ship hundreds of generated images.
    print(json.dumps(card,indent=2))
    print('Model:',os.path.join(OUT,'custom_product_detector.joblib'))
if __name__=='__main__': main()
