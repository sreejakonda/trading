"""
Execution layer — the only place that can move real money.

A `Broker` takes the strategy's decisions and turns them into fills:

  SimBroker        — test mode. Fills are simulated at the decision price
                     (optionally with slippage). No account, no risk.
  RobinhoodBroker  — live mode. Places REAL market orders on Robinhood via the
                     `robin_stocks` library. Guarded behind TRADING_MODE=live
                     *and* LIVE_CONFIRM=yes.

Strategy/risk code never imports a concrete broker — it calls `make_broker()`
and uses the abstract interface, so a future Robinhood MCP server (or IBKR,
Alpaca, …) drops in as one new subclass without touching anything upstream.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from config import Settings


@dataclass
class Fill:
    symbol: str
    side: str          # "buy" | "sell"
    shares: int
    price: float
    ok: bool
    detail: str = ""

    @property
    def notional(self) -> float:
        return round(self.shares * self.price, 2)


class Broker(ABC):
    label: str = "broker"
    is_real: bool = False

    @abstractmethod
    def buy(self, symbol: str, shares: int, price: float) -> Fill: ...

    @abstractmethod
    def sell(self, symbol: str, shares: int, price: float) -> Fill: ...

    def account_equity(self) -> Optional[float]:
        """Best-effort account equity, or None if unavailable."""
        return None


# ── test mode ───────────────────────────────────────────────────────────────
class SimBroker(Broker):
    """Simulated fills on live data. The default; nothing here can lose money."""

    def __init__(self, slippage_bps: float = 0.0, label: str = "SIM"):
        self.slippage_bps = slippage_bps
        self.label = label
        self.is_real = False

    def _fill_price(self, price: float, side: str) -> float:
        # Slippage works against us: pay up on buys, receive less on sells.
        adj = price * self.slippage_bps / 10_000
        return round(price + adj if side == "buy" else price - adj, 2)

    def buy(self, symbol: str, shares: int, price: float) -> Fill:
        fp = self._fill_price(price, "buy")
        return Fill(symbol, "buy", shares, fp, ok=True, detail="simulated")

    def sell(self, symbol: str, shares: int, price: float) -> Fill:
        fp = self._fill_price(price, "sell")
        return Fill(symbol, "sell", shares, fp, ok=True, detail="simulated")


# ── live mode ───────────────────────────────────────────────────────────────
class RobinhoodBroker(Broker):
    """
    Live Robinhood execution. Robinhood has no paper API, so this is real money.

    Auth via environment (set in `.env`):
        ROBINHOOD_USERNAME, ROBINHOOD_PASSWORD
        ROBINHOOD_TOTP        # base32 MFA secret, required for headless/cron use
    """

    label = "ROBINHOOD-LIVE"
    is_real = True

    def __init__(self):
        try:
            import robin_stocks.robinhood as rh
        except ImportError as e:
            raise RuntimeError(
                "Live mode needs robin_stocks: pip install robin_stocks pyotp"
            ) from e
        self._rh = rh
        self._login()

    def _login(self):
        user = os.environ.get("ROBINHOOD_USERNAME")
        pw = os.environ.get("ROBINHOOD_PASSWORD")
        totp_secret = os.environ.get("ROBINHOOD_TOTP")
        if not (user and pw):
            raise RuntimeError("Set ROBINHOOD_USERNAME and ROBINHOOD_PASSWORD for live mode.")
        mfa = None
        if totp_secret:
            import pyotp
            mfa = pyotp.TOTP(totp_secret).now()
        self._rh.login(username=user, password=pw, mfa_code=mfa)

    def account_equity(self) -> Optional[float]:
        try:
            data = self._rh.profiles.load_portfolio_profile()
            return float(data.get("equity") or data.get("extended_hours_equity"))
        except Exception:
            return None

    def buy(self, symbol: str, shares: int, price: float) -> Fill:
        try:
            res = self._rh.orders.order_buy_market(symbol, shares)
            ok = bool(res and res.get("id"))
            return Fill(symbol, "buy", shares, price, ok=ok,
                        detail=res.get("id", "rejected") if res else "no response")
        except Exception as e:
            return Fill(symbol, "buy", shares, price, ok=False, detail=str(e))

    def sell(self, symbol: str, shares: int, price: float) -> Fill:
        try:
            res = self._rh.orders.order_sell_market(symbol, shares)
            ok = bool(res and res.get("id"))
            return Fill(symbol, "sell", shares, price, ok=ok,
                        detail=res.get("id", "rejected") if res else "no response")
        except Exception as e:
            return Fill(symbol, "sell", shares, price, ok=False, detail=str(e))


def make_broker(settings: Settings) -> Broker:
    """Pick the broker for the current mode, enforcing the live-trading guards."""
    if not settings.is_live:
        return SimBroker(slippage_bps=5.0)  # model a little friction in test

    if settings.dry_run:
        # Live mode but explicitly dry — simulate, clearly labelled.
        return SimBroker(slippage_bps=5.0, label="LIVE-DRYRUN")

    if not settings.live_confirmed:
        raise RuntimeError(
            "TRADING_MODE=live but LIVE_CONFIRM is not 'yes'. Refusing to place real "
            "orders. Set LIVE_CONFIRM=yes in your environment to enable live trading."
        )
    return RobinhoodBroker()
