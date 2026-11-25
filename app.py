"""
app.py — Streamlit UI for the RRG sector-rotation app (Section 6, pinned behavior).

Equity-rotation drill path: US Sectors (vs SPY) -> Subsectors -> Stocks.
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
st.set_page_config(page_title="RRG — Sector Rotation", layout="wide",
                   initial_sidebar_state="collapsed")

# Tighter page margins so the chart and its table fit on one screen without
# scrolling (Streamlit's default top padding is ~6rem).
st.markdown(
    """
    <style>
    /* Hide Streamlit's thin top toolbar (it overlays the page and forces a
       big top margin); settings live in the app's own ⚙ menu instead. */
    [data-testid="stHeader"] { display: none; }
    [data-testid="stMainBlockContainer"], .block-container {
        padding-top: 1.2rem; padding-bottom: 1rem;
    }
    /* Keep headings on one line so a long level name can't grow the header. */
    [data-testid="stMainBlockContainer"] h3 {
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

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
# Reset if there's no path yet, or if a saved path points at a level that no
# longer exists in universe.json (e.g. after the universe is edited).
if ("path" not in st.session_state
        or st.session_state.path[0][0] != ROOT_KEY
        or any(k not in NODES for k, _ in st.session_state.path)):
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
# Only shown once you've drilled below the top level — at the top it would
# just repeat the page title and push the chart down.
show_crumbs = len(st.session_state.path) > 1
BREADCRUMB_PX = 40  # approximate height of the breadcrumb row
if show_crumbs:
    crumb_cols = st.columns([1] * len(st.session_state.path) + [6])
    for i, (key, label) in enumerate(st.session_state.path):
        if crumb_cols[i].button(
            label, key=f"crumb_{i}", type="tertiary",
            disabled=(i == len(st.session_state.path) - 1),  # current level isn't a link
        ):
            st.session_state.path = st.session_state.path[: i + 1]
            st.rerun()

# ---------------------------------------------------------------------------
# Compact header: level title, timeframe filter and daily toggle on ONE row.
# Timeframe filter — PINNED SEMANTICS: changes ONLY the tail length drawn,
# never the EMA/ROC calculation windows (quadrant positions stay consistent
# between views). "4 Weeks" additionally offers the daily-engine toggle.
# ---------------------------------------------------------------------------
title_col, tf_col, daily_col, set_col = st.columns([3, 3, 1.1, 1.1],
                                                  vertical_alignment="center")
# Long level names ("Communication Services Subsectors") must stay on one line,
# or the header grows and pushes the chart below the fold.
title_col.markdown(f"### {node['label']}")
timeframe = tf_col.radio(
    "Tail length",
    ["12 Weeks", "26 Weeks", "YTD", "4 Weeks"],
    horizontal=True,
    label_visibility="collapsed",  # buttons are self-explanatory; saves a line
)
daily_mode = False
if timeframe == "4 Weeks":
    daily_mode = daily_col.toggle(
        "Daily RRG",
        help="Recomputes the whole engine on DAILY closes with the same formulas. "
        "Short-term view — weekly is the RRG standard.",
    )

# ---------------------------------------------------------------------------
# Settings menu (top-right ⚙ popover): benchmark override, live prices,
# chart size, reload, and where the data came from.
# ---------------------------------------------------------------------------
settings = set_col.popover("⚙ Settings", width="stretch")
default_bench = node["benchmark"]
all_tickers = sorted({m["ticker"] for n in NODES.values() for m in n["members"]}
                     | {n["benchmark"] for n in NODES.values()})
bench_choice = settings.selectbox(
    "Benchmark",
    [f"Default ({default_bench})"] + all_tickers,
    key=f"bench_{node_key}",  # keyed per level so the override doesn't leak across levels
)
benchmark = default_bench if bench_choice.startswith("Default") else bench_choice

# Normally the app reads the daily snapshot (updated every weekday after the
# close). This switch fetches straight from Yahoo instead — useful intraday.
prefer_live = settings.toggle(
    "Live prices (intraday)",
    help="Off: use the daily snapshot saved after each market close (fast, reliable). "
    "On: fetch the latest prices from Yahoo right now (slower; can fail if Yahoo "
    "rate-limits the server).",
)

# Chart size: "Laptop" fits a 768p laptop screen without scrolling.
CHART_SIZES = {"Laptop": 530, "Large screen": 800}
chart_size = settings.radio(
    "Chart size", list(CHART_SIZES), horizontal=True,
    help="Laptop fits the chart and table on one screen of a typical laptop. "
    "Pick Large screen on a big monitor.",
)
settings.caption("The 12W / 26W / YTD / 4W buttons only change how much rotation "
                 "history is drawn; the math never changes.")

if settings.button("🔄 Reload data"):
    st.cache_data.clear()
    st.rerun()

# ---------------------------------------------------------------------------
# Fetch + compute (all hygiene rules live in engine/data.py)
# ---------------------------------------------------------------------------
tickers = tuple(m["ticker"] for m in members if m["ticker"] != benchmark)
with st.spinner("Loading prices…"):
    try:
        prices, bench_series, excluded, as_of, src = data.prepare_level(
            tickers, benchmark, daily_mode, prefer_live)
    except RuntimeError as err:
        st.error(str(err))
        st.stop()

# Data provenance (in the settings menu; problems shown on the page itself).
if as_of is not None:
    source_label = ("daily snapshot, updated each weekday after the close"
                    if src["source"] == "snapshot" else "live from Yahoo")
    settings.caption(f"Data as of **{as_of:%b %d, %Y}** close · {source_label}")
if src["stale_fallback"]:
    st.warning(
        "Yahoo didn't respond for " + ", ".join(src["stale_fallback"]) +
        f" — showing the saved snapshot from {src['snapshot_date']:%b %d} instead.")
elif src["source"] == "live" and not prefer_live and src["snapshot_date"] is not None:
    st.info(f"The daily snapshot is out of date (last {src['snapshot_date']:%b %d}), "
            "so prices were fetched live.")

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
# Side-by-side layout: chart on the left, drill-down + readings table on the
# right, so everything important is visible on one screen.
# The chart is square (equal axes), so it doesn't need more than ~55% width;
# the rest goes to the table so all its columns show without side-scrolling.
chart_col, side_col = st.columns([6, 5], gap="medium")

# Chart height is chosen so the whole page above the fold fits the screen:
# "Laptop" fits a ~633px-tall browser window (a 768p laptop). The breadcrumb
# row only appears below the top level, so subtract its height there.
CHART_HEIGHT = CHART_SIZES[chart_size] - (BREADCRUMB_PX if show_crumbs else 0)

# Show/hide chips: tap a ticker to take it off (or put it back on) the chart.
# Keyed per level + benchmark so each level remembers its own selection.
# Only the chart is filtered — the table keeps the full ranking for context.
available = [t for t in ratio.columns if ratio[t].notna().any()]
shown = side_col.pills(
    "Show on chart", available, selection_mode="multi", default=available,
    key=f"show_{node_key}_{benchmark}_{daily_mode}",
    help="Tap a ticker to hide or show it on the chart. The axes zoom to what's shown.",
)

# A stable color per ticker (by its position in the level, not in the filtered
# list), so hiding one sector never changes the others' colors.
COLOR_OF = {t: PALETTE[i % len(PALETTE)] for i, t in enumerate(ratio.columns)}

fig = go.Figure()

# Collect the points that will actually be drawn to size the axes.
plotted = []
for t in ratio.columns:
    if t not in shown:
        continue
    xy = pd.DataFrame({"x": ratio[t], "y": momentum[t]}).dropna().tail(tail_len)
    if len(xy) >= 1:
        plotted.append((t, xy))

if not plotted:
    chart_col.info("Nothing selected — tap at least one ticker on the right to show it.")
    if not shown:
        plotted = []
    else:
        st.error("Nothing to plot — all series are still inside the warm-up window.")
        st.stop()

# Square-ish axes centered on (100,100): take the largest deviation and pad it.
# (With nothing selected, fall back to a default ±3 window so the empty
# quadrant grid still draws.)
if plotted:
    all_vals = pd.concat([xy for _, xy in plotted])
    span = max(abs(all_vals["x"] - 100).max(), abs(all_vals["y"] - 100).max()) + 0.6
else:
    span = 3.0
lo, hi = 100 - span, 100 + span

# Quadrant background tints (Section 4 colors at low opacity) + crosshair.
TINTS = {  # (x0, x1, y0, y1) per quadrant
    "Leading": (100, hi, 100, hi),
    "Weakening": (100, hi, lo, 100),
    "Lagging": (lo, 100, lo, 100),
    "Improving": (lo, 100, 100, hi),
}
# Quadrant titles go in the OUTER corners: securities cluster near (100,100),
# so a title placed near the center sits on top of ticker labels.
CORNERS = {  # quadrant -> (x, y, xanchor, yanchor)
    "Leading": (hi, hi, "right", "top"),
    "Weakening": (hi, lo, "right", "bottom"),
    "Lagging": (lo, lo, "left", "bottom"),
    "Improving": (lo, hi, "left", "top"),
}
for qname, (x0, x1, y0, y1) in TINTS.items():
    fig.add_shape(type="rect", x0=x0, x1=x1, y0=y0, y1=y1, layer="below",
                  fillcolor=quadrant.QUADRANT_COLORS[qname], opacity=0.08, line_width=0)
    cx, cy, xa, ya = CORNERS[qname]
    fig.add_annotation(x=cx, y=cy, xanchor=xa, yanchor=ya, xshift=6 if xa == "left" else -6,
                       yshift=-6 if ya == "top" else 6, text=f"<b>{qname}</b>",
                       showarrow=False, opacity=0.75,
                       font=dict(color=quadrant.QUADRANT_COLORS[qname], size=14))
fig.add_hline(y=100, line_width=1, line_color="#888")
fig.add_vline(x=100, line_width=1, line_color="#888")

# Ticker labels: one boxed label per head, nudged up/down when it would collide
# with a label already placed (heads often cluster near the center).
# Sizes are approximate pixel->data conversions for the plot area.
PX = 2 * span / (CHART_HEIGHT - 60)  # data units per pixel (axes equal-scaled)
LABEL_H = 18 * PX              # label box height
placed_labels = []             # (x_center, y_center, half_width) of placed labels


def _place_label(x: float, y: float, text: str) -> int:
    """Return a yshift (px) that keeps this label clear of earlier ones."""
    half_w = (len(text) * 8 + 10) / 2 * PX
    for shift in (16, -18, 32, -34, 48, -50, 64, -66):
        cy = y + shift * PX
        if all(abs(cx - x) > (hw + half_w) or abs(cy - py) > LABEL_H
               for cx, py, hw in placed_labels):
            placed_labels.append((x, cy, half_w))
            return shift
    placed_labels.append((x, y + 16 * PX, half_w))
    return 16  # nowhere free nearby: default position


# One trace per security: smoothed line tail + markers fading older -> newer,
# a boxed ticker label on the newest point (the "head"), and an arrow showing
# the direction of travel — the rotation cue.
for i, (t, xy) in enumerate(plotted):
    color = COLOR_OF[t]
    n = len(xy)
    # Per-point customdata: [ticker, date label]. The ticker drives click-to-drill;
    # the label shows in the hover tooltip so every tail dot is identifiable.
    # The newest weekly bar is labeled with the upcoming Friday while that week
    # is still trading, so say "week to date" instead of a future date.
    if daily_mode:
        dates = [f"{d:%b %d, %Y}" for d in xy.index]
    else:
        dates = [f"week to date (through {as_of:%b %d, %Y})" if d > as_of
                 else f"week ending {d:%b %d, %Y}" for d in xy.index]
    fig.add_trace(go.Scatter(
        x=xy["x"], y=xy["y"],
        mode="lines+markers",
        name=t,
        line=dict(color=color, width=2, shape="spline", smoothing=1.0),
        marker=dict(
            color=color,
            size=np.linspace(6, 14, n),            # older = small, newest = big
            opacity=np.linspace(0.3, 1.0, n),      # older = faded, newest = solid
        ),
        customdata=np.column_stack([[t] * n, dates]),
        hovertemplate=(f"<b>{t}</b> · %{{customdata[1]}}<br>"
                       "RS-Ratio %{x:.2f} · RS-Momentum %{y:.2f}<extra></extra>"),
    ))
    # Boxed head label: dark translucent box + white text reads on any
    # background and in light or dark theme; the border carries the line color.
    hx, hy = xy["x"].iloc[-1], xy["y"].iloc[-1]
    fig.add_annotation(
        x=hx, y=hy, yshift=_place_label(hx, hy, t), text=f"<b>{t}</b>",
        showarrow=False, font=dict(color="white", size=12),
        bgcolor="rgba(20,20,20,0.78)", bordercolor=color, borderwidth=1.5, borderpad=2,
    )
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
    height=CHART_HEIGHT,
    showlegend=False,  # heads are labeled directly; the table beside lists everything
    xaxis=dict(title="JdK RS-Ratio (relative trend)", range=[lo, hi], zeroline=False),
    # scaleanchor keeps 1 unit of x = 1 unit of y, so rotation reads as
    # rotation instead of being stretched by the plot's aspect ratio.
    yaxis=dict(title="JdK RS-Momentum (rate of change)", range=[lo, hi],
               zeroline=False, scaleanchor="x", scaleratio=1),
    margin=dict(l=10, r=10, t=24, b=10),
    clickmode="event+select",
    dragmode="pan",
)

