"""
app.py — Streamlit UI for the RRG sector-rotation app (Section 6, pinned behavior).

Top-down drill path: Asset Classes -> Sectors -> Subsectors -> Stocks.
The math NEVER changes with the timeframe filter — it only changes how many
trailing weekly observations (the "tail") are drawn. The one exception is the
clearly-labeled "Daily RRG" toggle on the 4-week view, which reruns the whole
engine on daily closes.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# Make `engine` importable no matter which directory streamlit was launched from.
sys.path.insert(0, str(Path(__file__).parent))

from engine import calc, data, quadrant  # noqa: E402

# ---------------------------------------------------------------------------
# Page + universe config
# ---------------------------------------------------------------------------
st.set_page_config(page_title="RRG — Sector Rotation", layout="wide")

# The universe lives in config (data), never hardcoded in logic (Section 5).
UNIVERSE = json.loads((Path(__file__).parent / "config" / "universe.json").read_text())
NODES = UNIVERSE["nodes"]
ROOT_KEY = UNIVERSE["root"]

# Footer disclosure (Section 7). Update after each StockCharts comparison run:
#   python validation/validate.py --test 1   -> compare -> edit these two lines.
VALIDATED_ON = "2026-07-11 (engine run; StockCharts side-by-side pending)"
AGREEMENT = "_"  # e.g. "10" once the owner has done the screenshot comparison

# Distinct series colors (quadrant colors are reserved for the background tints).
PALETTE = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b",
    "#e377c2", "#7f7f7f", "#bcbd22", "#17becf", "#aec7e8", "#ffbb78",
]

# ---------------------------------------------------------------------------
# Navigation state: a list of (node_key, crumb_label) tuples, root first.
# Drilling appends; clicking a breadcrumb truncates.
# ---------------------------------------------------------------------------
if "path" not in st.session_state:
    st.session_state.path = [(ROOT_KEY, NODES[ROOT_KEY]["label"])]

node_key, _ = st.session_state.path[-1]
node = NODES[node_key]
members = node["members"]
by_ticker = {m["ticker"]: m for m in members}


def drill_into(member: dict) -> None:
    """Navigate one level down (called by both click-on-dot and the selectbox)."""
    st.session_state.path.append((member["drill"], member["name"]))


# ---------------------------------------------------------------------------
# Breadcrumb bar (each segment clickable to jump back up)
# ---------------------------------------------------------------------------
crumb_cols = st.columns([1] * len(st.session_state.path) + [6])
for i, (key, label) in enumerate(st.session_state.path):
    if crumb_cols[i].button(
        label, key=f"crumb_{i}", type="tertiary",
        disabled=(i == len(st.session_state.path) - 1),  # current level isn't a link
    ):
        st.session_state.path = st.session_state.path[: i + 1]
        st.rerun()

st.title(node["label"])

# ---------------------------------------------------------------------------
# Timeframe filter — PINNED SEMANTICS: changes ONLY the tail length drawn,
# never the EMA/ROC calculation windows (quadrant positions stay consistent
# between views). "4 Weeks" additionally offers the daily-engine toggle.
# ---------------------------------------------------------------------------
tf_col, daily_col = st.columns([3, 1])
timeframe = tf_col.radio(
    "Tail length (rotation history shown — the math never changes)",
    ["12 Weeks", "26 Weeks", "YTD", "4 Weeks"],
    horizontal=True,
)
daily_mode = False
if timeframe == "4 Weeks":
    daily_mode = daily_col.toggle(
        "Daily RRG (short-term)",
        help="Recomputes the whole engine on DAILY closes with the same formulas. "
        "Short-term view — weekly is the RRG standard.",
    )

# ---------------------------------------------------------------------------
# Sidebar: benchmark override, refresh, exclusions, data timestamp
# ---------------------------------------------------------------------------
st.sidebar.header("Settings")
default_bench = node["benchmark"]
all_tickers = sorted({m["ticker"] for n in NODES.values() for m in n["members"]}
                     | {n["benchmark"] for n in NODES.values()})
bench_choice = st.sidebar.selectbox(
    "Benchmark",
    [f"Default ({default_bench})"] + all_tickers,
    key=f"bench_{node_key}",  # keyed per level so the override doesn't leak across levels
)
benchmark = default_bench if bench_choice.startswith("Default") else bench_choice

if st.sidebar.button("🔄 Refresh data (clear cache)"):
    st.cache_data.clear()
    st.rerun()

# ---------------------------------------------------------------------------
# Fetch + compute (all hygiene rules live in engine/data.py)
# ---------------------------------------------------------------------------
tickers = tuple(m["ticker"] for m in members if m["ticker"] != benchmark)
try:
    prices, bench_series, excluded, as_of = data.prepare_level(tickers, benchmark, daily_mode)
except RuntimeError as err:
    st.error(str(err))
    st.stop()

if as_of is not None:
    st.sidebar.caption(f"Data as of **{as_of.date()}** · weekly = Friday close")

# Hygiene rule 1/3/4: excluded tickers get a visible warning chip — never silent.
if excluded:
    st.warning("Excluded from this render: " +
               " · ".join(f"**{t}** ({reason})" for t, reason in excluded.items()))

if prices.empty:
    st.error("No tickers survived data hygiene for this level.")
    st.stop()

ratio, momentum = calc.compute_rrg(prices, bench_series)

# ---------------------------------------------------------------------------
# Tail length in observations (weekly bars, or daily bars in daily mode)
# ---------------------------------------------------------------------------
if timeframe == "YTD":
    jan1 = pd.Timestamp(year=ratio.index[-1].year, month=1, day=1)
    tail_len = max(int((ratio.index >= jan1).sum()), 2)
elif timeframe == "4 Weeks":
    tail_len = 20 if daily_mode else 4  # 4 weeks ~ 20 trading days
else:
    tail_len = int(timeframe.split()[0])

# ---------------------------------------------------------------------------
# Main chart: X = RS-Ratio, Y = RS-Momentum, axes cross at (100,100),
# quadrant background tints, dot + label + fading tail per security.
# ---------------------------------------------------------------------------
fig = go.Figure()

# Collect the points that will actually be drawn to size the axes.
plotted = []
for t in ratio.columns:
    xy = pd.DataFrame({"x": ratio[t], "y": momentum[t]}).dropna().tail(tail_len)
    if len(xy) >= 1:
        plotted.append((t, xy))

if not plotted:
    st.error("Nothing to plot — all series are still inside the warm-up window.")
    st.stop()

# Square-ish axes centered on (100,100): take the largest deviation and pad it.
all_vals = pd.concat([xy for _, xy in plotted])
span = max(
    abs(all_vals["x"] - 100).max(),
    abs(all_vals["y"] - 100).max(),
) + 0.6
lo, hi = 100 - span, 100 + span

# Quadrant background tints (Section 4 colors at low opacity) + crosshair.
TINTS = {  # (x0, x1, y0, y1) per quadrant
    "Leading": (100, hi, 100, hi),
    "Weakening": (100, hi, lo, 100),
    "Lagging": (lo, 100, lo, 100),
    "Improving": (lo, 100, 100, hi),
}
for qname, (x0, x1, y0, y1) in TINTS.items():
    fig.add_shape(type="rect", x0=x0, x1=x1, y0=y0, y1=y1, layer="below",
                  fillcolor=quadrant.QUADRANT_COLORS[qname], opacity=0.08, line_width=0)
    # Corner label so the quadrants are readable at a glance.
    fig.add_annotation(x=(x0 + x1) / 2, y=(y0 + y1) / 2 + (y1 - y0) * 0.42,
                       text=f"<b>{qname}</b>", showarrow=False,
                       font=dict(color=quadrant.QUADRANT_COLORS[qname], size=13),
                       opacity=0.7)
fig.add_hline(y=100, line_width=1, line_color="#888")
fig.add_vline(x=100, line_width=1, line_color="#888")

# One trace per security: smoothed line tail + markers fading older -> newer,
# ticker label pinned to the newest point (the "head"), and an arrow showing
# the direction of travel — the rotation cue.
for i, (t, xy) in enumerate(plotted):
    color = PALETTE[i % len(PALETTE)]
    n = len(xy)
    # Per-point customdata: [ticker, date]. The ticker drives click-to-drill;
    # the date shows in the hover tooltip so every tail dot is identifiable.
    dates = xy.index.strftime("%b %d, %Y")
    fig.add_trace(go.Scatter(
        x=xy["x"], y=xy["y"],
        mode="lines+markers+text",
        name=t,
        line=dict(color=color, width=2, shape="spline", smoothing=1.0),
        marker=dict(
            color=color,
            size=np.linspace(6, 14, n),            # older = small, newest = big
            opacity=np.linspace(0.3, 1.0, n),      # older = faded, newest = solid
        ),
        text=[""] * (n - 1) + [f"<b>{t}</b>"],     # label only the head
        textposition="top center",
        textfont=dict(color=color, size=13),
        customdata=np.column_stack([[t] * n, dates]),
        hovertemplate=(f"<b>{t}</b> · week ending %{{customdata[1]}}<br>"
                       "RS-Ratio %{x:.2f} · RS-Momentum %{y:.2f}<extra></extra>"),
    ))
    # Direction arrow: consecutive weekly points sit almost on top of each
    # other, so an arrow BETWEEN them is invisible. Instead, project a short
    # arrow FORWARD from the head along the last move's direction — a clear
    # "this is where it's rotating" cue.
    if n >= 2:
        dx = xy["x"].iloc[-1] - xy["x"].iloc[-2]
        dy = xy["y"].iloc[-1] - xy["y"].iloc[-2]
        norm = (dx**2 + dy**2) ** 0.5
        if norm > 0:
            reach = span * 0.11  # arrow length ~11% of the plot's half-width
            fig.add_annotation(
                ax=xy["x"].iloc[-1], ay=xy["y"].iloc[-1],           # from the head…
                x=xy["x"].iloc[-1] + dx / norm * reach,             # …out along the
                y=xy["y"].iloc[-1] + dy / norm * reach,             # direction of travel
                xref="x", yref="y", axref="x", ayref="y",
                showarrow=True, arrowhead=3, arrowsize=1.5, arrowwidth=1.4,
                arrowcolor=color, standoff=0, startstandoff=9, text="",
            )

fig.update_layout(
    height=800,
    showlegend=False,  # heads are labeled directly; the table below lists everything
    xaxis=dict(title="JdK RS-Ratio (relative trend)", range=[lo, hi], zeroline=False),
    # scaleanchor keeps 1 unit of x = 1 unit of y, so rotation reads as
    # rotation instead of being stretched by the plot's aspect ratio.
    yaxis=dict(title="JdK RS-Momentum (rate of change)", range=[lo, hi],
               zeroline=False, scaleanchor="x", scaleratio=1),
    margin=dict(l=10, r=10, t=30, b=10),
    clickmode="event+select",
    dragmode="pan",
)

engine_label = "Daily RRG (short-term)" if daily_mode else "Weekly RRG"
st.caption(f"{engine_label} · benchmark **{benchmark}** · tail = last {tail_len} "
           f"{'trading days' if daily_mode else 'weekly closes'}")

# on_select="rerun": clicking a dot reruns the script with the selection so we
# can drill down. The chart key includes the level so selections don't carry
# over (and re-trigger navigation) after we move to a new level.
event = st.plotly_chart(
    fig, use_container_width=True,
    on_select="rerun", selection_mode="points",
    key=f"chart_{node_key}_{timeframe}_{daily_mode}",
)

# Click-to-drill bridge: plotly.js reliably fires plotly_click when a dot is
# clicked, but does not reliably promote that click to a *selection* — and
# Streamlit's on_select pipeline only reruns on selections. This tiny
# same-origin snippet re-emits every plotly_click as a plotly_selected event,
# which Streamlit's listener then forwards to Python (verified end-to-end).
import streamlit.components.v1 as components  # noqa: E402 — used only for this shim

components.html(
    """
    <script>
    const attach = () => {
        const gd = window.parent.document.querySelector('.js-plotly-plot');
        // The chart may not have mounted yet (or was replaced by a rerun) —
        // keep retrying; the __rrgBridge flag prevents double-attachment.
        if (!gd || !gd.on) { setTimeout(attach, 400); return; }
        if (gd.__rrgBridge) return;
        gd.__rrgBridge = true;
        gd.on('plotly_click', (d) => gd.emit('plotly_selected', { points: d.points }));
    };
    attach();
    </script>
    """,
    height=0,
)

# --- Drill-down 1/2: click on a dot -----------------------------------------
sel_points = (event or {}).get("selection", {}).get("points", [])
if sel_points:
    pt = sel_points[0]
    # Streamlit versions differ on the key casing for custom data, and some
    # only reliably provide the trace index — try all three, most direct first.
    ticker_clicked = pt.get("customdata") or pt.get("custom_data")
    if isinstance(ticker_clicked, list):  # plotly wraps per-point customdata in a list
        ticker_clicked = ticker_clicked[0] if ticker_clicked else None
    if ticker_clicked is None and pt.get("curve_number") is not None:
        ticker_clicked = plotted[pt["curve_number"]][0]  # trace order == plotted order
    clicked = by_ticker.get(ticker_clicked)
    if clicked and "drill" in clicked:
        drill_into(clicked)
        st.rerun()
    elif clicked:
        st.toast(f"{clicked['ticker']} is a bottom-level security — nothing to drill into.")

# --- Drill-down 2/2: selectbox listing this level's securities --------------
drillable = [m for m in members if "drill" in m]
if drillable:
    choice = st.selectbox(
        "Drill down into…",
        drillable,
        index=None,
        placeholder="Choose a security to drill into (or click its dot above)",
        format_func=lambda m: f"{m['ticker']} — {m['name']}",
        key=f"drill_{node_key}",  # keyed per level so it resets after navigating
    )
    if choice is not None:
        drill_into(choice)
        st.rerun()

# ---------------------------------------------------------------------------
# Table: current universe sorted by RS-Ratio desc, with rotation velocity
# and a direction arrow (which way the head moved over the last observation).
# ---------------------------------------------------------------------------
velocity_lookback = 20 if daily_mode else 4  # "4-week" change in both engines
velocity = calc.rotation_velocity(ratio, lookback=velocity_lookback)


def _heading_arrow(t: str) -> str:
    """Compass arrow for the last week's movement on the RRG plane."""
    r, m = ratio[t].dropna(), momentum[t].dropna()
    if len(r) < 2 or len(m) < 2:
        return ""
    dx, dy = r.iloc[-1] - r.iloc[-2], m.iloc[-1] - m.iloc[-2]
    return {(1, 1): "↗", (1, 0): "↘", (0, 1): "↖", (0, 0): "↙"}[(int(dx > 0), int(dy > 0))]


