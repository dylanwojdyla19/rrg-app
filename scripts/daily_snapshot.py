"""
scripts/daily_snapshot.py — entry point for the daily data job.

Run by .github/workflows/daily-snapshot.yml every weekday after the US close,
and runnable by hand:

    python scripts/daily_snapshot.py              # normal daily update
    python scripts/daily_snapshot.py --backfill   # also rebuild ~2y of weekly history

Exit code 1 means nothing was written (e.g. Yahoo blocked most requests) —
GitHub then emails the repo owner that the job failed.
"""

import argparse
import json
import sys
from pathlib import Path

# Make `engine` importable when run from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import snapshot  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Daily RRG data snapshot + rotation log")
    parser.add_argument("--backfill", action="store_true",
                        help="also write weekly history rows for all completed weeks")
    args = parser.parse_args()

    try:
        status = snapshot.run(backfill=args.backfill)
    except RuntimeError as err:
        print(f"SNAPSHOT ABORTED: {err}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(status, indent=2))
    # Partial problems don't fail the job (the good data still gets saved),
    # but they are printed here and recorded in data/last_run.json.
    if status["fetch_failures"] or status["level_errors"]:
        print("WARNING: completed with partial problems — see data/last_run.json",
              file=sys.stderr)
