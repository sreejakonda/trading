#!/usr/bin/env python3
"""
AGGRESSIVE strategy — scalping, fast in/out.

Gates (4):
  1. Price above real VWAP          — institutional trend confirmation
  2. pct_from_open > 0.3%           — real momentum, not noise
  3. range_pos > 0.40               — price in upper 60% of day's range
  4. vol_ratio > 0.8                — volume at least near session average

Scoring adds: momentum quality, relative strength, volatility.
Target: 1.5%  Stop: 1.0%  Time stop: 20 min  Re-entry: allowed
"""

from datetime import time
import engine

def decision_fn(ind):
    # ── 4 gates ──────────────────────────────────────────────────────────────
    if not ind["above_vwap"]:           return "SKIP", 0  # below VWAP
    if ind["pct_from_open"] <= 0.3:     return "SKIP", 0  # noise / flat
    if ind["range_pos"] < 0.40:         return "SKIP", 0  # in lower range
    if ind["vol_ratio"] < 0.8:          return "SKIP", 0  # thin volume

    # ── scoring (0–11) ───────────────────────────────────────────────────────
    score = 0

    # Momentum (0–3): how far above open, rewarding clean moves
    m = ind["pct_from_open"]
    score += 3 if m > 1.5 else 2 if m > 0.75 else 1

    # Range position (0–2): how close to the day's high
    score += 2 if ind["range_pos"] > 0.80 else 1 if ind["range_pos"] > 0.60 else 0

    # Volume (0–2): volume surge = conviction behind the move
    score += 2 if ind["vol_ratio"] > 1.5 else 1 if ind["vol_ratio"] > 1.0 else 0

    # Relative strength vs SPY (0–2): leading the market = strongest stocks
    rs = ind["rs_vs_spy"]
    score += 2 if rs > 1.0 else 1 if rs > 0.0 else 0

    # Bar quality (0–2): recent bars trending up = momentum is fresh
    score += 2 if ind["green_bars"] >= 4 else 1 if ind["green_bars"] >= 3 else 0

    if score >= 8:  return "STRONG BUY", score
    if score >= 5:  return "BUY",         score
    if score >= 3:  return "WATCH",        score
    return "SKIP", score

CFG = {
    "name":           "AGGRESSIVE",
    "label":          "aggressive",
    "log_file":       "aggressive.txt",
    "decision_fn":    decision_fn,
    "stop_pct":       1.0,
    "target_pct":     1.5,
    "max_positions":  3,
    "time_stop_mins": 20,
    "no_entry_after": time(15, 45),
    "strong_score":   8,
    "allow_reentry":  True,
}

if __name__ == "__main__":
    engine.run(CFG)
