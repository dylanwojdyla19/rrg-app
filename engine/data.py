"""
engine/data.py — Data fetching, weekly resampling, and data-hygiene rules.

Implements Section 2 of the master prompt:
  - yfinance, adjusted close only (auto_adjust=True)
  - WEEKLY closes (Friday) are the calculation standard; daily is opt-in
  - 3 years of history per ticker
  - All five pinned hygiene rules (gap-fill limits, benchmark alignment,
    minimum history, retry-then-exclude, loud NaN assertions live in calc.py)

Nothing in this module knows about Streamlit UI state — it only fetches,
cleans, and returns data plus a dict of excluded tickers with reasons,
so the UI can show warning chips instead of silently dropping anything.
"""

import os
import sys
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# Windows-only local dev shim (harmless elsewhere):
# On some Windows machines, antivirus/network TLS interception presents a
# certificate that IS in the Windows trust store but NOT in Python's certifi
# bundle, so every yfinance call dies with curl error 60. If a PEM export of
# the Windows trust store exists (generate it with scripts/make_ca_bundle.ps1),
# point curl_cffi/requests at it BEFORE yfinance is imported. Deployed Linux
# environments never have this file, so this block is a no-op there.
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parents[1]
_WIN_CA_BUNDLE = _ROOT / "config" / "windows-ca-bundle.pem"
if sys.platform == "win32" and _WIN_CA_BUNDLE.exists():
    os.environ.setdefault("CURL_CA_BUNDLE", str(_WIN_CA_BUNDLE))
    os.environ.setdefault("SSL_CERT_FILE", str(_WIN_CA_BUNDLE))
    os.environ.setdefault("REQUESTS_CA_BUNDLE", str(_WIN_CA_BUNDLE))

import yfinance as yf  # noqa: E402 — must come after the CA bundle shim above

# ---------------------------------------------------------------------------
# Caching shim:
# The master prompt pins @st.cache_data(ttl=3600) on the fetch function.
# But this module is also imported by pytest and validation scripts where a
# Streamlit runtime may not exist. If streamlit is importable we use its
# cache; otherwise we fall back to a no-op decorator so the engine stays
# usable from plain Python.
# ---------------------------------------------------------------------------
try:
    import streamlit as st
    _cache = st.cache_data(ttl=3600, show_spinner=False)  # 1h TTL — weekly data
except Exception:  # pragma: no cover - only hit when streamlit isn't installed
    def _cache(func):
        return func

# --- Pinned constants (Section 2) ------------------------------------------
HISTORY_PERIOD = "3y"      # enough for 30-wk EMA warm-up + 52-wk normalization + tails
MAX_FFILL_WEEKS = 2        # hygiene rule 1: forward-fill gaps of <= 2 weeks only
MIN_HISTORY_WEEKS = 40     # hygiene rule 3: exclude tickers with < 40 weeks of history

# Daily-mode equivalents (used only by the "Daily RRG" toggle on the 4-week view).
# 2 weeks ~ 10 trading days; 40 weeks ~ 200 trading days.
MAX_FFILL_DAYS = 10
MIN_HISTORY_DAYS = 200

# --- Daily snapshot (written by scripts/daily_snapshot.py via GitHub Actions) ---
# The app reads prices from this committed file first, so page loads don't depend
# on Yahoo answering (Yahoo intermittently rate-limits shared cloud servers).
SNAPSHOT_PATH = _ROOT / "data" / "closes.csv"
# A snapshot older than this many calendar days is treated as stale and the app
# fetches live instead. 4 days covers a weekend plus a Monday holiday.
SNAPSHOT_MAX_AGE_DAYS = 4


