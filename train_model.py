"""Train the product recognition model:  python train_model.py   -> models/product_model.joblib + models/product_model.json"""
import json, recognizer
c = recognizer.train()
print(json.dumps({k: c[k] for k in ("classes", "photos", "n_train", "augmented_val_accuracy", "cross_render_accuracy", "cross_render_test")}, indent=1))
