"""
Execution layer.

A `Broker` takes the strategy's decisions and turns them into fills.

  SimBroker    — test mode. Fills are simulated against live price data
                 (with modelled slippage). No account needed; nothing can lose money.
  AlpacaBroker — paper/live mode. Official Alpaca REST API.
                 paper=True  → ALPACA-PAPER:  real market infrastructure, fake money.
                 paper=False → ALPACA-LIVE:   real money. Guarded behind LIVE_CONFIRM=yes.

`make_broker(settings)` picks the right one from the environment. Strategy and
risk code never import a concrete broker — they call this function and use the
abstract interface, so adding a new broker is one subclass + one line.
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
        return None


# ── test mode ────────────────────────────────────────────────────────────────
class SimBroker(Broker):
    """Simulated fills on live data — no account, no money at risk."""

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


# ── Alpaca (paper + live) ────────────────────────────────────────────────────
class AlpacaBroker(Broker):
    """
    Official Alpaca broker via alpaca-py.

    Credentials (set in .env):
        ALPACA_API_KEY      — starts with "PK" for paper, "AK" for live
        ALPACA_SECRET_KEY

    The same credentials work for both paper and live endpoints.
    `paper=True` routes to paper-api.alpaca.markets (real fills, fake money).
    `paper=False` routes to api.alpaca.markets (real money).
    """

    def __init__(self, paper: bool = True):
        try:
            from alpaca.trading.client import TradingClient
            from alpaca.trading.enums import OrderSide, TimeInForce
            from alpaca.trading.requests import MarketOrderRequest
        except ImportError as e:
            raise RuntimeError(
                "Alpaca broker needs alpaca-py: pip install 'alpaca-py>=0.8.2'"
            ) from e

        api_key = os.environ.get("ALPACA_API_KEY", "").strip()
        secret_key = os.environ.get("ALPACA_SECRET_KEY", "").strip()
        if not (api_key and secret_key):
            raise RuntimeError("Set ALPACA_API_KEY and ALPACA_SECRET_KEY in .env for Alpaca trading.")

        self._client = TradingClient(api_key, secret_key, paper=paper)
        self._MarketOrderRequest = MarketOrderRequest
        self._OrderSide = OrderSide
        self._TimeInForce = TimeInForce
        self.label = "ALPACA-PAPER" if paper else "ALPACA-LIVE"
        self.is_real = not paper

    def account_equity(self) -> Optional[float]:
        try:
            acct = self._client.get_account()
            return float(acct.portfolio_value)
        except Exception:
            return None

    def buy(self, symbol: str, shares: int, price: float) -> Fill:
        try:
            req = self._MarketOrderRequest(
                symbol=symbol,
                qty=shares,
                side=self._OrderSide.BUY,
                time_in_force=self._TimeInForce.DAY,
            )
            order = self._client.submit_order(req)
            ok = order.id is not None
            return Fill(symbol, "buy", shares, price, ok=ok,
                        detail=str(order.id) if ok else "rejected")
        except Exception as e:
            return Fill(symbol, "buy", shares, price, ok=False, detail=str(e))

    def sell(self, symbol: str, shares: int, price: float) -> Fill:
        try:
            req = self._MarketOrderRequest(
                symbol=symbol,
                qty=shares,
                side=self._OrderSide.SELL,
                time_in_force=self._TimeInForce.DAY,
            )
            order = self._client.submit_order(req)
            ok = order.id is not None
            return Fill(symbol, "sell", shares, price, ok=ok,
                        detail=str(order.id) if ok else "rejected")
        except Exception as e:
            return Fill(symbol, "sell", shares, price, ok=False, detail=str(e))


def make_broker(settings: Settings) -> Broker:
    """Return the broker for the current TRADING_MODE, enforcing the live guard."""
    if settings.mode == "test":
        return SimBroker(slippage_bps=5.0)

    if settings.mode == "paper":
        return AlpacaBroker(paper=True)

    # live — real money, requires an explicit confirmation flag
    if not settings.live_confirmed:
        raise RuntimeError(
            "TRADING_MODE=live but LIVE_CONFIRM is not 'yes'. Refusing to place real "
            "orders. Set LIVE_CONFIRM=yes in your .env to enable live trading."
        )
    return AlpacaBroker(paper=False)
