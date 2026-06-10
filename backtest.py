"""
Event-driven intraday backtest over recent 1-minute history.

It replays the *same* strategy and risk rules the live engine uses, bar by bar,
with no look-ahead: a signal computed from bars[0..i] can only be filled at
bar i's close, and exits are evaluated on bars i+1 onward. Trades are
intraday-only (everything is flattened before the close), matching live
behaviour.

The Claude advisor is intentionally skipped here — a backtest reflects the
mechanical, reproducible edge of the rules alone.
"""

from __future__ import annotations

import bisect
import os
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import strategy
from config import BACKTESTS_DIR, ET, RiskConfig, Settings, StrategyConfig
from market_data import Bar, Series, fetch_series
from state import stats

SCAN_EVERY = 5  # minutes between entry scans, mirroring a 5-min cron cadence


def _by_day(bars: List[Bar]) -> Dict[str, List[Bar]]:
    out: Dict[str, List[Bar]] = defaultdict(list)
    for b in bars:
        out[datetime.fromtimestamp(b.ts, ET).strftime("%Y-%m-%d")].append(b)
    return out


def _et_time(ts: int):
    return datetime.fromtimestamp(ts, ET).time()


def _hhmm(ts: int) -> str:
    return datetime.fromtimestamp(ts, ET).strftime("%H:%M")


def _slice_series(symbol: str, bars: List[Bar], ts_list: List[int], upto_ts: int) -> Optional[Series]:
    end = bisect.bisect_right(ts_list, upto_ts)
    if end == 0:
        return None
    window = bars[:end]
    return Series(symbol=symbol, bars=window, prev_close=window[0].open)


def _simulate_day(day: str, sym_bars: Dict[str, List[Bar]], spy_bars: List[Bar],
                  cfg: StrategyConfig, risk: RiskConfig) -> List[dict]:
    """Replay one trading day; return the list of closed trades."""
    trades: List[dict] = []
    open_pos: Dict[str, dict] = {}
    realized = 0.0

    # Per-symbol fast lookups.
    ts_index = {s: [b.ts for b in bs] for s, bs in sym_bars.items()}
    ts_to_bar = {s: {b.ts: b for b in bs} for s, bs in sym_bars.items()}

    spy_open = spy_bars[0].open
    spy_close_at = {b.ts: b.close for b in spy_bars}

    for i, master in enumerate(spy_bars):
        ts = master.ts
        t = _et_time(ts)

        # ── manage exits on this minute ──────────────────────────────────────
        for sym in list(open_pos.keys()):
            bar = ts_to_bar.get(sym, {}).get(ts)
            if bar is None:
                continue
            pos = open_pos[sym]
            reason = exit_price = None
            if t >= risk.flatten_at:
                reason, exit_price = "EOD flatten", bar.close
            elif bar.low <= pos["stop"]:
                reason, exit_price = "stopped out", pos["stop"]
            elif bar.high >= pos["target"]:
                reason, exit_price = "target hit", pos["target"]
            else:
                held = (ts - pos["entry_ts"]) / 60
                if held >= risk.time_stop_minutes and bar.close <= pos["entry_price"]:
                    reason, exit_price = f"time stop {held:.0f}m", bar.close
            if reason:
                pnl = round((exit_price - pos["entry_price"]) * pos["shares"], 2)
                realized += pnl
                trades.append({**pos, "exit_time": _hhmm(ts), "exit_price": round(exit_price, 2),
                               "exit_reason": reason, "pnl": pnl,
                               "pnl_pct": round((exit_price - pos["entry_price"]) / pos["entry_price"] * 100, 2)})
                del open_pos[sym]

        # ── entries on the scan grid ─────────────────────────────────────────
        if i % SCAN_EVERY != 0 or t >= risk.no_entry_after:
            continue
        if realized <= -risk.daily_loss_limit_dollars:
            continue
        slots = risk.max_positions - len(open_pos)
        if slots <= 0:
            continue

        market_pct = (spy_close_at[ts] - spy_open) / spy_open * 100 if spy_open else 0.0
        candidates = []
        for sym, bars in sym_bars.items():
            if sym in open_pos:
                continue
            series = _slice_series(sym, bars, ts_index[sym], ts)
            if series is None or len(series.bars) < cfg.bb_period + 1:
                continue
            sig = strategy.generate(series, cfg, risk, market_pct)
            if sig.action in (strategy.BUY, strategy.STRONG_BUY) and sig.shares > 0:
                candidates.append(sig)

        candidates.sort(key=lambda s: s.score, reverse=True)
        for c in candidates[:slots]:
            open_pos[c.symbol] = {
                "date": day, "symbol": c.symbol, "shares": c.shares,
                "entry_time": _hhmm(ts), "entry_price": c.price,
                "stop": c.stop, "target": c.target, "score": c.score,
                "entry_ts": ts,
            }

    # ── force-close anything still open at the last available bar ─────────────
    last_ts = spy_bars[-1].ts
    for sym, pos in open_pos.items():
        bar = ts_to_bar.get(sym, {}).get(last_ts)
        price = bar.close if bar else pos["entry_price"]
        pnl = round((price - pos["entry_price"]) * pos["shares"], 2)
        trades.append({**pos, "exit_time": _hhmm(last_ts), "exit_price": round(price, 2),
                       "exit_reason": "session end", "pnl": pnl,
                       "pnl_pct": round((price - pos["entry_price"]) / pos["entry_price"] * 100, 2)})
    return trades


