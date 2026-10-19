"""Train every trainable TrustCart AI component shipped in this MVP."""
import subprocess, sys
for script in ('train_model.py','train_custom_detector.py'):
    print('\n=== TRAINING', script, '===')
    r=subprocess.run([sys.executable,script],check=False)
    if r.returncode: raise SystemExit(r.returncode)
print('\nAll trainable TrustCart models completed.')
