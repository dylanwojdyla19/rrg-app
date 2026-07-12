"""
validation/validate.py — Section 7 validation protocol (Tests 1, 2, 4).
Test 3 (mathematical invariants) lives in tests/test_invariants.py (pytest).

Usage (from the repo root):
    python validation/validate.py            # run all three tests
    python validation/validate.py --test 1   # just the StockCharts comparison table
    python validation/validate.py --test 1 --date 2026-07-10   # historical date
    python validation/validate.py --test 2   # historical rotation sanity
    python validation/validate.py --test 4   # data integrity vs Stooq

Record every run's results (and any parameter tuning) in VALIDATION.md.
"""

import argparse
import io
import json
import random
import sys
from pathlib import Path

import pandas as pd

# Make `engine` importable when this script is run from anywhere.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine import calc, data, quadrant  # noqa: E402

SECTORS = ["XLK", "XLF", "XLV", "XLE", "XLI", "XLY", "XLP", "XLB", "XLU", "XLRE", "XLC"]
BENCH = "SPY"


def _compute_sectors():
    """Fetch + compute the 11-sector weekly RRG vs SPY once, reused by tests 1 & 2."""
    prices, bench, excluded, as_of = data.prepare_level(tuple(SECTORS), BENCH)
    if excluded:
        print(f"  WARNING — excluded tickers: {excluded}")
    ratio, momentum = calc.compute_rrg(prices, bench)
    return ratio, momentum, as_of


def _heading(dx: float, dy: float) -> str:
    """Rough tail direction (compass) from the last week's movement."""
    ew = "E" if dx > 0 else "W"   # East = RS-Ratio rising
    ns = "N" if dy > 0 else "S"   # North = RS-Momentum rising
    return ns + ew


# ---------------------------------------------------------------------------
# Test 1 — Quadrant agreement vs StockCharts (primary trust test)
# ---------------------------------------------------------------------------
def test1(date):
    print("=" * 74)
    print("TEST 1 — Sector quadrants vs SPY (compare against StockCharts free RRG)")
    print("=" * 74)
    ratio, momentum, as_of = _compute_sectors()

    # Pick the requested week (or the latest available one).
    if date:
        target = pd.Timestamp(date)
        idx = ratio.index[ratio.index <= target]
        if len(idx) == 0:
            raise SystemExit(f"no data on/before {date}")
        when = idx[-1]
    else:
        when = ratio.index[-1]
    prev = ratio.index[ratio.index.get_loc(when) - 1]

    print(f"\nWeek ending: {when.date()}   (data as of {as_of.date()})\n")
    print(f"{'Sector':<7}{'Quadrant':<12}{'RS-Ratio':>10}{'RS-Mom':>10}   Heading")
    print("-" * 50)
    for t in SECTORS:
        if t not in ratio.columns:
            print(f"{t:<7}EXCLUDED")
            continue
        r, m = ratio.loc[when, t], momentum.loc[when, t]
        q = quadrant.classify(r, m)
        h = _heading(r - ratio.loc[prev, t], m - momentum.loc[prev, t])
        print(f"{t:<7}{q:<12}{r:>10.2f}{m:>10.2f}   {h}")
    print(
        "\nPASS RULE: screenshot StockCharts' free sector RRG (weekly, vs SPY) on the"
        "\nsame date; >= 9 of 11 sectors must land in the same quadrant, and tail"
        "\ndirections should broadly agree. Record the result in VALIDATION.md."
    )


# ---------------------------------------------------------------------------
# Test 2 — Rotation sanity vs known market history
# ---------------------------------------------------------------------------
def test2():
    print("=" * 74)
    print("TEST 2 — Historical rotation sanity (owner eyeballs vs known history)")
    print("=" * 74)
    print(
        "\nNOTE: yfinance history here is 3y from today, so 'known rotations' are"
        "\nchecked in the OLDEST part of the current window. If today-3y no longer"
        "\ncovers a listed episode, eyeball the printed timeline against whatever"
        "\nmarket regime it does cover, and log what you checked in VALIDATION.md.\n"
    )
    ratio, momentum, _ = _compute_sectors()

    # Print a monthly-sampled quadrant timeline for the defensive rotation pair
    # and energy — the classic risk-on/risk-off tells.
    watch = ["XLE", "XLU", "XLP", "XLK"]
    monthly = ratio.resample("ME").last()
    monthly_m = momentum.resample("ME").last()
    print(f"{'Month':<12}" + "".join(f"{t:>12}" for t in watch))
    print("-" * (12 + 12 * len(watch)))
    for when in monthly.index:
        row = []
        for t in watch:
            r, m = monthly.loc[when, t], monthly_m.loc[when, t]
            row.append("warmup" if pd.isna(r) or pd.isna(m) else quadrant.classify(r, m)[:4])
        print(f"{str(when.date()):<12}" + "".join(f"{c:>12}" for c in row))
    print(
        "\nSanity anchors (check the ones inside the printed window):"
        "\n  - Risk-off stretches: XLU/XLP should shift toward Improving/Leading."
        "\n  - Risk-on stretches: XLK Leading while XLU/XLP sink to Lagging."
        "\n  - Energy shocks (e.g., late 2021-2022): XLE Improving -> Leading."
    )