# One small grey line above the plot says what's being shown — costs no
# extra page height because it sits in the chart's own top margin.
engine_label = "Daily RRG (short-term)" if daily_mode else "Weekly RRG"
fig.add_annotation(
    xref="paper", yref="paper", x=0, y=1, xanchor="left", yanchor="bottom", yshift=4,
    showarrow=False, font=dict(size=11, color="#8a8f98"),
    text=(f"{engine_label} vs <b>{benchmark}</b> · {tail_len}-"
          f"{'day' if daily_mode else 'week'} tails · data through {as_of:%b %d, %Y} close"),
)

# on_select="rerun": clicking a dot reruns the script with the selection so we
# can drill down. The chart key includes the level so selections don't carry
# over (and re-trigger navigation) after we move to a new level.
event = chart_col.plotly_chart(
    fig, width="stretch",
    on_select="rerun", selection_mode="points",
    key=f"chart_{node_key}_{timeframe}_{daily_mode}",
)

# Click-to-drill bridge: plotly.js fires plotly_click on a dot but does not
# reliably promote it to a *selection*, and Streamlit's on_select only reruns
# on selections — so re-emit each click as plotly_selected. Re-checks every
# half second because Streamlit can replace the chart element on any rerun.
import streamlit.components.v1 as components  # noqa: E402 — used only for this shim

