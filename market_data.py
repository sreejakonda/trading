"""
Market data access.

Uses Yahoo Finance's public chart endpoint — free, no credentials, intraday
1-minute bars. This is the *data* source; it is independent of the *broker*
(execution) layer, so live trading on Robinhood still prices and decides off
the same bars used in test mode.
"""

from __future__ import annotations

import json
import ssl
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Optional


def _ssl_context() -> ssl.SSLContext:
    """
    Verified TLS context. Many Python installs (notably macOS framework builds)
    ship without a usable system CA bundle, so prefer certifi's. Verification is
    never disabled — that would expose the data feed to tampering.
    """
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


_SSL = _ssl_context()
_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
_CHART_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
    "?interval={interval}&range={rng}&includePrePost=false"
)


@dataclass
class Bar:
    ts: int       # unix epoch seconds
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Series:
    """An ordered intraday bar series for one symbol, plus session metadata."""

    symbol: str
    bars: List[Bar]
    prev_close: float

    @property
    def price(self) -> float:
        return self.bars[-1].close

    @property
    def open(self) -> float:
        return self.bars[0].open

    @property
    def session_high(self) -> float:
        return max(b.high for b in self.bars)

    @property
    def session_low(self) -> float:
        return min(b.low for b in self.bars)

    @property
    def closes(self) -> List[float]:
        return [b.close for b in self.bars]

    @property
    def pct_from_open(self) -> float:
        o = self.open
        return (self.price - o) / o * 100 if o else 0.0

    def bars_after(self, ts: Optional[int]) -> List[Bar]:
        if ts is None:
            return self.bars
        return [b for b in self.bars if b.ts > ts]


def _fetch(symbol: str, interval: str, rng: str) -> Optional[Series]:
    url = _CHART_URL.format(sym=symbol, interval=interval, rng=rng)
    try:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=10, context=_SSL) as resp:
            data = json.loads(resp.read())
        result = data.get("chart", {}).get("result")
        if not result:
            return None
        r = result[0]
        meta = r.get("meta", {})
        timestamps = r.get("timestamp", []) or []
        q = r["indicators"]["quote"][0]
        opens, highs = q.get("open", []), q.get("high", [])
        lows, closes = q.get("low", []), q.get("close", [])
        vols = q.get("volume", [])
        bars: List[Bar] = []
        for i, ts in enumerate(timestamps):
            o, h, l, c = opens[i], highs[i], lows[i], closes[i]
            if None in (o, h, l, c):
                continue
            v = vols[i] if i < len(vols) and vols[i] is not None else 0.0
            bars.append(Bar(ts=ts, open=o, high=h, low=l, close=c, volume=float(v)))
        if not bars:
            return None
        prev_close = (
            meta.get("previousClose")
            or meta.get("chartPreviousClose")
            or bars[0].open
        )
        return Series(symbol=symbol, bars=bars, prev_close=float(prev_close))
    except Exception:
        return None


def fetch_series(symbols: List[str], interval: str = "1m", rng: str = "1d") -> Dict[str, Series]:
    """Fetch intraday series for each symbol. Failed symbols are simply absent."""
    out: Dict[str, Series] = {}
    for sym in symbols:
        s = _fetch(sym, interval, rng)
        if s is not None:
            out[sym] = s
    return out


def last_price(symbol: str) -> Optional[float]:
    """One-shot latest price — used by the broker layer for fills/marks."""
    s = _fetch(symbol, "1m", "1d")
    return s.price if s else None