def _download_one(ticker: str) -> pd.Series:
    """
    Download 3y of ADJUSTED daily closes for one ticker.

    Hygiene rule 4: every yfinance call is wrapped in try/except and retried
    exactly once before we give up. Raises RuntimeError on double failure so
    the caller can exclude the ticker and surface a warning (never silent).
    """
    last_err = None
    for attempt in range(2):  # first try + one retry
        try:
            df = yf.download(
                ticker,
                period=HISTORY_PERIOD,
                interval="1d",
                auto_adjust=True,   # pinned: adjusted close only
                progress=False,
                threads=False,
            )
            if df is None or df.empty:
                raise ValueError(f"yfinance returned no data for {ticker}")
            close = df["Close"]
            # yfinance sometimes returns a 1-column DataFrame for Close —
            # squeeze it down to a Series either way.
            if isinstance(close, pd.DataFrame):
                close = close.iloc[:, 0]
            close = close.dropna()
            if close.empty:
                raise ValueError(f"all-NaN close series for {ticker}")
            close.name = ticker
            # Normalize the index to tz-naive dates so every ticker aligns.
            close.index = pd.to_datetime(close.index).tz_localize(None).normalize()
            return close
        except Exception as err:  # noqa: BLE001 — any fetch failure is handled the same
            last_err = err
    raise RuntimeError(f"fetch failed twice for {ticker}: {last_err}")


@_cache
def fetch_daily_closes(tickers: tuple) -> tuple:
    """
    Fetch adjusted DAILY closes for a tuple of tickers (tuple, not list,
    so the arguments are hashable for st.cache_data).

    Returns (DataFrame of daily closes, dict {ticker: failure reason}).
    Failed tickers are reported, never silently dropped (hygiene rule 4).
    """
    series = {}
    failures = {}
    for t in tickers:
        try:
            series[t] = _download_one(t)
        except RuntimeError as err:
            failures[t] = str(err)
    if not series:
        return pd.DataFrame(), failures
    # Outer-join all tickers on date; alignment with the benchmark happens later.
    daily = pd.concat(series.values(), axis=1)
    return daily, failures


@_cache
def _read_snapshot(path_str: str, mtime: float) -> pd.DataFrame:
    """
    Read the committed daily-closes snapshot. `mtime` is part of the cache key
    so a newly written/pulled file is picked up immediately instead of serving
    a stale cached copy.
    """
    df = pd.read_csv(path_str, index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index).normalize()
    return df.sort_index()


def load_snapshot():
    """Return the snapshot DataFrame, or None if it is missing or unreadable."""
    if not SNAPSHOT_PATH.exists():
        return None
    try:
        snap = _read_snapshot(str(SNAPSHOT_PATH), SNAPSHOT_PATH.stat().st_mtime)
    except Exception:  # noqa: BLE001 — a corrupt file just means "fetch live instead"
        return None
    return snap if not snap.empty else None


def snapshot_is_fresh(snap: pd.DataFrame, today=None) -> bool:
    """True if the snapshot's last close is within SNAPSHOT_MAX_AGE_DAYS of today."""
    today = pd.Timestamp(today).normalize() if today is not None else pd.Timestamp.today().normalize()
    return (today - snap.index[-1]).days <= SNAPSHOT_MAX_AGE_DAYS


def get_daily_closes(tickers: tuple, prefer_live: bool = False):
    """
    Daily adjusted closes from the best available source:

      1. the committed daily snapshot, if it is fresh (normal case — no network);
      2. live Yahoo for anything the snapshot lacks, or for everything if the
         snapshot is stale/missing or `prefer_live` is set;
      3. if a live fetch fails, the (possibly stale) snapshot as a last resort —
         reported in info["stale_fallback"] so the UI can warn about it.

    Returns (daily DataFrame, failures dict, info dict). Failures that could not
    be rescued are still returned so the hygiene layer excludes + warns.
    """
    snap = load_snapshot()
    use_snapshot = (not prefer_live) and snap is not None and snapshot_is_fresh(snap)
    info = {
        "source": "snapshot" if use_snapshot else "live",
        "snapshot_date": snap.index[-1] if snap is not None else None,
        "stale_fallback": [],
    }

    parts = []
    if use_snapshot:
        from_snap = [t for t in tickers if t in snap.columns and snap[t].notna().any()]
        parts.append(snap[from_snap])
        need_live = tuple(t for t in tickers if t not in from_snap)
    else:
        need_live = tuple(tickers)

    failures = {}
    if need_live:
        live, failures = fetch_daily_closes(need_live)
        if not live.empty:
            parts.append(live)

    # Last resort: rescue live failures from the snapshot even if it is stale.
    if failures and snap is not None:
        rescued = [t for t in failures if t in snap.columns and snap[t].notna().any()]
        if rescued:
            parts.append(snap[rescued])
            for t in rescued:
                failures.pop(t)
            info["stale_fallback"] = rescued

    daily = pd.concat(parts, axis=1).sort_index() if parts else pd.DataFrame()
    return daily, failures, info


