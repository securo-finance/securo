"""One-shot generator for the Monarch golden sidecars (packet §8.1).

Run from backend/: uv run python /path/to/gen_golden.py
Regenerating is only allowed in the same commit as a deliberate parser
behavior change (§8.1 update discipline).
"""
import json
from pathlib import Path

from app.services.import_presets import MONARCH
from app.services.import_service import parse_csv

base = Path(__file__).resolve().parent
for name in ("monarch-combined", "monarch-account-checking"):
    content = (base / f"{name}.csv").read_bytes()
    txns, failed = parse_csv(content, preset=MONARCH)
    out = {
        "transactions": [t.model_dump(mode="json") for t in txns],
        "failed_rows": [f.model_dump(mode="json") for f in failed],
    }
    (base / f"{name}.expected.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"== {name}: {len(txns)} parsed, {len(failed)} failed")
    for t in txns:
        print(
            "  OK  ", t.date.isoformat(), "|", (t.account_hint or "-")[:18], "|",
            t.description[:24], "|", t.category_name, "|", t.type, t.amount,
            "| notes:", t.notes,
        )
    for f in failed:
        print("  FAIL", f.line_number, f.error_reason, repr(f.raw_value), "|", f.description)
