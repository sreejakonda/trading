"""
The orchestrator. One `scan()` is one decision cycle:

    fetch data → manage exits → enforce risk limits → scan for entries
               → (optional) Claude confirmation → place orders → report

Risk and strategy live in their own modules; this file only sequences them and
talks to the broker. Run it on a schedule (see scripts/run.sh) during market
hours.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import List, Optional

import advisor
import state
import strategy
from broker import Broker, make_broker
from config import ET, RiskConfig, Settings, StrategyConfig
from market_data import Series, fetch_series

MARKET_OPEN, MARKET_CLOSE = time(9, 30), time(16, 0)


def _market_open(now: datetime) -> bool:
    return now.weekday() < 5 and MARKET_OPEN <= now.time() <= MARKET_CLOSE


def _market_pct(series: Optional[Series]) -> float:
    if not series or not series.open:
        return 0.0
    return series.pct_from_open


def _manage_exits(now, settings, cfg, broker, positions, data):
    """Close positions on stop, target, time-stop, or end-of-day flatten."""
    risk: RiskConfig = settings.risk
    flatten = now.time() >= risk.flatten_at
    still_open, closed = {}, []

    for sym, pos in positions.items():
        series = data.get(sym)
        price = series.price if series else pos["entry_price"]

        reason = None
        exit_price = price
        if flatten:
            reason, exit_price = "EOD flatten", price
        elif series:
            # Replay 1-min bars since entry so we catch intrabar stop/target hits
            # even at a coarse polling interval. Stop is checked before target.
            for bar in series.bars_after(pos.get("entry_ts")):
                if bar.low <= pos["stop"]:
                    reason, exit_price = "stopped out", pos["stop"]
                    break
                if bar.high >= pos["target"]:
                    reason, exit_price = "target hit", pos["target"]
                    break

        if reason is None:
            # Time stop: cut trades that haven't worked after N minutes.
            held = (now - datetime.fromtimestamp(pos["entry_ts"], ET)).total_seconds() / 60
            if held >= risk.time_stop_minutes and price <= pos["entry_price"]:
                reason, exit_price = f"time stop {held:.0f}m", price

        if reason is None:
            still_open[sym] = pos
            continue

        fill = broker.sell(sym, pos["shares"], exit_price)
        rec = state.log_trade(settings.mode, cfg.kind, pos, fill.price, reason)
        closed.append((rec, fill))

    return still_open, closed


def _kill_switch_tripped(settings, cfg, now) -> bool:
    realized = state.realized_pnl_today(settings.mode, cfg.kind, now.strftime("%Y-%m-%d"))
    return realized <= -settings.risk.daily_loss_limit_dollars


def scan(settings: Settings, cfg: StrategyConfig, force: bool = False) -> List[str]:
    """Run one decision cycle. Returns the lines it printed (handy for logging)."""
    out: List[str] = []

    def emit(s: str = ""):
        print(s)
        out.append(s)

    now = datetime.now(ET)
    stamp = now.strftime("%H:%M ET %Y-%m-%d")

    if not force and not _market_open(now):
        emit(f"[{stamp}] Market closed.")
        return out

    broker: Broker = make_broker(settings)
    tag = "DRY-RUN" if settings.dry_run else broker.label
    emit("=" * 60)
    emit(f"  {cfg.name}  ·  {settings.mode.upper()} ({tag})  ·  {stamp}")
    emit("=" * 60)

    data = fetch_series(list(settings.watchlist) + [settings.benchmark],
                        settings.bar_interval, settings.bar_range)
    if len(data) < 3:
        emit("  DATA ERROR — skipping cycle.")
        return out
    market_pct = _market_pct(data.get(settings.benchmark))
    emit(f"  Market: {settings.benchmark} {market_pct:+.2f}% from open")

    # ── exits ──────────────────────────────────────────────────────────────
    positions = state.load_positions(settings.mode, cfg.kind)
    if positions:
        positions, closed = _manage_exits(now, settings, cfg, broker, positions, data)
        for rec, fill in closed:
            icon = "✓" if rec["pnl"] > 0 else "✗"
            ok = "" if fill.ok else "  [ORDER FAILED]"
            emit(f"  {icon} EXIT  {rec['symbol']:<5} {rec['exit_reason']:<14} "
                 f"@ ${rec['exit_price']}  P&L ${rec['pnl']:+.2f} "
                 f"({rec['pnl_pct']:+.2f}%){ok}")
        state.save_positions(settings.mode, cfg.kind, positions)

    if positions:
        emit(f"  Holding {len(positions)}/{settings.risk.max_positions}: "
             + ", ".join(positions.keys()))

    # ── entries ──────────────────────────────────────────────────────────────
    slots = settings.risk.max_positions - len(positions)
    if _kill_switch_tripped(settings, cfg, now):
        emit(f"  KILL SWITCH — daily loss limit "
             f"(${settings.risk.daily_loss_limit_dollars:.0f}) hit. No new entries.")
    elif now.time() >= settings.risk.no_entry_after:
        emit("  Past entry cutoff — managing existing positions only.")
    elif slots <= 0:
        emit("  Position book full — no new entries.")
    else:
        candidates = []
        watch = []
        for sym in settings.watchlist:
            if sym in positions or sym not in data:
                continue
            sig = strategy.generate(data[sym], cfg, settings.risk, market_pct)
            if sig.action in (strategy.BUY, strategy.STRONG_BUY):
                candidates.append(sig)
            elif sig.action == strategy.WATCH:
                watch.append(f"{sym}[{sig.score}]")
        candidates.sort(key=lambda s: s.score, reverse=True)

        if watch:
            emit("  Watch: " + ", ".join(watch))
        if not candidates:
            emit("  No entry signals this cycle.")
        else:
            for c in candidates:
                emit(f"  • {c.action:<10} {c.symbol:<5} [{c.score}/10] @ ${c.price}  "
                     f"stop ${c.stop} target ${c.target} ({c.shares}sh, "
                     f"risk ${c.risk_dollars:.2f}) — {'; '.join(c.reasons)}")

            shortlist = candidates[:slots]
            verdict = advisor.confirm(shortlist, cfg.name)
            for c in shortlist:
                if not verdict.get(c.symbol, True):
                    emit(f"  ⤷ {c.symbol}: advisor says WAIT — skipped")
                    continue
                if len(positions) >= settings.risk.max_positions:
                    break
                fill = broker.buy(c.symbol, c.shares, c.price)
                if not fill.ok:
                    emit(f"  ⤷ {c.symbol}: order failed — {fill.detail}")
                    continue
                positions[c.symbol] = {
                    "symbol": c.symbol,
                    "shares": c.shares,
                    "entry_price": fill.price,
                    "stop": c.stop,
                    "target": c.target,
                    "score": c.score,
                    "entry_ts": int(now.timestamp()),
                    "entry_time": now.strftime("%Y-%m-%dT%H:%M"),
                }
                emit(f"  ► BOUGHT {c.symbol} {c.shares}sh @ ${fill.price}  "
                     f"stop ${c.stop} target ${c.target}  ({fill.detail})")
            state.save_positions(settings.mode, cfg.kind, positions)

    # ── P&L snapshot ───────────────────────────────────────────────────────
    trades = state.load_trades(settings.mode, cfg.kind)
    if trades:
        s = state.stats(trades)
        today = state.realized_pnl_today(settings.mode, cfg.kind, now.strftime("%Y-%m-%d"))
        emit(f"  P&L today ${today:+.2f}  ·  all-time {s['n']} trades, "
             f"{s['win_rate']:.0f}% win, net ${s['net']:+.2f}, PF {s['profit_factor']}")
    return out
