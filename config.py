"""
Central configuration for the trading system.

Everything tunable lives here or is overridable via environment variables
(loaded from `.env`). Nothing else in the codebase reads `os.environ`
directly for trading parameters — keep it that way.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from datetime import time

import pytz

# ── paths ───────────────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(ROOT, "state")
ET = pytz.timezone("America/New_York")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


# ── trading mode ────────────────────────────────────────────────────────────
# "test" → SimBroker: live market data, simulated fills, no money at risk.
# "live" → RobinhoodBroker: REAL orders against a real Robinhood account.
MODE = os.environ.get("TRADING_MODE", "test").strip().lower()
# Extra belt-and-suspenders gate: live orders are refused unless this is "yes".
LIVE_CONFIRM = os.environ.get("LIVE_CONFIRM", "no").strip().lower() == "yes"
# When true, the engine logs the orders it *would* place but never sends them.
DRY_RUN = os.environ.get("DRY_RUN", "false").strip().lower() in ("1", "true", "yes")


# ── universe ────────────────────────────────────────────────────────────────
# Liquid, high-volume names that day-trade well. Edit freely.
WATCHLIST = os.environ.get(
    "WATCHLIST",
    "AAPL,NVDA,TSLA,AMD,META,MSFT,AMZN,GOOGL,NFLX,PLTR,COIN,SOFI",
).split(",")
WATCHLIST = [s.strip().upper() for s in WATCHLIST if s.strip()]
BENCHMARK = "SPY"  # used for relative-strength and a crude market-regime read


@dataclass(frozen=True)
class RiskConfig:
    """Account-level risk limits. The strategy never overrides these."""

    capital: float = _env_float("CAPITAL", 2000.0)
    risk_per_trade_pct: float = _env_float("RISK_PER_TRADE_PCT", 1.0)  # % of capital
    max_position_pct: float = _env_float("MAX_POSITION_PCT", 25.0)     # % of capital
    max_positions: int = _env_int("MAX_POSITIONS", 4)
    daily_loss_limit_pct: float = _env_float("DAILY_LOSS_LIMIT_PCT", 3.0)  # kill switch

    # Intraday session rules (US/Eastern).
    no_entry_after: time = time(15, 30)   # stop opening new trades
    flatten_at: time = time(15, 50)       # close everything before the bell
    time_stop_minutes: int = _env_int("TIME_STOP_MINUTES", 60)  # cut dead trades

    @property
    def max_risk_dollars(self) -> float:
        return self.capital * self.risk_per_trade_pct / 100.0

    @property
    def max_position_dollars(self) -> float:
        return self.capital * self.max_position_pct / 100.0

    @property
    def daily_loss_limit_dollars(self) -> float:
        return self.capital * self.daily_loss_limit_pct / 100.0


@dataclass(frozen=True)
class StrategyConfig:
    """
    A named, self-describing strategy profile. Two ship by default:

      momentum       — trend / breakout continuation (buy strength)
      mean_reversion — fade statistical extremes (buy oversold dips)

    Thresholds here are deliberately explicit so the behaviour is auditable.
    """

    name: str
    kind: str  # "momentum" | "mean_reversion"

    # Indicator windows (in bars).
    ema_fast: int = 9
    ema_slow: int = 21
    rsi_period: int = 14
    bb_period: int = 20
    bb_stdev: float = 2.0
    atr_period: int = 14
    vwap_band_stdev: float = 1.0
    opening_range_minutes: int = 30

    # Entry thresholds.
    min_volume_ratio: float = 1.0   # recent vs session-average volume
    min_rs_vs_market: float = 0.0   # relative strength vs benchmark, %

    # Exit geometry, expressed in ATR multiples (volatility-adaptive).
    stop_atr_mult: float = 1.3
    target_atr_mult: float = 2.2

    # Score → action mapping (score is 0..10).
    strong_score: int = 8
    buy_score: int = 5
    watch_score: int = 3

    def with_overrides(self, **kw) -> "StrategyConfig":
        return replace(self, **kw)


# Default profiles. Pick with `--strategy` on the CLI or STRATEGY env var.
STRATEGIES = {
    "momentum": StrategyConfig(
        name="Momentum Breakout",
        kind="momentum",
        stop_atr_mult=1.3,
        target_atr_mult=2.4,
        min_volume_ratio=1.1,
        min_rs_vs_market=0.0,
    ),
    "mean_reversion": StrategyConfig(
        name="Mean Reversion",
        kind="mean_reversion",
        stop_atr_mult=1.0,
        target_atr_mult=1.8,
        min_volume_ratio=0.8,
        min_rs_vs_market=-99.0,  # RS not required when fading extremes
    ),
}

DEFAULT_STRATEGY = os.environ.get("STRATEGY", "momentum").strip().lower()


@dataclass(frozen=True)
class Settings:
    mode: str = MODE
    dry_run: bool = DRY_RUN
    live_confirmed: bool = LIVE_CONFIRM
    risk: RiskConfig = field(default_factory=RiskConfig)
    watchlist: tuple = field(default_factory=lambda: tuple(WATCHLIST))
    benchmark: str = BENCHMARK
    bar_interval: str = "1m"
    bar_range: str = "1d"

    @property
    def is_live(self) -> bool:
        return self.mode == "live"


def load_settings() -> Settings:
    return Settings()
