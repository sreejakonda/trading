"""
Statistical indicators — the well-known building blocks.

Every function is pure (takes numbers, returns numbers) so it can be unit-tested
in isolation. Each carries a one-line note on *why* it matters for trading.
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

from market_data import Bar


def sma(values: List[float], period: int) -> Optional[float]:
    """Simple moving average of the last `period` values."""
    if len(values) < period or period <= 0:
        return None
    return sum(values[-period:]) / period


def ema(values: List[float], period: int) -> Optional[float]:
    """
    Exponential moving average — weights recent prices more heavily, so it
    reacts faster than an SMA. Fast-vs-slow EMA crossovers are a classic
    trend filter.
    """
    if len(values) < period or period <= 0:
        return None
    k = 2 / (period + 1)
    e = sum(values[:period]) / period  # seed with SMA
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e


def rsi(closes: List[float], period: int = 14) -> Optional[float]:
    """
    Relative Strength Index (Wilder). Oscillates 0–100.
      >70 overbought, <30 oversold. Momentum strategies want RSI rising through
      the midline; mean-reversion strategies buy oversold extremes.
    """
    if len(closes) <= period:
        return None
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    avg_gain, avg_loss = gains / period, losses / period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(d, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-d, 0.0)) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def bollinger(closes: List[float], period: int = 20, k: float = 2.0
              ) -> Optional[Tuple[float, float, float, float]]:
    """
    Bollinger Bands + the z-score of price vs its rolling mean.
    Returns (mid, upper, lower, zscore). z = (price - mean) / stdev measures,
    in standard deviations, how stretched price is — the core mean-reversion edge.
    """
    if len(closes) < period:
        return None
    window = closes[-period:]
    mid = sum(window) / period
    var = sum((c - mid) ** 2 for c in window) / period
    stdev = math.sqrt(var)
    upper, lower = mid + k * stdev, mid - k * stdev
    z = (closes[-1] - mid) / stdev if stdev > 0 else 0.0
    return mid, upper, lower, z


def atr(bars: List[Bar], period: int = 14) -> Optional[float]:
    """
    Average True Range — the average size of a bar including gaps. The standard
    way to size volatility-adaptive stops: a stop at N×ATR adjusts automatically
    to how much each name actually moves.
    """
    if len(bars) <= period:
        return None
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i].high, bars[i].low, bars[i - 1].close
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < period:
        return None
    # Wilder smoothing.
    a = sum(trs[:period]) / period
    for tr in trs[period:]:
        a = (a * (period - 1) + tr) / period
    return a


def vwap(bars: List[Bar]) -> Tuple[float, float]:
    """
    Volume-Weighted Average Price and the stdev of price around it.
    VWAP is the session's fair-value anchor that institutions benchmark against;
    holding above VWAP is bullish. The stdev lets us build VWAP bands.
    Returns (vwap, stdev).
    """
    cum_pv = cum_v = 0.0
    typicals = []
    for b in bars:
        tp = (b.high + b.low + b.close) / 3
        cum_pv += tp * b.volume
        cum_v += b.volume
        typicals.append(tp)
    if cum_v <= 0:  # pre-volume / illiquid fallback
        vw = sum(typicals) / len(typicals)
    else:
        vw = cum_pv / cum_v
    var = sum((tp - vw) ** 2 for tp in typicals) / len(typicals)
    return vw, math.sqrt(var)


def opening_range(bars: List[Bar], minutes: int = 30) -> Optional[Tuple[float, float]]:
    """
    Opening Range = the high/low of the first `minutes` of trading. A break
    above the opening-range high is one of the most-documented intraday
    momentum signals (ORB). Returns (or_high, or_low).
    """
    if not bars:
        return None
    start = bars[0].ts
    window = [b for b in bars if b.ts < start + minutes * 60]
    if not window:
        return None
    return max(b.high for b in window), min(b.low for b in window)


def volume_ratio(bars: List[Bar], recent: int = 5) -> float:
    """Recent average volume ÷ session average volume. >1 = volume picking up."""
    if not bars:
        return 1.0
    recent_bars = bars[-recent:]
    recent_avg = sum(b.volume for b in recent_bars) / len(recent_bars)
    session = [b.volume for b in bars if b.volume > 0]
    session_avg = (sum(session) / len(session)) if session else 0.0
    return recent_avg / session_avg if session_avg > 0 else 1.0
