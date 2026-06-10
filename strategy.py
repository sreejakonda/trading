"""
Strategy: turn raw bars into a graded, sized trade signal.

Two profiles ship (see config.STRATEGIES):

  momentum       — buy strength: price above VWAP, fast EMA over slow EMA,
                   RSI rising but not blown out, breaking the opening range,
                   leading the market on above-average volume.
  mean_reversion — buy weakness: price stretched ≥2σ below its rolling mean
                   (Bollinger z-score) with RSI oversold and a reversal tick.

Both are long-only and risk-bounded. Every entry produces an ATR-based stop and
a target, so position size follows from a fixed dollar risk, not from a guess.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import indicators as ind
from config import RiskConfig, StrategyConfig
from market_data import Series

# Action ladder, weakest → strongest.
SKIP, WATCH, BUY, STRONG_BUY = "SKIP", "WATCH", "BUY", "STRONG_BUY"


@dataclass
class Features:
    """Everything the decision logic needs, computed once per symbol per scan."""

    symbol: str
    price: float
    pct_from_open: float
    rs_vs_market: float
    vwap: float
    vwap_z: float          # (price - vwap) / vwap stdev
    above_vwap: bool
    ema_fast: Optional[float]
    ema_slow: Optional[float]
    rsi: Optional[float]
    bb_mid: Optional[float]
    bb_z: Optional[float]  # z-score of price vs rolling mean
    bb_lower: Optional[float]
    atr: Optional[float]
    or_high: Optional[float]
    or_low: Optional[float]
    volume_ratio: float
    reversal_tick: bool    # last close > prior close


@dataclass
class Signal:
    symbol: str
    action: str
    score: int
    price: float
    stop: float = 0.0
    target: float = 0.0
    shares: int = 0
    reasons: List[str] = field(default_factory=list)

    @property
    def deployed(self) -> float:
        return round(self.shares * self.price, 2)

    @property
    def risk_dollars(self) -> float:
        return round((self.price - self.stop) * self.shares, 2)


def compute_features(series: Series, cfg: StrategyConfig, market_pct: float) -> Features:
    closes = series.closes
    bars = series.bars
    vw, vw_std = ind.vwap(bars)
    bb = ind.bollinger(closes, cfg.bb_period, cfg.bb_stdev)
    orange = ind.opening_range(bars, cfg.opening_range_minutes)
    return Features(
        symbol=series.symbol,
        price=round(series.price, 2),
        pct_from_open=series.pct_from_open,
        rs_vs_market=series.pct_from_open - market_pct,
        vwap=vw,
        vwap_z=(series.price - vw) / vw_std if vw_std > 0 else 0.0,
        above_vwap=series.price > vw,
        ema_fast=ind.ema(closes, cfg.ema_fast),
        ema_slow=ind.ema(closes, cfg.ema_slow),
        rsi=ind.rsi(closes, cfg.rsi_period),
        bb_mid=bb[0] if bb else None,
        bb_z=bb[3] if bb else None,
        bb_lower=bb[2] if bb else None,
        atr=ind.atr(bars, cfg.atr_period),
        or_high=orange[0] if orange else None,
        or_low=orange[1] if orange else None,
        volume_ratio=ind.volume_ratio(bars),
        reversal_tick=len(closes) >= 2 and closes[-1] > closes[-2],
    )


def _action_for(score: int, cfg: StrategyConfig) -> str:
    if score >= cfg.strong_score:
        return STRONG_BUY
    if score >= cfg.buy_score:
        return BUY
    if score >= cfg.watch_score:
        return WATCH
    return SKIP


def _size(price: float, stop: float, risk: RiskConfig) -> int:
    """Shares such that (price-stop)·shares ≤ max risk and notional ≤ max size."""
    risk_per_share = price - stop
    if risk_per_share <= 0:
        return 0
    by_risk = risk.max_risk_dollars / risk_per_share
    by_size = risk.max_position_dollars / price
    return max(0, math.floor(min(by_risk, by_size)))


# ── momentum ────────────────────────────────────────────────────────────────
def _momentum(f: Features, cfg: StrategyConfig) -> Signal:
    sig = Signal(symbol=f.symbol, action=SKIP, score=0, price=f.price)

    # Hard gates — any failure means no trade.
    gates = [
        (f.above_vwap, "below VWAP"),
        (f.ema_fast is not None and f.ema_slow is not None and f.ema_fast > f.ema_slow,
         "no EMA uptrend"),
        (f.pct_from_open > 0, "red on day"),
        (f.rsi is not None and 50 <= f.rsi <= 80, "RSI not in 50–80"),
        (f.volume_ratio >= cfg.min_volume_ratio, "thin volume"),
        (f.rs_vs_market >= cfg.min_rs_vs_market, "lagging market"),
    ]
    for ok, why in gates:
        if not ok:
            sig.reasons.append(why)
            return sig

    # Score 0..10.
    score = 0.0
    score += min(3.0, f.pct_from_open)                                  # trend size
    score += 2.0 if f.rs_vs_market > 1.0 else 1.0 if f.rs_vs_market > 0.3 else 0.0
    score += 2.0 if f.volume_ratio > 1.5 else 1.0 if f.volume_ratio > 1.1 else 0.0
    score += 1.5 if f.vwap_z > 1.0 else 0.75 if f.vwap_z > 0.3 else 0.0  # above-VWAP strength
    if f.or_high is not None and f.price >= f.or_high:
        score += 1.5                                                    # opening-range break
    sig.score = int(round(min(10.0, score)))
    sig.action = _action_for(sig.score, cfg)
    sig.reasons.append(
        f"mom {f.pct_from_open:+.1f}% · RS {f.rs_vs_market:+.1f}% · "
        f"vol×{f.volume_ratio:.1f} · RSI {f.rsi:.0f}"
    )

    if f.atr:
        sig.stop = round(f.price - cfg.stop_atr_mult * f.atr, 2)
        sig.target = round(f.price + cfg.target_atr_mult * f.atr, 2)
    return sig


# ── mean reversion ──────────────────────────────────────────────────────────
def _mean_reversion(f: Features, cfg: StrategyConfig) -> Signal:
    sig = Signal(symbol=f.symbol, action=SKIP, score=0, price=f.price)

    gates = [
        (f.bb_z is not None and f.bb_z <= -cfg.bb_stdev, "not ≥2σ below mean"),
        (f.rsi is not None and f.rsi < 35, "RSI not oversold"),
        (f.bb_lower is not None and f.price <= f.bb_lower, "above lower band"),
        (f.reversal_tick, "still falling"),
        (f.volume_ratio >= cfg.min_volume_ratio, "thin volume"),
    ]
    for ok, why in gates:
        if not ok:
            sig.reasons.append(why)
            return sig

    score = 0.0
    score += min(3.5, abs(f.bb_z))                                      # how stretched
    score += 3.0 if f.rsi < 20 else 2.0 if f.rsi < 28 else 1.0          # oversold depth
    score += 2.0 if f.volume_ratio > 1.5 else 1.0                       # capitulation vol
    score += 1.5 if f.reversal_tick else 0.0                            # turning up
    sig.score = int(round(min(10.0, score)))
    sig.action = _action_for(sig.score, cfg)
    sig.reasons.append(
        f"z {f.bb_z:+.1f}σ · RSI {f.rsi:.0f} · vol×{f.volume_ratio:.1f}"
    )

    if f.atr:
        sig.stop = round(f.price - cfg.stop_atr_mult * f.atr, 2)
        # Mean reversion targets the mean (VWAP / Bollinger mid), capped by ATR.
        mean_target = max(f.bb_mid or f.price, f.vwap)
        atr_target = f.price + cfg.target_atr_mult * f.atr
        sig.target = round(min(mean_target, atr_target), 2)
    return sig


def generate(series: Series, cfg: StrategyConfig, risk: RiskConfig, market_pct: float) -> Signal:
    """Compute features, grade the setup, and size the position."""
    f = compute_features(series, cfg, market_pct)
    sig = _momentum(f, cfg) if cfg.kind == "momentum" else _mean_reversion(f, cfg)

    if sig.action in (BUY, STRONG_BUY) and sig.stop > 0 and sig.target > sig.price:
        sig.shares = _size(sig.price, sig.stop, risk)
        if sig.shares <= 0:
            sig.action = WATCH
            sig.reasons.append("size rounds to 0 shares")
    elif sig.action in (BUY, STRONG_BUY):
        # Missing ATR or degenerate geometry — don't trade it.
        sig.action = WATCH
        sig.reasons.append("incomplete risk geometry")
    return sig
