"""
tests/test_snapshot.py — offline tests for the daily data pipeline.

No network: Yahoo fetches are replaced with stubs, and the snapshot / history
files live in pytest's temporary directory.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import data, snapshot  # noqa: E402


def _write_snapshot(path: Path, end: pd.Timestamp, tickers=("SPY", "XLK")) -> pd.DataFrame:
    """A small synthetic daily-closes snapshot ending on `end`."""
    idx = pd.bdate_range(end=end, periods=300)
    rng = np.random.default_rng(0)
    df = pd.DataFrame(
        {t: 100 * np.exp(np.cumsum(rng.normal(0, 0.01, len(idx)))) for t in tickers},
        index=idx,
    )
    df.to_csv(path, float_format="%.4f", index_label="date")
    return df


@pytest.fixture
def tmp_snapshot(tmp_path, monkeypatch):
    path = tmp_path / "closes.csv"
    monkeypatch.setattr(data, "SNAPSHOT_PATH", path)
    return path


def _no_network(tickers):
    raise AssertionError(f"tried to hit Yahoo for {tickers} — should have used the snapshot")


def test_fresh_snapshot_is_used_without_network(tmp_snapshot, monkeypatch):
    _write_snapshot(tmp_snapshot, pd.Timestamp.today().normalize())
    monkeypatch.setattr(data, "fetch_daily_closes", _no_network)
    daily, failures, info = data.get_daily_closes(("SPY", "XLK"))
    assert info["source"] == "snapshot"
    assert failures == {}
    assert list(daily.columns) == ["SPY", "XLK"]


def test_stale_snapshot_rescues_failed_live_fetch(tmp_snapshot, monkeypatch):
    _write_snapshot(tmp_snapshot, pd.Timestamp.today().normalize() - pd.Timedelta(days=30))
    # Live fetch "fails" for everything, as when Yahoo blocks a cloud server.
    monkeypatch.setattr(data, "fetch_daily_closes",
                        lambda t: (pd.DataFrame(), {x: "blocked" for x in t}))
    daily, failures, info = data.get_daily_closes(("SPY", "XLK"))
    assert info["source"] == "live"                  # snapshot was too old to prefer
    assert sorted(info["stale_fallback"]) == ["SPY", "XLK"]  # …but it saved the render
    assert failures == {}
    assert not daily.empty


def test_unrescuable_failure_is_reported_not_dropped(tmp_snapshot, monkeypatch):
    _write_snapshot(tmp_snapshot, pd.Timestamp.today().normalize(), tickers=("SPY",))
    monkeypatch.setattr(data, "fetch_daily_closes",
                        lambda t: (pd.DataFrame(), {x: "blocked" for x in t}))
    _, failures, _ = data.get_daily_closes(("SPY", "NEWTICKER"))
    assert "NEWTICKER" in failures                   # hygiene rule 4: never silent


def test_history_append_is_idempotent(tmp_path):
    path = tmp_path / "rotation_history.csv"
    day = pd.Timestamp("2026-11-24")
    rows = pd.DataFrame(
        [[day, "sectors", "SPY", "XLK", 98.3, 99.0, "Lagging", "daily"]],
        columns=snapshot.HISTORY_COLUMNS,
    )
    snapshot.append_history(rows, path)
    snapshot.append_history(rows, path)              # same day re-run (holiday / manual)
    rows2 = rows.assign(rs_ratio=98.5)               # corrected value for the same day
    total = snapshot.append_history(rows2, path)
    out = pd.read_csv(path)
    assert total == 1 and len(out) == 1
    assert out.loc[0, "rs_ratio"] == pytest.approx(98.5)


def test_snapshot_refuses_mass_failure(monkeypatch):
    monkeypatch.setattr(data, "fetch_daily_closes",
                        lambda t: (pd.DataFrame(), {x: "blocked" for x in t}))
    with pytest.raises(RuntimeError):
        snapshot.build_snapshot(("SPY", "XLK", "XLF"), previous=None)
