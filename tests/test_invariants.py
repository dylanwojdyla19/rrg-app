"""
tests/test_invariants.py — Section 7, Test 3: mathematical invariants (pytest).

These run on SYNTHETIC data (no network) so they are deterministic and can run
in CI. The invariants pinned by the master prompt:

  1. A security whose price series equals the benchmark's sits at EXACTLY (100, 100).
  2. Means of RS_Ratio and RS_Momentum across a full 52-week window ~ 100 (+/- 0.5).
  3. No NaNs after warm-up.
  4. No lookahead: the value at week t uses only data <= t (truncation test).
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Make `engine` importable when pytest is run from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import calc  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic data builders — weekly Friday index, ~3y (160 weeks) like production.
# ---------------------------------------------------------------------------
N_WEEKS = 160


def weekly_index(n=N_WEEKS):
    return pd.date_range("2023-01-06", periods=n, freq="W-FRI")


def random_walk_benchmark(seed=42, n=N_WEEKS):
    """A benchmark that behaves like an index: geometric random walk with drift."""
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0015, 0.02, n)  # ~8%/yr drift, ~14% ann. vol
    return pd.Series(100 * np.exp(np.cumsum(rets)), index=weekly_index(n), name="BENCH")


def stationary_satellite(benchmark, seed, rel_vol=0.03):
    """
    A security whose relative strength vs the benchmark is STATIONARY
    (mean-reverting noise, no persistent trend). This is the right synthetic
    input for the mean~100 invariant: the z-scored outputs should oscillate
    around 100 rather than ride a trend.
    """
    rng = np.random.default_rng(seed)
    # AR(1) log-relative-strength, strongly mean-reverting (phi=0.6). The
    # +/-0.5 band is pinned by the spec, so the synthetic input must actually
    # BE stationary — a near-unit-root series would trend for quarters at a
    # time and legitimately hold a z-score away from 0.
    rs_log = np.zeros(len(benchmark))
    for i in range(1, len(benchmark)):
        rs_log[i] = 0.6 * rs_log[i - 1] + rng.normal(0, rel_vol)
    return benchmark * np.exp(rs_log)


# ---------------------------------------------------------------------------
# Invariant 1 — identical-to-benchmark security sits at exactly (100, 100)
# ---------------------------------------------------------------------------
def test_benchmark_identical_security_is_exactly_100_100():
    bench = random_walk_benchmark()
    prices = pd.DataFrame({"CLONE": bench.values}, index=bench.index)
    ratio, momentum = calc.compute_rrg(prices, bench)

    after_warmup_ratio = ratio["CLONE"].dropna()
    after_warmup_mom = momentum["CLONE"].dropna()
    assert len(after_warmup_ratio) > 0, "no RS_Ratio values produced at all"
    assert len(after_warmup_mom) > 0, "no RS_Momentum values produced at all"
    # Exactly 100 (tiny float tolerance): RS is constant, so z-score must be 0.
    assert np.allclose(after_warmup_ratio.values, 100.0, atol=1e-9)
    assert np.allclose(after_warmup_mom.values, 100.0, atol=1e-9)


# ---------------------------------------------------------------------------
# Invariant 2 — 52-week means of both outputs ~ 100 (+/- 0.5)
# ---------------------------------------------------------------------------
def test_means_over_52_weeks_are_near_100():
    bench = random_walk_benchmark()
    prices = pd.DataFrame(
        {f"S{i}": stationary_satellite(bench, seed=100 + i) for i in range(6)},
        index=bench.index,
    )
    ratio, momentum = calc.compute_rrg(prices, bench)

    for col in prices.columns:
        r_mean = ratio[col].iloc[-52:].mean()
        m_mean = momentum[col].iloc[-52:].mean()
        assert abs(r_mean - 100) <= 0.5, f"RS_Ratio 52w mean off for {col}: {r_mean:.3f}"
        assert abs(m_mean - 100) <= 0.5, f"RS_Momentum 52w mean off for {col}: {m_mean:.3f}"


# ---------------------------------------------------------------------------
# Invariant 3 — no NaNs after warm-up (and the assertion actually fires)
# ---------------------------------------------------------------------------
def test_no_nans_after_warmup():
    bench = random_walk_benchmark()
    prices = pd.DataFrame(
        {f"S{i}": stationary_satellite(bench, seed=200 + i) for i in range(4)},
        index=bench.index,
    )
    ratio, momentum = calc.compute_rrg(prices, bench)
    # compute_rrg would have raised already; double-check from the outside too.
    assert not ratio.iloc[calc.WARMUP_OBS:].isna().any().any()
    assert not momentum.iloc[calc.WARMUP_OBS:].isna().any().any()


def test_nan_after_warmup_raises_loudly():
    """Hygiene rule 5 must RAISE, not warn, when post-warm-up NaNs appear."""
    bench = random_walk_benchmark()
    df = pd.DataFrame({"X": bench.values * 1.01}, index=bench.index)
    ratio, _ = calc.compute_rrg(df, bench)
    ratio.iloc[-3, 0] = np.nan  # sabotage a post-warm-up value
    with pytest.raises(AssertionError):
        calc._assert_no_nans_after_warmup(ratio, "RS_Ratio")


# ---------------------------------------------------------------------------
# Invariant 4 — no lookahead: truncating the future never changes the past
# ---------------------------------------------------------------------------
def test_no_lookahead():
    bench = random_walk_benchmark()
    prices = pd.DataFrame(
        {"A": stationary_satellite(bench, seed=7), "B": stationary_satellite(bench, seed=8)},
        index=bench.index,
    )
    full_ratio, full_mom = calc.compute_rrg(prices, bench)

    # Truncate at week t = 120 and recompute; every value at or before t must
    # be IDENTICAL. Any difference means a formula peeked at future data.
    t = 120
    trunc_ratio, trunc_mom = calc.compute_rrg(prices.iloc[:t], bench.iloc[:t])
    pd.testing.assert_frame_equal(full_ratio.iloc[:t], trunc_ratio)
    pd.testing.assert_frame_equal(full_mom.iloc[:t], trunc_mom)


# ---------------------------------------------------------------------------
# Bonus: misaligned indexes must be rejected loudly (hygiene rule 2 guard)
# ---------------------------------------------------------------------------
def test_misaligned_index_rejected():
    bench = random_walk_benchmark()
    prices = pd.DataFrame({"A": bench.values * 1.02}, index=bench.index)
    with pytest.raises(ValueError):
        calc.compute_rrg(prices.iloc[5:], bench)  # deliberately misaligned
