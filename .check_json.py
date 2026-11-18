import glob
import json

bad = []
for f in sorted(glob.glob("frontend/src/locales/*.json")):
    try:
        json.load(open(f))
    except Exception as exc:
        bad.append((f, str(exc)[:100]))
print(bad if bad else "json ok")
