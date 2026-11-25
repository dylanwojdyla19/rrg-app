# RRG — Sector Rotation Web App

A Relative Rotation Graph (RRG) web app for **US equity rotation**, using Julius de
Kempenaer's top-down methodology: **US Sectors (vs SPY) → Subsectors → Stocks**, each
level plotted against its own benchmark on a JdK RS-Ratio × RS-Momentum plane.
(Live at https://rrg-sector-rotation.streamlit.app. The original asset-class top level
was removed in Nov 2026 to keep the focus on equities.)

> **Disclosure:** this is a **public approximation of the JdK RRG**, not a replica of
> StockCharts'/Optuma's proprietary, universe-aware normalization. Quadrant placement
> and rotation direction are validated against StockCharts' free sector RRG (see
> [VALIDATION.md](VALIDATION.md)); exact coordinates will always differ.

## How it works

- **Data:** Yahoo Finance adjusted closes (`yfinance`, `auto_adjust=True`), 3 years
  per ticker, resampled to **weekly (Friday) closes** — the RRG standard. Cached 1h.
- **RS-Ratio:** `100 × EMA₁₀(RS) / EMA₃₀(RS)` where `RS = price / benchmark`,
  normalized by a rolling 52-week z-score rescaled to mean 100 / std 1.
- **RS-Momentum:** 10-week rate of change of RS-Ratio, same normalization.
- **Quadrants:** Leading (↗ green), Weakening (↘ yellow), Lagging (↙ red),
  Improving (↖ blue), axes crossing at (100, 100).
- **Timeframe filter changes only the tail length drawn** — never the calculation
  windows — so quadrant positions are consistent across views. The 4-week view has an
  optional, clearly-labeled "Daily RRG" toggle that reruns the engine on daily closes.

## Daily data refresh

A GitHub Actions job (`.github/workflows/daily-snapshot.yml`) runs every weekday at
22:30 UTC, after the US close:

1. fetches 3 years of adjusted daily closes for all ~300 tickers;
2. saves them to `data/closes.csv` — the app reads this file instead of calling Yahoo
   on every page load (Yahoo intermittently rate-limits cloud servers);
3. appends every level's RS-Ratio / RS-Momentum / quadrant to
   `data/rotation_history.csv` — a permanent day-by-day record of the rotation
   (the first run also backfilled ~2 years of completed weeks);
4. writes `data/last_run.json` with anything that went wrong.

If Yahoo blocks most requests, the job fails without touching yesterday's data and
GitHub emails you. Single flaky tickers keep yesterday's prices and are listed in
`last_run.json`. Run it by hand from the repo's **Actions** tab → *Daily RRG snapshot*
→ *Run workflow*, or locally with `python scripts/daily_snapshot.py`.

Because the bot commits daily, run `git pull` before editing the repo locally.

In the app, the sidebar shows the data date and source. The **Live prices** switch
bypasses the snapshot and fetches from Yahoo directly for intraday checks. If the
snapshot is more than 4 days old, the app fetches live automatically.

## Run locally

```bash
pip install -r requirements.txt
python -m pytest tests/          # mathematical invariants must be green
streamlit run app.py
```

**Windows note:** if yfinance dies with `curl: (60) SSL certificate problem` (antivirus
or network TLS interception), run `scripts/make_ca_bundle.ps1` once. It exports the
Windows trust store to `config/windows-ca-bundle.pem`, which `engine/data.py` picks up
automatically. The file is machine-specific and gitignored.

## Validation

The full protocol (quadrant agreement vs StockCharts, historical rotation sanity,
mathematical invariants, data integrity vs a second source) and all recorded results
live in [VALIDATION.md](VALIDATION.md).

```bash
python validation/validate.py            # tests 1, 2, 4 (network)
python -m pytest tests/                  # test 3 (offline, CI-safe)
```

## Universe maintenance

`config/universe.json` is pure data — the app never hardcodes tickers.

- **Stock lists are static top-holdings snapshots** (yfinance does not reliably expose
  ETF holdings). Each stock node carries a `holdings_as_of` date. Refresh them
  occasionally from each ETF issuer's holdings page and bump the date.
- **Substitutions vs the original spec** (dead tickers found by the build-time
  resolution check, replaced with liquid alternatives from the same funds):
  - CHX (ChampionX, absorbed by SLB) → **LBRT** (Liberty Energy) in the OIH list.
  - CYBR (CyberArk, acquired by Palo Alto Networks) → **CHKP** (Check Point) in CIBR.
  - CTRA (Coterra, no longer resolving on Yahoo) → **EQT** in the XOP list.
  - PBS (Invesco Dynamic Media, delisted by Nov 2026) → **IYZ** (iShares U.S.
    Telecommunications) as the second XLC subsector; its stock list is IYZ's top 10
    as of 2026-11-24. SPCX (SpaceX) is in that top 10 but is too newly listed for the
    40-week warm-up, so the app shows it as excluded until it has enough history.
  - GDX top holdings: Barrick is listed under its post-2025 NYSE ticker; the snapshot
    uses other large, liquid GDX names instead to avoid ticker-rename breakage.
  - COPX/LIT hold several foreign-listed names; the snapshots keep US-listed
    constituents (8 names instead of 10 where needed).
  - KBE/KIE are ~equal-weight, so "top 10 by weight" is nearly arbitrary — the
    snapshots use the largest liquid US banks/insurers in the funds.
  - Data-integrity cross-check source: Stooq (spec) is behind a JS wall → **Nasdaq's
    free historical API** is used instead.

## Deploy (Streamlit Community Cloud)

1. Push this repo to GitHub.
2. At [share.streamlit.io](https://share.streamlit.io) → New app → pick the repo,
   branch `main`, entrypoint `app.py`.
3. After it's live, re-run validation Test 1 against the deployed app and record the
   result in VALIDATION.md (build-order step 8).

## Structure

```
app.py                     # Streamlit UI: chart, tails, drill-down, table
config/universe.json       # the 4-level pyramid (data, never logic)
engine/data.py             # fetch, weekly resample, hygiene rules
engine/calc.py             # pinned RRG formulas, nothing else
engine/quadrant.py         # quadrant classification + pinned colors
engine/snapshot.py         # daily pipeline: snapshot, rotation log, run status
scripts/daily_snapshot.py  # entry point for the daily GitHub Actions job
data/closes.csv            # daily adjusted closes (bot-updated)
data/rotation_history.csv  # day-by-day RRG readings for every level (bot-updated)
tests/test_snapshot.py     # offline tests for the data pipeline
validation/validate.py     # protocol tests 1, 2, 4
tests/test_invariants.py   # protocol test 3 (pytest)
scripts/make_ca_bundle.ps1 # Windows-only TLS fix helper
VALIDATION.md              # trust log — results + parameter changes
```

## Non-goals (v1)

No intraday data, no alerts, no portfolio tracking, no paid data sources, and no
attempt to replicate StockCharts' proprietary normalization exactly.