rows = []
for t in ratio.columns:
    r, m = ratio[t].iloc[-1], momentum[t].iloc[-1]
    if pd.isna(r) or pd.isna(m):
        continue  # still in warm-up — excluded from the chart too
    rows.append({
        "Ticker": t,
        "Name": by_ticker.get(t, {}).get("name", ""),
        "Quadrant": quadrant.classify(r, m),
        "Heading": _heading_arrow(t),
        "RS-Ratio": round(r, 2),
        "RS-Momentum": round(m, 2),
        "Δ RS-Ratio (4w)": round(velocity[t], 2) if pd.notna(velocity[t]) else None,
    })
table = pd.DataFrame(rows).sort_values("RS-Ratio", ascending=False, ignore_index=True)

# Color the quadrant column with the pinned Section-4 colors so the table
# reads at a glance like the chart does.
styled = table.style.map(
    lambda q: f"color: {quadrant.QUADRANT_COLORS.get(q, '')}; font-weight: 700;",
    subset=["Quadrant"],
).format({"RS-Ratio": "{:.2f}", "RS-Momentum": "{:.2f}", "Δ RS-Ratio (4w)": "{:+.2f}"})
st.dataframe(styled, use_container_width=True, hide_index=True)

with st.expander("How to read this chart"):
    st.markdown(
        """
- Everything is measured **relative to the benchmark** — not absolute price. Right of
  center = stronger trend than the benchmark; above center = that strength is accelerating.
- Securities tend to rotate **clockwise**: Improving (↖ blue) → Leading (↗ green) →
  Weakening (↘ yellow) → Lagging (↙ red) → back to Improving.
- The **tail** is the trail of recent weekly readings (small/faded = older). The arrow on
  the big dot shows the current direction of travel. Hover any dot for its date and values.
- **Click a dot** (or use the dropdown) to drill into what's inside it; use the
  breadcrumbs at the top to come back up.
        """
    )

# ---------------------------------------------------------------------------
# Footer disclosure (pinned wording, Section 7)
# ---------------------------------------------------------------------------
st.caption(
    f"Public approximation of JdK RRG — validated vs StockCharts on {VALIDATED_ON}, "
    f"{AGREEMENT}/11 quadrant agreement. Not a replica of StockCharts' proprietary "
    "normalization. Data: Yahoo Finance adjusted closes."
)
