# VALIDATION.md — trust log for the RRG engine

Every validation run, its result, and every parameter change gets recorded here.
The engine parameters are pinned (EMA 10/30, ROC 10, 52-week z-score, min_periods 26)
and may only be tuned if Test 1 fails — allowed tuning candidates: EMA spans 12/26
or SMA 10/30, ROC window 9 or 14.

## Parameter change log

| Date | Change | Reason |
|------|--------|--------|
| 2026-07-11 | none — initial build with pinned parameters (EMA 10/30, ROC 10, z-52/26) | — |

## Test 3 — Mathematical invariants (pytest, automated)

**2026-07-11 — PASS (6/6)** on Python 3.14, pandas 2.x:

- benchmark-identical security sits at exactly (100, 100) after warm-up ✅
- 52-week means of RS-Ratio and RS-Momentum within 100 ± 0.5 ✅
- no NaNs after warm-up (and the loud-failure assertion itself is tested) ✅
- no lookahead: truncating the future never changes past values ✅
- misaligned indexes are rejected loudly ✅

Run with: `python -m pytest tests/`

## Test 1 — Quadrant agreement vs StockCharts (primary trust test)

**2026-07-11 — engine output recorded; StockCharts side-by-side PENDING (owner action).**

Our quadrants for week ending **2026-07-10** (weekly, vs SPY):

| Sector | Quadrant | RS-Ratio | RS-Mom | Heading |
|--------|----------|---------:|-------:|---------|
| XLK  | Leading   | 101.96 | 100.68 | SW |
| XLF  | Leading   | 100.04 | 101.41 | NE |
| XLV  | Leading   | 100.24 | 100.24 | NE |
| XLE  | Lagging   |  99.69 |  98.95 | NW |
| XLI  | Lagging   |  99.94 |  99.90 | NW |
| XLY  | Improving |  98.02 | 100.62 | NE |
| XLP  | Lagging   |  99.99 |  99.70 | NW |
| XLB  | Lagging   |  99.90 |  99.35 | NW |
| XLU  | Lagging   |  98.71 |  99.14 | NW |
| XLRE | Weakening | 100.75 |  99.43 | SW |
| XLC  | Lagging   |  97.40 |  99.94 | NE |

**Owner TODO:** screenshot StockCharts' free sector RRG (weekly, benchmark SPY) on the
same date, count matching quadrants, and record the score here. PASS = ≥ 9/11 and
broadly agreeing tail directions. Then update `VALIDATED_ON` / `AGREEMENT` in `app.py`.

Re-run any time with: `python validation/validate.py --test 1 [--date YYYY-MM-DD]`

## Test 2 — Rotation sanity (historical)

**2026-07-11 — PASS (eyeball check within the available 3y window).**

The spec's example episode (XLE entering Leading in late 2021–2022) is outside the
current 3-year yfinance window (which now starts mid-2023), so the check was done
against known 2024–2026 regimes visible in the monthly quadrant timeline the script
prints:

- **Spring 2025 risk-off:** XLU and XLP move to Leading while XLK sits in Lagging
  (Feb–Apr 2025 rows) — matches the tariff-selloff defensive rotation. ✅
- **Mid/late-2025 risk-on:** XLK returns to Leading; XLU/XLP decay to Lagging. ✅
- **Late-2025 energy strength:** XLE Improving → Leading (Nov 2025–Mar 2026 rows). ✅

Re-run any time with: `python validation/validate.py --test 2`

## Test 4 — Data integrity vs a second free source

**2026-07-11 — PASS (3/3 within 0.5%).**

Source substitution: the plan named Stooq, but Stooq's CSV endpoint now sits behind a
JavaScript verification wall, so the cross-check uses **Nasdaq's free historical-quotes
API** instead (raw daily closes, matched on the exact Friday date).

| Ticker | yfinance | Nasdaq | Date | Diff |
|--------|---------:|-------:|------|-----:|
| KBE | 68.75 | 68.75 | 2026-07-10 | 0.000% |
| HMY | 15.11 | 15.11 | 2026-07-10 | 0.000% |
| TJX | 151.34 | 151.34 | 2026-07-10 | 0.000% |

(Exact matches because the latest bar's adjusted close equals the raw close — no
subsequent dividends yet. Older bars would legitimately differ by dividend adjustments.)

Re-run any time with: `python validation/validate.py --test 4`
