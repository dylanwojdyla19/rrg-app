"""
engine/calc.py — The RRG calculation engine. Section 3 formulas, nothing else.

Pinned pipeline (all series weekly adjusted closes, index-aligned with benchmark):
  1. RS           = price_security / price_benchmark
  2. rs_ratio_raw = 100 * EMA(RS,10) / EMA(RS,30)
     RS_Ratio     = 100 + rolling-52wk z-score of rs_ratio_raw (min_periods=26)
  3. roc          = RS_Ratio.diff(10)
     RS_Momentum  = 100 + rolling-52wk z-score of roc (min_periods=26)

If validation fails, tune ONLY: EMA spans (12/26, or SMA 10/30) and ROC window
(9 or 14), and log every change in VALIDATION.md.
"""

import numpy as np
import pandas as pd

# --- Pinned parameters (Section 3). Tuning candidates listed in docstring. ---
EMA_SHORT = 10       # short EMA span on raw RS
EMA_LONG = 30        # long EMA span on raw RS
ROC_WINDOW = 10      # RS-Momentum = rate of change of RS-Ratio over this window
NORM_WINDOW = 52     # rolling z-score window (weeks)
NORM_MIN_PERIODS = 26  # min observations before the z-score starts producing values

# Warm-up arithmetic (0-based positions from a ticker's first valid observation):
#   RS_Ratio needs NORM_MIN_PERIODS obs of rs_ratio_raw     -> first valid at 25
#   roc = RS_Ratio.diff(10)                                  -> first valid at 35
#   RS_Momentum needs NORM_MIN_PERIODS non-NaN rocs          -> first valid at 60
# So after 61 observations BOTH outputs must be NaN-free. The hygiene layer's
# 40-week minimum admits tickers whose momentum is still warming up; the UI
# simply doesn't plot rows that are still NaN inside this window.
WARMUP_OBS = (NORM_MIN_PERIODS - 1) + ROC_WINDOW + (NORM_MIN_PERIODS - 1) + 1  # = 61


def _rolling_zscore_around_100(x: pd.Series) -> pd.Series:
    """
    Pinned normalization: rolling z-score over NORM_WINDOW observations,
    rescaled to mean 100 / std 1:   100 + (x - rolling_mean) / rolling_std

    Edge case that the invariant tests demand we get right: a security whose
    price series EQUALS the benchmark has a perfectly constant input, so the
    rolling std is 0 and (x - mu)/sd would be 0/0 = NaN. Mathematically that
    security has zero deviation from its mean, so its z-score is 0 and it must
    sit at exactly 100. We encode that explicitly.
    """
    mu = x.rolling(NORM_WINDOW, min_periods=NORM_MIN_PERIODS).mean()
    sd = x.rolling(NORM_WINDOW, min_periods=NORM_MIN_PERIODS).std()
    z = (x - mu) / sd
    # Where the window is valid (mu exists) but sd == 0, deviation is 0 by
    # definition. Only patch those cells — genuine warm-up NaNs stay NaN.
    z = z.mask((sd == 0) & mu.notna(), 0.0)
    return 100 + z


def rs_ratio_series(price: pd.Series, benchmark: pd.Series) -> pd.Series:
    """RS-Ratio for ONE security vs its benchmark (pinned Steps 1-2)."""
    # Step 1 — raw relative strength.
    rs = price / benchmark

    # Step 2 — MACD-style EMA ratio, scaled around 100. Pinned: EMA, not SMA.
    ema_short = rs.ewm(span=EMA_SHORT, adjust=False).mean()
    ema_long = rs.ewm(span=EMA_LONG, adjust=False).mean()
    rs_ratio_raw = 100 * (ema_short / ema_long)

    return _rolling_zscore_around_100(rs_ratio_raw)


def rs_momentum_series(rs_ratio: pd.Series) -> pd.Series:
    """RS-Momentum for ONE security (pinned Step 3): ROC of RS-Ratio, same normalization."""
    roc = rs_ratio.diff(ROC_WINDOW)
    return _rolling_zscore_around_100(roc)


def compute_rrg(prices: pd.DataFrame, benchmark: pd.Series):
    """
    Compute RS-Ratio and RS-Momentum for every column of `prices` vs `benchmark`.

    Inputs MUST already be index-aligned and hygiene-cleaned by engine/data.py.
    Returns (rs_ratio_df, rs_momentum_df) with the same shape as `prices`.

    Hygiene rule 5: asserts loudly that there are no NaNs after each ticker's
    warm-up period — a NaN there means silent index misalignment upstream.
    """
    if not prices.index.equals(benchmark.index):
        raise ValueError(
            "prices and benchmark are not index-aligned — engine/data.prepare_level "
            "must be used before compute_rrg (hygiene rule 2)."
        )

    ratio = pd.DataFrame(index=prices.index)
    momentum = pd.DataFrame(index=prices.index)
    for col in prices.columns:
        r = rs_ratio_series(prices[col], benchmark)
        ratio[col] = r
        momentum[col] = rs_momentum_series(r)

    _assert_no_nans_after_warmup(ratio, "RS_Ratio")
    _assert_no_nans_after_warmup(momentum, "RS_Momentum")
    return ratio, momentum


def _assert_no_nans_after_warmup(df: pd.DataFrame, label: str) -> None:
    """
    Hygiene rule 5: after WARMUP_OBS observations from a ticker's first valid
    data point, its RRG values must be fully populated. Raise loudly if not.
    """
    for col in df.columns:
        first = df[col].first_valid_index()
        if first is None:
            # A column with no valid values at all means the input was shorter
            # than the warm-up — that ticker should have been excluded upstream,
            # but it isn't a *misalignment* error, so we let the UI drop it.
            continue
        pos = df.index.get_loc(first)
        # Everything from (first valid + remaining warm-up) onward must be non-NaN.
        tail = df[col].iloc[pos + WARMUP_OBS:]
        if tail.isna().any():
            bad = tail[tail.isna()].index[0]
            raise AssertionError(
                f"{label} for {col} has NaN at {bad} AFTER the warm-up period — "
                "this indicates silent data misalignment. Refusing to render."
            )


def rotation_velocity(rs_ratio: pd.DataFrame, lookback: int = 4) -> pd.Series:
    """
    4-week change in RS-Ratio for the summary table ('rotation velocity').
    Positive = strengthening vs benchmark over the last `lookback` observations.
    """
    if len(rs_ratio) <= lookback:
        return pd.Series(np.nan, index=rs_ratio.columns)
    return rs_ratio.iloc[-1] - rs_ratio.iloc[-1 - lookback]
