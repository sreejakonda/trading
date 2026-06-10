#!/usr/bin/env python3
"""
CAUTIOUS strategy — high conviction only.

Gates (6):
  1. Price above real VWAP          — institutional trend confirmation
  2. pct_from_open > 0.5%           — meaningful momentum (not noise)
  3. range_pos > 0.60               — price in top 40% of day's range
  4. pct_from_open ≤ 3.0%           — not extended / don't chase
  5. rs_vs_spy > 0.0%               — must be outperforming the market
  6. vol_ratio > 1.0                — above-average volume (conviction)

Scoring adds: strength of each indicator, bar quality.
Target: 3.0%  Stop: 1.5%  Time stop: 45 min  Re-entry: disabled
"""

from datetime import time
import engine

def decision_fn(ind):
    # ── 6 gates ──────────────────────────────────────────────────────────────
    if not ind["above_vwap"]:           return "SKIP", 0  # below VWAP
    if ind["pct_from_open"] <= 0.5:     return "SKIP", 0  # too small / noise
    if ind["range_pos"] < 0.60:         return "SKIP", 0  # not in top of range
    if ind["pct_from_open"] > 3.0:      return "SKIP", 0  # extended — don't chase
    if ind["rs_vs_spy"] <= 0.0:         return "SKIP", 0  # must beat SPY outright
    if ind["vol_ratio"] < 1.0:          return "SKIP", 0  # must have above-avg volume

    # ── scoring (0–12) ───────────────────────────────────────────────────────
    score = 0

    # Momentum (0–3)
    m = ind["pct_from_open"]
    score += 3 if m > 2.0 else 2 if m > 1.0 else 1

    # Range position (0–2): near the high = trending strongly
    score += 2 if ind["range_pos"] > 0.85 else 1 if ind["range_pos"] > 0.70 else 0

    # Volume (0–3): strong volume = institutional involvement
    v = ind["vol_ratio"]
    score += 3 if v > 2.0 else 2 if v > 1.5 else 1

    # Relative strength (0–2): bigger outperformance = stronger leader
    rs = ind["rs_vs_spy"]
    score += 2 if rs > 1.5 else 1 if rs > 0.5 else 0

    # Bar quality (0–2): recent bar direction — is momentum fresh or fading?
    score += 2 if ind["green_bars"] >= 4 else 1 if ind["green_bars"] >= 3 else 0

    if score >= 9:  return "STRONG BUY", score
    if score >= 6:  return "BUY",         score
    if score >= 4:  return "WATCH",        score
    return "SKIP", score

CFG = {
    "name":           "CAUTIOUS",
    "label":          "cautious",
    "log_file":       "cautious.txt",
    "decision_fn":    decision_fn,
    "stop_pct":       1.5,
    "target_pct":     3.0,
    "max_positions":  2,
    "time_stop_mins": 45,
    "no_entry_after": time(15, 30),
    "strong_score":   9,
    "allow_reentry":  False,
}

if __name__ == "__main__":
    engine.run(CFG)
