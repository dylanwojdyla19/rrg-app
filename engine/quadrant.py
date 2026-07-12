"""
engine/quadrant.py — Quadrant classification and colors. Section 4, pinned.

  RS_Ratio > 100 and RS_Momentum > 100 -> Leading    (green  #2E7D32)
  RS_Ratio > 100 and RS_Momentum < 100 -> Weakening  (yellow #F9A825)
  RS_Ratio < 100 and RS_Momentum < 100 -> Lagging    (red    #C62828)
  RS_Ratio < 100 and RS_Momentum > 100 -> Improving  (blue   #1565C0)

Boundary convention: values exactly AT 100 are grouped with the ">" side so a
benchmark-identical security (exactly 100,100) reads as Leading rather than
crashing or returning None. The chart shows it sitting on the crosshair either way.
"""

# Hex colors pinned in Section 4, used for both quadrant tints and labels.
QUADRANT_COLORS = {
    "Leading": "#2E7D32",
    "Weakening": "#F9A825",
    "Lagging": "#C62828",
    "Improving": "#1565C0",
}

# The JdK clockwise rotation order — handy for legends and sanity checks.
ROTATION_ORDER = ["Improving", "Leading", "Weakening", "Lagging"]


def classify(rs_ratio: float, rs_momentum: float) -> str:
    """Map one (RS-Ratio, RS-Momentum) point to its quadrant name."""
    if rs_ratio >= 100:
        return "Leading" if rs_momentum >= 100 else "Weakening"
    return "Improving" if rs_momentum >= 100 else "Lagging"
