"""Run all tests without installing the package; optionally save machine-readable evidence."""
from __future__ import annotations
import argparse
import io
import json
import platform
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--report", type=Path)
    args = p.parse_args()
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps({"created_at": datetime.now(timezone.utc).isoformat(),
                              "python": sys.version, "platform": platform.platform(),
                              "tests_run": result.testsRun, "failures": len(result.failures),
                              "errors": len(result.errors), "skipped": len(result.skipped),
                              "success": result.wasSuccessful(), "live_network_calls": 0,
                              "note": "Network provider paths use mocked responses; not a live integration certification."},
                              ensure_ascii=False, indent=2), encoding="utf-8")
        args.report.with_suffix(".log").write_text(stream.getvalue(), encoding="utf-8")
    # Save UTF-8 evidence before console output: a Windows legacy code page
    # must not erase the original failure or prevent the JSON report entirely.
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    console = stream.getvalue().encode(encoding, errors="backslashreplace").decode(encoding)
    print(console)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