def to_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    """
    Resample daily closes to WEEKLY (Friday) closes — the pinned RRG standard.
    .last() takes the final available close of each week, so a holiday Friday
    correctly falls back to Thursday's close.
    """
    return daily.resample("W-FRI").last()


def _max_consecutive_nans(s: pd.Series) -> int:
    """
    Length of the longest run of consecutive NaNs INSIDE a series
    (between its first and last valid observations). Leading NaNs are just
    shorter history — handled by the minimum-history rule, not the gap rule.
    """
    s = s.loc[s.first_valid_index():s.last_valid_index()]
    if s.empty:
        return 0
    is_nan = s.isna()
    # Group consecutive equal values and measure the longest NaN group.
    groups = (is_nan != is_nan.shift()).cumsum()
    runs = is_nan.groupby(groups).sum()
    return int(runs.max()) if not runs.empty else 0


def prepare_level(tickers: tuple, benchmark: str, daily_mode: bool = False,
                  prefer_live: bool = False):
    """
    Fetch + clean everything needed to compute an RRG for one level of the
    pyramid. This is THE entry point the UI and validation scripts use.

    Returns:
      prices    — DataFrame of clean weekly (or daily) closes, one col/ticker
      bench     — matching Series for the benchmark
      excluded  — dict {ticker: human-readable reason} for the warning chips
      as_of     — date of the benchmark's last actual DAILY close. (The last
                  weekly bar is labeled with the upcoming Friday while the
                  week is still in progress, so the label is not the as-of date.)
      info      — data-source details from get_daily_closes (snapshot vs live)

    Raises RuntimeError if the BENCHMARK itself can't be fetched — nothing
    can be computed without it, so that failure must be loud.
    """
    # Thresholds switch together when the daily engine is requested.
    max_gap = MAX_FFILL_DAYS if daily_mode else MAX_FFILL_WEEKS
    min_history = MIN_HISTORY_DAYS if daily_mode else MIN_HISTORY_WEEKS

    all_tickers = tuple(dict.fromkeys(list(tickers) + [benchmark]))  # dedupe, keep order
    daily, failures, info = get_daily_closes(all_tickers, prefer_live)

    if benchmark in failures or benchmark not in getattr(daily, "columns", []):
        raise RuntimeError(
            f"Benchmark {benchmark} could not be fetched — cannot compute RRG. "
            f"({failures.get(benchmark, 'missing from result')})"
        )

    data = daily if daily_mode else to_weekly(daily)

    # Hygiene rule 2 (the #1 silent-error source): align every series on the
    # intersection of dates where the BENCHMARK has data, BEFORE computing RS.
    bench = data[benchmark].dropna()
    data = data.reindex(bench.index)

    excluded = {t: f"data fetch failed: {r}" for t, r in failures.items()}
    clean_cols = {}
    for t in tickers:
        if t in excluded:
            continue  # already excluded by fetch failure
        if t == benchmark:
            continue  # benchmark handled separately; a level never plots itself
        s = data[t]

        # Hygiene rule 3: require enough history for MA warm-up.
        if s.notna().sum() < min_history:
            unit = "days" if daily_mode else "weeks"
            excluded[t] = f"only {int(s.notna().sum())} {unit} of history (< {min_history})"
            continue

        # Hygiene rule 1: gaps of <= 2 weeks get forward-filled; anything
        # longer means the data is untrustworthy for this render — exclude.
        gap = _max_consecutive_nans(s)
        if gap > max_gap:
            unit = "days" if daily_mode else "weeks"
            excluded[t] = f"gap of {gap} consecutive missing {unit} (> {max_gap})"
            continue
        clean_cols[t] = s.ffill(limit=max_gap)

    prices = pd.DataFrame(clean_cols, index=bench.index)
    daily_bench = daily[benchmark].dropna()
    as_of = daily_bench.index[-1] if len(daily_bench) else None
    return prices, bench, excluded, as_of, info