# ---------------------------------------------------------------------------
# Test 4 — Data integrity: cross-check latest weekly closes vs Stooq
# ---------------------------------------------------------------------------
def _nasdaq_close_on(ticker, when):
    """
    Fetch the raw daily close for `ticker` on date `when` from Nasdaq's free
    historical-quotes API. (Stooq — the original plan — now sits behind a
    JavaScript verification wall, so Nasdaq is the substitute free source;
    substitution noted in VALIDATION.md.) Uses curl_cffi so the CA-bundle
    handling from engine/data.py applies here too.
    """
    from curl_cffi import requests as curl_requests  # imported after engine.data's CA shim

    start = (when - pd.Timedelta(days=10)).date()
    for assetclass in ("stocks", "etf"):  # equities and ETFs live on different endpoints
        url = (
            f"https://api.nasdaq.com/api/quote/{ticker}/historical"
            f"?assetclass={assetclass}&fromdate={start}&todate={when.date()}&limit=15"
        )
        resp = curl_requests.get(url, impersonate="chrome", timeout=30)
        if resp.status_code != 200:
            continue
        rows = (((resp.json() or {}).get("data") or {}).get("tradesTable") or {}).get("rows") or []
        for row in rows:
            if pd.Timestamp(row["date"]) == when.normalize():
                return float(row["close"].replace("$", "").replace(",", ""))
    raise ValueError(f"Nasdaq returned no close for {ticker} on {when.date()}")


def test4():
    print("=" * 74)
    print("TEST 4 — Data integrity: yfinance weekly closes vs Nasdaq (free source)")
    print("=" * 74)
    universe = json.loads((ROOT / "config" / "universe.json").read_text())
    all_tickers = sorted(
        {m["ticker"] for node in universe["nodes"].values() for m in node["members"]}
    )
    random.seed()  # genuinely random pick each run, per the spec
    picks = random.sample(all_tickers, 3)
    print(f"\nRandomly selected: {picks}\n")

    ok = True
    for t in picks:
        prices, bench, excluded, _ = data.prepare_level((t,), "SPY")
        if t not in prices.columns:
            print(f"{t}: EXCLUDED by hygiene ({excluded.get(t)}) — pick another ticker")
            ok = False
            continue
        yf_date, yf_close = prices[t].dropna().index[-1], prices[t].dropna().iloc[-1]
        try:
            # Same calendar date on both sides: our weekly bar is stamped with
            # the Friday it closed on, and we ask Nasdaq for that exact day.
            nd_close = _nasdaq_close_on(t, yf_date)
        except Exception as err:  # noqa: BLE001
            print(f"{t}: Nasdaq fetch failed ({err}) — retry or pick another ticker")
            ok = False
            continue
        diff_pct = abs(yf_close - nd_close) / nd_close * 100
        verdict = "PASS" if diff_pct <= 0.5 else "CHECK"
        print(
            f"{t}: yfinance {yf_close:.2f}  vs  Nasdaq {nd_close:.2f} "
            f"({yf_date.date()})  diff {diff_pct:.3f}%  -> {verdict}"
        )
        ok = ok and diff_pct <= 0.5
    print(
        "\nPASS RULE: within 0.5%. Small differences can be adjusted-vs-raw close"
        "\n(dividends) — note them in VALIDATION.md rather than failing outright."
    )
    return ok


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RRG validation protocol (Section 7)")
    parser.add_argument("--test", choices=["1", "2", "4", "all"], default="all")
    parser.add_argument("--date", help="YYYY-MM-DD for Test 1 (default: latest week)")
    args = parser.parse_args()

    if args.test in ("1", "all"):
        test1(args.date)
    if args.test in ("2", "all"):
        print()
        test2()
    if args.test in ("4", "all"):
        print()
        test4()
