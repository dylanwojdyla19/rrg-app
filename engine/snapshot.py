"""
engine/snapshot.py — the daily data pipeline (run by scripts/daily_snapshot.py).

Once per weekday after the US close, GitHub Actions:
  1. fetches 3y of adjusted daily closes for every ticker in the universe;
  2. writes them to data/closes.csv (the app reads this instead of Yahoo);
  3. computes the RRG for every level of the pyramid and appends each
     security's position + quadrant to data/rotation_history.csv — a durable
     record of what the chart showed on each day;
  4. writes data/last_run.json with what happened (failures, carried-forward
     tickers, level errors) so problems are visible, never silent.

The RRG math itself is NOT here — it goes through engine/data.prepare_level and
engine/calc.compute_rrg, exactly like the app, so the log matches the chart.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from engine import calc, data, quadrant

ROOT = Path(__file__).resolve().parents[1]
UNIVERSE_PATH = ROOT / "config" / "universe.json"
HISTORY_PATH = ROOT / "data" / "rotation_history.csv"
STATUS_PATH = ROOT / "data" / "last_run.json"

# If more than this share of tickers fail to fetch, Yahoo is having a bad day
# (or blocking us): abort WITHOUT overwriting yesterday's good snapshot.
MAX_FAILURE_SHARE = 0.5

HISTORY_COLUMNS = ["date", "level", "benchmark", "ticker",
                   "rs_ratio", "rs_momentum", "quadrant", "kind"]


def load_universe() -> dict:
    return json.loads(UNIVERSE_PATH.read_text())


def universe_tickers(universe: dict) -> tuple:
    """Every member and benchmark across all levels, deduped and sorted."""
    nodes = universe["nodes"].values()
    return tuple(sorted({m["ticker"] for n in nodes for m in n["members"]}
                        | {n["benchmark"] for n in nodes}))


def build_snapshot(tickers: tuple, previous):
    """
    Fetch fresh closes for all tickers. Tickers that fail today keep their
    column from the previous snapshot (so one flaky ticker doesn't vanish from
    the app) and are listed as carried forward.

    Returns (closes DataFrame, failures dict, carried list).
    Raises RuntimeError if too many tickers fail — protects the last good file.
    """
    fresh, failures = data.fetch_daily_closes(tickers)
    if len(failures) > MAX_FAILURE_SHARE * len(tickers):
        raise RuntimeError(
            f"{len(failures)}/{len(tickers)} tickers failed to fetch — refusing to "
            "overwrite the existing snapshot. Sample: "
            + "; ".join(f"{t}: {r}" for t, r in list(failures.items())[:3])
        )
    carried = []
    if previous is not None:
        keep = [t for t in failures if t in previous.columns]
        if keep:
            fresh = pd.concat([fresh, previous[keep]], axis=1)
            carried = keep
    return fresh.sort_index(), failures, carried


def write_snapshot(closes: pd.DataFrame) -> None:
    """Write closes.csv. 4 decimals (hundredths of a cent) keeps the file and
    the daily git diff small; the rounding is far too small to move RS values."""
    data.SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    closes.to_csv(data.SNAPSHOT_PATH, float_format="%.4f", index_label="date")


def compute_history_rows(universe: dict, backfill: bool = False):
    """
    Compute every level's weekly RRG (from the snapshot just written) and turn
    it into history rows.

    Daily rows: one per security, dated with the actual last close (the value
    of the in-progress week as of that day). Backfill rows (only when asked,
    e.g. the first run): one per security per COMPLETED week, so the log has
    ~2 years of rotation history from day one.

    Returns (rows DataFrame, errors dict {level: message}).
    """
    rows, errors = [], {}
    for key, node in universe["nodes"].items():
        bench_t = node["benchmark"]
        tickers = tuple(m["ticker"] for m in node["members"] if m["ticker"] != bench_t)
        try:
            prices, bench, excluded, as_of, _ = data.prepare_level(tickers, bench_t)
            ratio, momentum = calc.compute_rrg(prices, bench)
        except Exception as err:  # noqa: BLE001 — one bad level must not sink the run
            errors[key] = str(err)
            continue

        for t in ratio.columns:
            r, m = ratio[t], momentum[t]
            # Today's position (the in-progress week as of the last close).
            if pd.notna(r.iloc[-1]) and pd.notna(m.iloc[-1]):
                rows.append([as_of, key, bench_t, t, r.iloc[-1], m.iloc[-1],
                             quadrant.classify(r.iloc[-1], m.iloc[-1]), "daily"])
            if backfill:
                # Completed weeks only: a week whose Friday label is after the
                # as-of date is still in progress and is covered by the row above.
                done = r.index <= as_of
                for when, rv, mv in zip(r.index[done], r[done], m[done]):
                    if pd.notna(rv) and pd.notna(mv):
                        rows.append([when, key, bench_t, t, rv, mv,
                                     quadrant.classify(rv, mv), "weekly-backfill"])
    return pd.DataFrame(rows, columns=HISTORY_COLUMNS), errors


def append_history(new_rows: pd.DataFrame, path: Path = HISTORY_PATH) -> int:
    """
    Merge new rows into the history CSV. Idempotent: re-running on the same day
    replaces that day's rows instead of duplicating them (safe for manual reruns
    and market holidays). Returns the number of rows in the file afterwards.
    """
    if path.exists():
        old = pd.read_csv(path, parse_dates=["date"])
        merged = pd.concat([old, new_rows], ignore_index=True)
    else:
        merged = new_rows.copy()
    merged["date"] = pd.to_datetime(merged["date"]).dt.normalize()
    merged = (merged.drop_duplicates(subset=["date", "level", "ticker"], keep="last")
                    .sort_values(["date", "level", "ticker"], ignore_index=True))
    path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(path, index=False, float_format="%.4f", date_format="%Y-%m-%d")
    return len(merged)


def run(backfill: bool = False) -> dict:
    """Full daily pipeline. Returns the status dict (also written to last_run.json)."""
    universe = load_universe()
    tickers = universe_tickers(universe)

    closes, failures, carried = build_snapshot(tickers, data.load_snapshot())
    write_snapshot(closes)

    # First run (no log yet) backfills automatically.
    backfill = backfill or not HISTORY_PATH.exists()
    rows, errors = compute_history_rows(universe, backfill=backfill)
    total = append_history(rows)

    status = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "as_of": str(closes.index[-1].date()),
        "tickers": len(tickers),
        "fetch_failures": failures,
        "carried_forward_from_previous_snapshot": carried,
        "level_errors": errors,
        "history_rows_written": int(len(rows)),
        "history_rows_total": int(total),
        "backfilled": backfill,
    }
    STATUS_PATH.write_text(json.dumps(status, indent=2))
    return status