def run(settings: Settings, cfg: StrategyConfig, days: int = 7) -> Tuple[List[dict], str]:
    """Run the backtest and return (trades, formatted_report)."""
    rng = "7d" if days > 5 else "5d"
    data = fetch_series(list(settings.watchlist) + [settings.benchmark], "1m", rng)
    if settings.benchmark not in data:
        return [], "Could not fetch benchmark data; aborting backtest."

    spy_days = _by_day(data[settings.benchmark].bars)
    per_symbol_days = {sym: _by_day(s.bars) for sym, s in data.items()
                       if sym != settings.benchmark}

    trade_days = sorted(spy_days.keys())[-days:]
    all_trades: List[dict] = []
    per_day_pnl: Dict[str, float] = {}
    for day in trade_days:
        spy_bars = spy_days[day]
        if len(spy_bars) < cfg.bb_period + 1:
            continue
        sym_bars = {sym: dd[day] for sym, dd in per_symbol_days.items() if day in dd}
        day_trades = _simulate_day(day, sym_bars, spy_bars, cfg, settings.risk)
        all_trades.extend(day_trades)
        per_day_pnl[day] = round(sum(t["pnl"] for t in day_trades), 2)

    report = _format(settings, cfg, trade_days, all_trades, per_day_pnl)
    _write(cfg, report)
    return all_trades, report


def _format(settings, cfg, trade_days, trades, per_day_pnl) -> str:
    L: List[str] = []
    span = f"{trade_days[0]} → {trade_days[-1]}" if trade_days else "no data"
    L.append("=" * 78)
    L.append(f"  BACKTEST · {cfg.name} · {span} · TEST (simulated, rules only)")
    L.append("=" * 78)
    L.append(f"  Universe: {', '.join(settings.watchlist)}")
    L.append(f"  Capital ${settings.risk.capital:,.0f} · risk {settings.risk.risk_per_trade_pct}%"
             f"/trade · max {settings.risk.max_positions} positions · "
             f"stop {cfg.stop_atr_mult}×ATR / target {cfg.target_atr_mult}×ATR")
    L.append("")

    if not trades:
        L.append("  No trades — the strategy's gates were never satisfied this week.")
        L.append("  (Try --strategy mean_reversion, or a wider WATCHLIST.)")
        return "\n".join(L)

    # Blotter.
    L.append("  Date        In     Out    Symbol  Sh   Entry     Exit      P&L       Reason")
    L.append("  " + "-" * 74)
    for t in sorted(trades, key=lambda x: (x["date"], x["entry_time"])):
        icon = "✓" if t["pnl"] > 0 else "✗"
        L.append(f"  {t['date']}  {t['entry_time']}  {t['exit_time']}  "
                 f"{t['symbol']:<5} {t['shares']:>3}  "
                 f"${t['entry_price']:>7.2f}  ${t['exit_price']:>7.2f}  "
                 f"${t['pnl']:>+7.2f} {icon}  {t['exit_reason']}")
    L.append("")

    # Daily P&L.
    L.append("  Daily P&L")
    for day in trade_days:
        if day in per_day_pnl:
            v = per_day_pnl[day]
            bar = ("+" if v >= 0 else "-") * min(20, int(abs(v)))
            L.append(f"    {day}  ${v:>+8.2f}  {bar}")
    L.append("")

    # Summary.
    s = stats(trades)
    net = s["net"]
    roc = net / settings.risk.capital * 100
    best = max(trades, key=lambda x: x["pnl"])
    worst = min(trades, key=lambda x: x["pnl"])
    L.append("  Summary")
    L.append(f"    Trades {s['n']}  ·  {s['wins']}W / {s['losses']}L  ·  win rate {s['win_rate']:.0f}%")
    L.append(f"    Net P&L ${net:+.2f}  ·  return on capital {roc:+.2f}%")
    L.append(f"    Avg win ${s['avg_win']:+.2f}  ·  avg loss ${s['avg_loss']:+.2f}  ·  "
             f"R:R {s['rr']}  ·  profit factor {s['profit_factor']}")
    L.append(f"    Expectancy ${s['expectancy']:+.2f}/trade  ·  "
             f"best {best['symbol']} ${best['pnl']:+.2f}  ·  worst {worst['symbol']} ${worst['pnl']:+.2f}")
    return "\n".join(L)


def _write(cfg: StrategyConfig, report: str) -> None:
    os.makedirs(BACKTESTS_DIR, exist_ok=True)
    path = os.path.join(BACKTESTS_DIR,
                        f"backtest_{cfg.kind}_{datetime.now(ET):%Y-%m-%d}.txt")
    with open(path, "w") as f:
        f.write(report + "\n")
