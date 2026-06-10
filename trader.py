#!/usr/bin/env python3
"""
Command-line entry point.

    python3 trader.py scan                 one decision cycle (default strategy)
    python3 trader.py scan --strategy mean_reversion
    python3 trader.py run --interval 300    loop every 5 min during market hours
    python3 trader.py backtest              replay the past week, show P&L
    python3 trader.py backtest --strategy mean_reversion --days 5
    python3 trader.py report                performance across strategies
    python3 trader.py status                open positions
    python3 trader.py doctor                check config and credentials
"""

from __future__ import annotations

import argparse
import os
import sys
import time as _time
from datetime import datetime


def _load_dotenv():
    """Load KEY=VALUE pairs from ./.env so the CLI works without `source`."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            key, val = key.strip(), val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)


_load_dotenv()

# Import after dotenv so config reads the populated environment.
import state                       # noqa: E402
from config import (DEFAULT_STRATEGY, STRATEGIES, ET, load_settings)  # noqa: E402
from engine import scan           # noqa: E402


def _resolve_strategy(name: str):
    if name not in STRATEGIES:
        sys.exit(f"Unknown strategy '{name}'. Choose from: {', '.join(STRATEGIES)}")
    return STRATEGIES[name]


def cmd_scan(args):
    settings = load_settings()
    cfg = _resolve_strategy(args.strategy)
    scan(settings, cfg, force=args.force)


def cmd_run(args):
    settings = load_settings()
    cfg = _resolve_strategy(args.strategy)
    print(f"Loop every {args.interval}s — Ctrl-C to stop.")
    try:
        while True:
            scan(settings, cfg, force=args.force)
            _time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")


def cmd_backtest(args):
    import backtest
    settings = load_settings()
    cfg = _resolve_strategy(args.strategy)
    _trades, report = backtest.run(settings, cfg, days=args.days)
    print(report)


def cmd_report(args):
    settings = load_settings()
    print("=" * 60)
    print(f"  PERFORMANCE REPORT  ·  {settings.mode.upper()}  ·  "
          f"{datetime.now(ET):%Y-%m-%d %H:%M ET}")
    print("=" * 60)
    any_trades = False
    for name, cfg in STRATEGIES.items():
        trades = state.load_trades(settings.mode, cfg.kind)
        if not trades:
            continue
        any_trades = True
        s = state.stats(trades)
        print(f"\n  {cfg.name} ({name})")
        print(f"    Trades {s['n']}  ·  {s['wins']}W/{s['losses']}L  ·  "
              f"win rate {s['win_rate']:.0f}%")
        print(f"    Net ${s['net']:+.2f}  ·  expectancy ${s['expectancy']:+.2f}/trade  ·  "
              f"R:R {s['rr']}  ·  profit factor {s['profit_factor']}")
        for t in trades[-5:]:
            icon = "✓" if t["pnl"] > 0 else "✗"
            print(f"      {icon} {t['symbol']:<5} {t['exit_reason']:<14} "
                  f"${t['entry_price']}→${t['exit_price']}  ${t['pnl']:+.2f}")
    if not any_trades:
        print("\n  No completed trades yet for this mode.")


def cmd_status(args):
    settings = load_settings()
    print(f"Mode: {settings.mode.upper()}")
    for name, cfg in STRATEGIES.items():
        positions = state.load_positions(settings.mode, cfg.kind)
        if not positions:
            continue
        print(f"\n  {cfg.name} — {len(positions)} open:")
        for sym, p in positions.items():
            print(f"    {sym:<5} {p['shares']}sh @ ${p['entry_price']}  "
                  f"stop ${p['stop']}  target ${p['target']}  (since {p['entry_time']})")


def cmd_doctor(args):
    settings = load_settings()
    print(f"Trading mode      : {settings.mode}")
    print(f"Capital           : ${settings.risk.capital:,.0f}")
    print(f"Risk / trade      : {settings.risk.risk_per_trade_pct}%  "
          f"(${settings.risk.max_risk_dollars:.2f})")
    print(f"Max positions     : {settings.risk.max_positions}")
    print(f"Daily loss limit  : {settings.risk.daily_loss_limit_pct}%  "
          f"(${settings.risk.daily_loss_limit_dollars:.2f})")
    print(f"Watchlist         : {', '.join(settings.watchlist)}")
    print(f"Claude advisor    : {'on' if os.environ.get('ANTHROPIC_API_KEY') else 'off (rules only)'}")
    if settings.mode in ("paper", "live"):
        have_key = bool(os.environ.get("ALPACA_API_KEY"))
        have_secret = bool(os.environ.get("ALPACA_SECRET_KEY"))
        print(f"Alpaca API key    : {'present' if have_key else 'MISSING'}")
        print(f"Alpaca secret     : {'present' if have_secret else 'MISSING'}")
        if settings.is_live:
            print(f"Live confirmed    : {'yes' if settings.live_confirmed else 'NO — set LIVE_CONFIRM=yes to enable live orders'}")


def main():
    p = argparse.ArgumentParser(description="Minimal statistical day-trading system.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_strategy(sp):
        sp.add_argument("--strategy", default=DEFAULT_STRATEGY,
                        help=f"strategy profile (default: {DEFAULT_STRATEGY})")
        sp.add_argument("--force", action="store_true",
                        help="run even when the market is closed")

    add_strategy(sub.add_parser("scan", help="run one decision cycle"))
    rp = sub.add_parser("run", help="loop on an interval")
    add_strategy(rp)
    rp.add_argument("--interval", type=int, default=300, help="seconds between cycles")
    bp = sub.add_parser("backtest", help="replay recent history and report P&L")
    bp.add_argument("--strategy", default=DEFAULT_STRATEGY,
                    help=f"strategy profile (default: {DEFAULT_STRATEGY})")
    bp.add_argument("--days", type=int, default=7, help="trading days to replay (max 7)")
    sub.add_parser("report", help="performance report")
    sub.add_parser("status", help="open positions")
    sub.add_parser("doctor", help="check configuration")

    args = p.parse_args()
    {"scan": cmd_scan, "run": cmd_run, "backtest": cmd_backtest,
     "report": cmd_report, "status": cmd_status,
     "doctor": cmd_doctor}[args.cmd](args)


if __name__ == "__main__":
    main()
