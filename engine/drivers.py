"""
engine/drivers.py — "what's driving this ETF": performance attribution of a
fund's move to its top holdings.

For each top holding over a period:
    return_i        = price_end / price_start - 1
    contribution_i  = weight_i × return_i         (in percentage points of the fund's return)

This is the standard first-order attribution: a 15%-weight stock that rose 10%
added ~1.5 points to the fund. It uses today's weights for the whole period, so
it's an approximation (weights drift as prices move), which is fine for
"who is moving this sector" — not for exact accounting.
"""

import pandas as pd


def period_start(as_of: pd.Timestamp, timeframe: str) -> pd.Timestamp:
    """Start date for a timeframe label used in the app ('12 Weeks', 'YTD', ...)."""
    if timeframe == "YTD":
        return pd.Timestamp(year=as_of.year - 1, month=12, day=31)
    weeks = int(timeframe.split()[0])
    return as_of - pd.Timedelta(weeks=weeks)


def _price_on_or_before(s: pd.Series, when: pd.Timestamp):
    s = s.dropna()
    s = s[s.index <= when]
    return s.iloc[-1] if len(s) else None


def period_return(s: pd.Series, start: pd.Timestamp, end: pd.Timestamp):
    """Simple return between the last closes on/before start and end, or None."""
    p0, p1 = _price_on_or_before(s, start), _price_on_or_before(s, end)
    if p0 is None or p1 is None or p0 == 0:
        return None
    return p1 / p0 - 1


def contributions(holdings: list, closes: pd.DataFrame,
                  start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """
    One row per holding: Ticker, Name, Weight, Return, Contribution.
    Holdings without price data get NaN return/contribution (shown, not dropped).
    Sorted by contribution, biggest positive driver first.
    """
    rows = []
    for h in holdings:
        t = h["ticker"]
        r = period_return(closes[t], start, end) if t in closes.columns else None
        rows.append({
            "Ticker": t,
            "Name": h.get("name", ""),
            "Weight": h["weight"],
            "Return": r,
            "Contribution": h["weight"] * r if r is not None else None,
        })
    df = pd.DataFrame(rows, columns=["Ticker", "Name", "Weight", "Return", "Contribution"])
    df[["Return", "Contribution"]] = df[["Return", "Contribution"]].astype(float)
    return df.sort_values("Contribution", ascending=False, na_position="last",
                          ignore_index=True)
