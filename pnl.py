#!/usr/bin/env python3
"""
P&L comparison report — run anytime: python3 ~/trading/pnl.py
Shows AGGRESSIVE vs CAUTIOUS side by side.
"""

import json, os
from collections import defaultdict
from datetime import datetime
import pytz

DIR = os.path.dirname(os.path.abspath(__file__))
ET  = pytz.timezone("America/New_York")

def load_trades(label):
    path = os.path.join(DIR, f"trades_{label}.jsonl")
    if not os.path.exists(path): return []
    trades = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try: trades.append(json.loads(line))
                except: pass
    return trades

def load_positions(label):
    path = os.path.join(DIR, f"positions_{label}.json")
    if not os.path.exists(path): return {}
    try:
        with open(path) as f: return json.load(f)
    except: return {}

def stats(trades):
    if not trades:
        return {"n":0,"wins":0,"losses":0,"wr":0,"net":0,
                "avg_w":0,"avg_l":0,"rr":0,"pf":0}
    wins   = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    net    = sum(t["pnl"] for t in trades)
    avg_w  = sum(t["pnl"] for t in wins)  / len(wins)  if wins  else 0
    avg_l  = sum(t["pnl"] for t in losses)/ len(losses) if losses else 0
    gp     = sum(t["pnl"] for t in wins)
    gl     = sum(t["pnl"] for t in losses)
    return {
        "n":    len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "wr":   len(wins)/len(trades)*100,
        "net":  net,
        "avg_w": avg_w,
        "avg_l": avg_l,
        "rr":   abs(avg_w/avg_l) if avg_l else float("inf"),
        "pf":   abs(gp/gl) if gl else float("inf"),
    }

def report(label, trades):
    today = datetime.now(ET).strftime("%Y-%m-%d")
    today_trades = [t for t in trades if t["entry_time"].startswith(today)]
    all_s  = stats(trades)
    day_s  = stats(today_trades)
    pos    = load_positions(label)

    print(f"\n  ┌─ {label.upper()} {'─'*(38-len(label))}┐")
    print(f"  │  All-time  {all_s['n']:>3} trades  "
          f"{all_s['wins']}W/{all_s['losses']}L  "
          f"WR {all_s['wr']:4.0f}%  net ${all_s['net']:+7.2f}  "
          f"R:R {all_s['rr']:.2f}  PF {all_s['pf']:.2f}     │")
    print(f"  │  Today     {day_s['n']:>3} trades  "
          f"{day_s['wins']}W/{day_s['losses']}L  "
          f"WR {day_s['wr']:4.0f}%  net ${day_s['net']:+7.2f}"
          f"{'':>20}│")
    if pos:
        print(f"  │  Open: {', '.join(pos.keys()):<43}│")

    # By exit reason
    by_reason = defaultdict(list)
    for t in trades: by_reason[t["exit_reason"]].append(t["pnl"])
    if by_reason:
        print(f"  │  Exit breakdown:{'':>33}│")
        for reason, pnls in sorted(by_reason.items()):
            w = sum(1 for p in pnls if p > 0)
            print(f"  │    {reason:<18} {len(pnls):>3}x  "
                  f"{w}/{len(pnls)} wins  net ${sum(pnls):+.2f}{'':>10}│")
    print(f"  └{'─'*50}┘")

    # Recent trades
    recent = (today_trades if today_trades else trades)[-10:]
    if recent:
        print(f"  {'Time':<6} {'Sym':<5} {'Entry':>7} {'Exit':>7} {'Sh':>3} {'P&L':>7}  Reason")
        for t in recent:
            icon = "✓" if t["pnl"] > 0 else "✗"
            tstr = t["entry_time"][11:]  # HH:MM
            print(f"  {icon} {tstr} {t['symbol']:<5} "
                  f"${t['entry_price']:>6.2f} ${t['exit_price']:>6.2f} "
                  f"{t['shares']:>2}sh  ${t['pnl']:>+6.2f}  {t['exit_reason']}")

def main():
    agg = load_trades("aggressive")
    cau = load_trades("cautious")

    today = datetime.now(ET).strftime("%Y-%m-%d")
    print("=" * 54)
    print(f"  P&L REPORT  {today}  (Phase 1 — simulated)")
    print("=" * 54)

    if not agg and not cau:
        print("  No completed trades yet. Engine is running — check back after market hours.")
        for label in ("aggressive","cautious"):
            pos = load_positions(label)
            if pos:
                print(f"\n  {label.upper()} open positions:")
                for sym, p in pos.items():
                    print(f"    {sym} @ ${p['entry_price']}  {p['shares']}sh  "
                          f"stop ${p['stop']}  tgt ${p['target']}")
        return

    report("aggressive", agg)
    report("cautious",   cau)

    # Head-to-head summary
    agg_s = stats(agg)
    cau_s = stats(cau)
    print(f"\n  HEAD-TO-HEAD")
    print(f"  {'':20} {'AGGR':>8}  {'CAUT':>8}")
    print(f"  {'Trades':20} {agg_s['n']:>8}  {cau_s['n']:>8}")
    print(f"  {'Win rate':20} {agg_s['wr']:>7.0f}%  {cau_s['wr']:>7.0f}%")
    print(f"  {'Net P&L':20} ${agg_s['net']:>+7.2f}  ${cau_s['net']:>+7.2f}")
    print(f"  {'R:R ratio':20} {agg_s['rr']:>8.2f}  {cau_s['rr']:>8.2f}")
    print(f"  {'Profit factor':20} {agg_s['pf']:>8.2f}  {cau_s['pf']:>8.2f}")
    print()

if __name__ == "__main__":
    main()