with chart_col:
    components.html(
        """
        <script>
        const tick = () => {
            const gd = window.parent.document.querySelector('.js-plotly-plot');
            if (!gd || !gd.on || gd.__rrgBridge) return;
            gd.__rrgBridge = true;
            gd.on('plotly_click', (d) => gd.emit('plotly_selected', { points: d.points }));
        };
        setInterval(tick, 500);
        tick();
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

# (Drill-down 2/2 is clicking a row in the table below.)

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
    # Short column names so the table fits in the side column next to the chart.
    rows.append({
        "Ticker": t,
        "Quadrant": quadrant.classify(r, m),
        "Dir": _heading_arrow(t),
        "RS-Ratio": round(r, 2),
        "RS-Mom": round(m, 2),
        "Δ 4w": round(velocity[t], 2) if pd.notna(velocity[t]) else None,
        "Name": by_ticker.get(t, {}).get("name", ""),
    })
table = pd.DataFrame(rows).sort_values("RS-Ratio", ascending=False, ignore_index=True)

# Color the quadrant column with the pinned Section-4 colors so the table
# reads at a glance like the chart does.
styled = table.style.map(
    lambda q: f"color: {quadrant.QUADRANT_COLORS.get(q, '')}; font-weight: 700;",
    subset=["Quadrant"],
).format({"RS-Ratio": "{:.2f}", "RS-Mom": "{:.2f}", "Δ 4w": "{:+.2f}"})
# --- Drill-down 2/2: click a row in the table --------------------------------
can_drill = any("drill" in m for m in members)
table_event = side_col.dataframe(
    styled, width="stretch", hide_index=True,
    height=35 * (len(table) + 1) + 3,  # show every row without an inner scrollbar
    column_config={
        "Dir": st.column_config.TextColumn("Dir", help="Direction of the last move", width="small"),
        "Δ 4w": st.column_config.NumberColumn(
            "Δ 4w", help="Change in RS-Ratio over 4 weeks (rotation speed)"),
    },
    on_select="rerun" if can_drill else "ignore",
    selection_mode="single-row",
    key=f"table_{node_key}_{benchmark}",  # per level, so a selection never carries over
)
# One-line read of who's leading / gaining right now (table is RS-Ratio sorted).
leaders = table.loc[table["Quadrant"] == "Leading", "Ticker"].tolist()
improvers = table.loc[table["Quadrant"] == "Improving", "Ticker"].tolist()
summary = []
if leaders:
    summary.append(f"**Leading vs {benchmark}:** {', '.join(leaders)}")
if improvers:
    summary.append(f"**Improving:** {', '.join(improvers)}")
if summary:
    side_col.markdown(" · ".join(summary))

if can_drill:
    side_col.caption("Click a row (or a dot on the chart) to see what's inside it.")
    picked_rows = (table_event or {}).get("selection", {}).get("rows", [])
    if picked_rows:
        picked = by_ticker.get(table.loc[picked_rows[0], "Ticker"])
        if picked and "drill" in picked:
            drill_into(picked)
            st.rerun()

# ---------------------------------------------------------------------------
# What's driving the benchmark: top holdings × return over the chosen window.
# On a sector page (benchmark = XLK) this shows which stocks moved Technology;
# at the top level (benchmark = SPY) it shows what moved the market.
# ---------------------------------------------------------------------------
HOLDINGS_FILE = Path(__file__).parent / "data" / "holdings.json"


@st.cache_data(show_spinner=False)
def _load_holdings(path_str: str, mtime: float) -> dict:
    return json.loads(Path(path_str).read_text())


fund = (_load_holdings(str(HOLDINGS_FILE), HOLDINGS_FILE.stat().st_mtime).get(benchmark)
        if HOLDINGS_FILE.exists() else None)
if fund:
    from engine import drivers  # noqa: E402 — only needed for this panel

    drv_tickers = tuple(h["ticker"] for h in fund["holdings"]) + (benchmark,)
    drv_closes, _, _ = data.get_daily_closes(drv_tickers, prefer_live)
    start = drivers.period_start(as_of, timeframe)
    drv = drivers.contributions(fund["holdings"], drv_closes, start, as_of)
    fund_ret = (drivers.period_return(drv_closes[benchmark], start, as_of)
                if benchmark in drv_closes.columns else None)

    side_col.markdown(f"##### What's driving {benchmark} · {timeframe}")
    top_w = drv["Weight"].sum()
    top_c = drv["Contribution"].sum(skipna=True)
    if fund_ret is not None:
        side_col.caption(
            f"{benchmark} {fund_ret:+.1%} over {timeframe.lower()}. Its top {len(drv)} holdings "
            f"({top_w:.0%} of the fund) account for {top_c * 100:+.1f} pts of that. "
            "Contribution = weight × return.")
    side_col.dataframe(
        drv.style.format({"Weight": "{:.1%}", "Return": "{:+.1%}",
                          "Contribution": lambda v: f"{v * 100:+.2f} pts"}, na_rep="—")
           .map(lambda v: "color: #2E7D32; font-weight: 600;" if isinstance(v, float) and v > 0
                else ("color: #C62828; font-weight: 600;" if isinstance(v, float) and v < 0 else ""),
                subset=["Return", "Contribution"]),
        width="stretch", hide_index=True,
        height=35 * (len(drv) + 1) + 3,
        column_order=["Ticker", "Contribution", "Return", "Weight", "Name"],
    )
    side_col.caption(f"Holdings as of {fund['as_of']} (Yahoo fund data, top 10 by weight).")

# ---------------------------------------------------------------------------
# Quadrant history: which quadrant each security sat in over the last 12
# observations — the quickest way to see WHEN money rotated in or out.
# ---------------------------------------------------------------------------
st.subheader("Quadrant history")
HIST_N = 12
hist_index = ratio.index[-HIST_N:]
grid = pd.DataFrame(index=table["Ticker"])  # same order as the table above
for d in hist_index:
    if daily_mode:
        col = f"{d:%b %d}"
    else:
        col = f"{as_of:%b %d}*" if d > as_of else f"{d:%b %d}"
    grid[col] = [
        quadrant.classify(ratio.at[d, t], momentum.at[d, t])
        if pd.notna(ratio.at[d, t]) and pd.notna(momentum.at[d, t]) else ""
        for t in grid.index
    ]

QUAD_SHORT = {"Leading": "LEAD", "Weakening": "WEAK", "Lagging": "LAG", "Improving": "IMPR"}


def _quad_cell(q: str) -> str:
    """Solid quadrant color with white text — reads like a heat strip."""
    c = quadrant.QUADRANT_COLORS.get(q)
    return f"background-color: {c}; color: white; font-weight: 600;" if c else ""


st.dataframe(
    grid.style.map(_quad_cell).format(lambda q: QUAD_SHORT.get(q, q)),
    width="stretch",
)
period_word = "trading days" if daily_mode else "weeks"
st.caption(
    f"Last {len(hist_index)} {period_word}, newest on the right. Clockwise rotation reads "
    "IMPR → LEAD → WEAK → LAG."
    + ("" if daily_mode or hist_index[-1] <= as_of
       else f" * = week in progress, through {as_of:%b %d}.")
)

# Download the durable rotation log written by the daily job (one row per
# security per day, plus ~2 years of weekly backfill).
history_path = Path(__file__).parent / "data" / "rotation_history.csv"


@st.cache_data(show_spinner=False)
def _load_log(path_str: str, mtime: float) -> pd.DataFrame:
    """mtime in the key so each day's new log is picked up after a redeploy."""
    return pd.read_csv(path_str)


if history_path.exists():
    log = _load_log(str(history_path), history_path.stat().st_mtime)
    level_log = log[log["level"] == node_key]
    dl1, dl2 = st.columns(2)
    dl1.download_button(
        f"⬇ Rotation log — {node['label']} ({len(level_log):,} rows)",
        level_log.to_csv(index=False), file_name=f"rotation_log_{node_key}.csv",
        mime="text/csv", width="stretch",
        help=f"Benchmark {default_bench}. Every daily reading of this level, oldest first.",
    )
    dl2.download_button(
        f"⬇ Full rotation log — all levels ({len(log):,} rows)",
        log.to_csv(index=False), file_name="rotation_history.csv",
        mime="text/csv", width="stretch",
    )

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
    "normalization. Data: Yahoo Finance adjusted closes, refreshed every weekday after "
    "the US close."
)
