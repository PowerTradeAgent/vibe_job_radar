"""Run the entire synthetic demo with standard-library Python; no installation or API keys."""
from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from vibe_job_radar.ingest import iter_items
from vibe_job_radar.pipeline import analyze
from vibe_job_radar.store import Store


def main() -> int:
    p = argparse.ArgumentParser(description="Offline synthetic demo, never a live market survey")
    p.add_argument("--out", type=Path, default=ROOT / "runs" / ("demo_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")))
    args = p.parse_args()
    if args.out.exists():
        p.error("choose a new --out directory; previous runs are preserved")
    args.out.mkdir(parents=True)
    db = args.out / "jobs.sqlite"
    with Store(db) as store:
        for ref, item in iter_items(ROOT / "examples/jobs.synthetic.jsonl"):
            if isinstance(item, Exception):
                raise RuntimeError(f"{ref}: {item}")
            store.add(item)
    result = analyze(db, args.out / "report", demo_mode=True, as_of="2026-09-14T08:07:15Z",
                     candidate_path=ROOT / "examples/candidate.synthetic.json")
    print(json.dumps(result["stats"], ensure_ascii=False, indent=2))
    print("\nOpen locally: " + str((args.out / "report/dashboard.html").resolve()))
    print("SYNTHETIC DATA ONLY: not real vacancies or personal achievements.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
