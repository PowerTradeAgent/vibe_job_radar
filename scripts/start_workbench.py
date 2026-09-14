"""Start directly from a source checkout; no pip install or shell activation required."""
import sys
from pathlib import Path


def main():
    if sys.version_info < (3, 10):
        print("Python 3.10+ is required. Install a supported Python and run this launcher again.", file=sys.stderr)
        return 2
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from vibe_job_radar.workbench import main as start
    return start()


if __name__ == "__main__":
    raise SystemExit(main())
