#!/usr/bin/env python3
"""
Shared day-trading engine core.
Imported by aggressive.py and cautious.py — not run directly.
"""

import os, sys, math, json, ssl, urllib.request
from datetime import datetime, time
import pytz

_SSL = ssl.create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = ssl.CERT_NONE

try:
    import anthropic
except ImportError:
    print("ERROR: pip3 install anthropic"); sys.exit(1)

ET = pytz.timezone("America/New_York")
DIR = os.path.dirname(os.path.abspath(__file__))

BENCHMARK  = ["SPY", "QQQ"]
WATCHLIST  = ["AAPL", "NVDA", "TSLA", "AMD", "META", "MSFT",
              "AMZN", "GOOGL", "NFLX", "PLTR", "COIN", "SOFI"]

# ── time helpers ──────────────────────────────────────────────────────────────
def now_et():
    return datetime.now(ET)

def is_market_open():
    n = now_et()
    if n.weekday() >= 5: return False
    return time(9, 30) <= n.time() <= time(16, 0)

# ── data fetch ────────────────────────────────────────────────────────────────
_HDR = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

def _fetch_chart(sym):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
           "?interval=1m&range=1d&includePrePost=false")
    try:
        req = urllib.request.Request(url, headers=_HDR)
        with urllib.request.urlopen(req, timeout=8, context=_SSL) as r:
            data = json.loads(r.read())
        result = data["chart"]["result"]
        if not result: return None, None
        r        = result[0]
        meta     = r["meta"]
        ts_list  = r.get("timestamp", [])
        q        = r["indicators"]["quote"][0]
        vols     = q.get("volume", [])
        bars = []
        for i, ts in enumerate(ts_list):
            o,h,l,c = q["open"][i],q["high"][i],q["low"][i],q["close"][i]
            if None in (o,h,l,c): continue
            v = vols[i] if i < len(vols) and vols[i] is not None else 0
            bars.append({"ts":ts,"open":o,"high":h,"low":l,"close":c,"vol":v})
        return meta, bars
    except Exception:
        return None, None

def get_quotes(symbols):
    result = {}
    for sym in symbols:
        meta, bars = _fetch_chart(sym)
        if not bars: continue
        opens  = [b["open"]  for b in bars]
        highs  = [b["high"]  for b in bars]
        lows   = [b["low"]   for b in bars]
        closes = [b["close"] for b in bars]
        result[sym] = {
            "price":      closes[-1],
            "open":       opens[0],
            "high":       max(highs),
            "low":        min(lows),
            "prev_close": meta.get("previousClose") or meta.get("chartPreviousClose") or opens[0],
            "_bars":      bars,
        }
    return result

def bars_since(q_entry, entry_time_str):
    bars = q_entry.get("_bars", [])
    if not entry_time_str: return bars
    try:
        dt = ET.localize(datetime.strptime(entry_time_str, "%Y-%m-%dT%H:%M"))
        entry_ts = int(dt.timestamp())
        return [b for b in bars if b["ts"] > entry_ts]
    except Exception:
        return bars

# ── indicators ────────────────────────────────────────────────────────────────
def calc_indicators(sym, q, spy_pct):
    p,o,h,l,pc = q["price"],q["open"],q["high"],q["low"],q["prev_close"]
    bars = q.get("_bars", [])

    pct_open = (p-o)/o*100 if o else 0
    rng      = h-l

    # ── Real VWAP (cumulative volume-weighted avg price from open) ──────────
    # VWAP = Σ(typical_price × volume) / Σ(volume)
    # Far more accurate than (H+L+C)/3 snapshot — institutions trade against this.
    cum_tp_vol = sum((b["high"]+b["low"]+b["close"])/3 * b["vol"] for b in bars)
    cum_vol    = sum(b["vol"] for b in bars)
    vwap       = cum_tp_vol / cum_vol if cum_vol > 0 else (h+l+p)/3
    above_vwap = p > vwap
    vwap_gap   = (p - vwap) / vwap * 100  # how far above/below VWAP in %

    # ── Volume strength ─────────────────────────────────────────────────────
    # Compare recent 5-bar avg volume to session avg volume.
    # Rising vol on up move = institutional participation = signal is real.
    recent_bars   = bars[-5:] if len(bars) >= 5 else bars
    recent_vol    = sum(b["vol"] for b in recent_bars) / len(recent_bars) if recent_bars else 0
    session_vols  = [b["vol"] for b in bars if b["vol"] > 0]
    session_avg   = sum(session_vols) / len(session_vols) if session_vols else 1
    vol_ratio     = recent_vol / session_avg if session_avg > 0 else 1.0

    # ── Momentum quality (last 5 bars directional) ──────────────────────────
    # Count green bars (close > open) in last 5 — a crude but real trend filter.
    green_bars = sum(1 for b in recent_bars if b["close"] > b["open"])

    # ── ATR proxy (volatility = daily range / open) ─────────────────────────
    atr_proxy = rng/o*100 if o else 0

    return {
        "symbol":        sym,
        "price":         round(p, 2),
        "pct_from_open": round(pct_open, 3),
        "range_pos":     round((p-l)/rng if rng>0 else 0.5, 3),
        "above_vwap":    above_vwap,
        "vwap":          round(vwap, 2),
        "vwap_gap":      round(vwap_gap, 3),  # % above VWAP — strength indicator
        "vol_ratio":     round(vol_ratio, 2), # recent vol / session avg
        "green_bars":    green_bars,           # of last 5 bars
        "atr_proxy":     round(atr_proxy, 3),
        "rs_vs_spy":     round(pct_open - spy_pct, 3),
        "gap_pct":       round((o-pc)/pc*100 if pc else 0, 3),
    }

# ── state ─────────────────────────────────────────────────────────────────────
def load_positions(label):
    path = os.path.join(DIR, f"positions_{label}.json")
    if os.path.exists(path):
        try:
            with open(path) as f: return json.load(f)
        except Exception: pass
    return {}

def save_positions(label, positions):
    with open(os.path.join(DIR, f"positions_{label}.json"), "w") as f:
        json.dump(positions, f, indent=2)

def log_trade(label, pos, exit_price, exit_reason):
    pnl     = round((exit_price - pos["entry_price"]) * pos["shares"], 2)
    pnl_pct = round((exit_price - pos["entry_price"]) / pos["entry_price"] * 100, 3)
    record  = {
        "symbol":      pos["symbol"],
        "entry_time":  pos["entry_time"],
        "entry_price": pos["entry_price"],
        "shares":      pos["shares"],
        "stop":        pos["stop"],
        "target":      pos["target"],
        "signal":      pos.get("signal",""),
        "score":       pos.get("score",0),
        "exit_time":   now_et().strftime("%Y-%m-%dT%H:%M"),
        "exit_price":  round(exit_price,2),
        "exit_reason": exit_reason,
        "pnl":         pnl,
        "pnl_pct":     pnl_pct,
    }
    with open(os.path.join(DIR, f"trades_{label}.jsonl"), "a") as f:
        f.write(json.dumps(record) + "\n")
    return pnl, pnl_pct

def load_trades(label):
    path = os.path.join(DIR, f"trades_{label}.jsonl")
    if not os.path.exists(path): return []
    trades = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try: trades.append(json.loads(line))
                except Exception: pass
    return trades

# ── exit checker ─────────────────────────────────────────────────────────────
def check_exits(label, positions, quotes, cfg, eod=False):
    closed, still_open = [], {}
    n = now_et()
    for sym, pos in positions.items():
        if sym not in quotes:
            still_open[sym] = pos; continue
        price = quotes[sym]["price"]
        if eod:
            pnl, pnl_pct = log_trade(label, pos, price, "EOD close")
            closed.append((sym, price, "EOD close", pnl, pnl_pct)); continue
        post = bars_since(quotes[sym], pos.get("entry_time"))
        stop_hit = target_hit = False
        for bar in post:
            if bar["low"]  <= pos["stop"]:   stop_hit   = True; break
            if bar["high"] >= pos["target"]: target_hit = True; break
        if stop_hit:
            pnl, pnl_pct = log_trade(label, pos, pos["stop"], "stopped out")
            closed.append((sym, pos["stop"], "stopped out", pnl, pnl_pct))
        elif target_hit:
            pnl, pnl_pct = log_trade(label, pos, pos["target"], "target hit")
            closed.append((sym, pos["target"], "target hit", pnl, pnl_pct))
        else:
            try:
                entry_dt  = ET.localize(datetime.strptime(pos["entry_time"], "%Y-%m-%dT%H:%M"))
                mins_held = (n - entry_dt).total_seconds() / 60
            except Exception:
                mins_held = 0
            if mins_held >= cfg["time_stop_mins"] and price <= pos["entry_price"]:
                pnl, pnl_pct = log_trade(label, pos, price, "time stop")
                closed.append((sym, price, f"time stop ({mins_held:.0f}m)", pnl, pnl_pct))
            else:
                still_open[sym] = pos
    return still_open, closed

# ── LLM judgment ─────────────────────────────────────────────────────────────
def ask_claude(signals, cfg):
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return "\n".join(
            f"{s['symbol']} {'ACT' if s['score'] >= cfg['strong_score'] else 'WAIT'} — no API key, rule-based"
            for s in signals)
    client = anthropic.Anthropic(api_key=key)
    rows = "\n".join(
        f"{s['symbol']} {s['signal']} [{s['score']}/11] "
        f"mom:{s['pct_from_open']:+.1f}% rs:{s['rs_vs_spy']:+.1f}% "
        f"range:{s['range_pos']:.2f} → stop${s['stop']} tgt${s['target']} ({s['shares']}sh)"
        for s in signals)
    msg = client.messages.create(
        model="claude-haiku-4-5", max_tokens=120,
        messages=[{"role":"user","content":(
            f"Strategy: {cfg['name']}. Capital $200, max {cfg['max_positions']} positions.\n\n"
            f"Signals:\n{rows}\n\n"
            "Reply per signal: [TICKER] ACT or WAIT — <10 word reason\n"
            "If none: NO ACTION — <reason>")}])
    return msg.content[0].text.strip()

# ── run engine ────────────────────────────────────────────────────────────────
def run(cfg):
    """
    cfg keys: name, label, log_file, gates_fn, score_fn,
              stop_pct, target_pct, max_positions, time_stop_mins,
              no_entry_after, strong_score, allow_reentry
    """
    n       = now_et()
    ts      = n.strftime("%H:%M")
    date    = n.strftime("%Y-%m-%d")
    lines   = []   # collected for log file

    def emit(s=""):
        print(s)
        lines.append(s)

    if not is_market_open():
        emit(f"[{ts}] Market closed.")
        _append_log(cfg["log_file"], date, lines)
        return

    emit(f"\n[{ts}] ── {cfg['name'].upper()} ──────────────────────────────────")

    quotes = get_quotes(BENCHMARK + WATCHLIST)
    if len(quotes) < 3:
        emit("  DATA ERROR — NO ACTION.")
        _append_log(cfg["log_file"], date, lines)
        return

    spy_pct = qqq_pct = 0.0
    if "SPY" in quotes:
        s = quotes["SPY"]
        spy_pct = (s["price"]-s["open"])/s["open"]*100 if s["open"] else 0
    if "QQQ" in quotes:
        q = quotes["QQQ"]
        qqq_pct = (q["price"]-q["open"])/q["open"]*100 if q["open"] else 0
    emit(f"  Benchmark │ SPY {spy_pct:+.2f}%  QQQ {qqq_pct:+.2f}%")

    # ── exits ──
    positions = load_positions(cfg["label"])
    eod       = n.time() >= time(15, 50)
    if positions:
        positions, closed = check_exits(cfg["label"], positions, quotes, cfg, eod=eod)
        for sym, ep, reason, pnl, pnl_pct in closed:
            icon = "✓" if pnl > 0 else "✗"
            emit(f"  {icon} EXIT  {sym:<5} {reason:<18} ${ep}  P&L ${pnl:+.2f} ({pnl_pct:+.2f}%)")
        save_positions(cfg["label"], positions)

    # ── open positions ──
    if positions:
        parts = []
        for sym, pos in positions.items():
            price     = quotes.get(sym,{}).get("price", pos["entry_price"])
            unreal    = (price - pos["entry_price"]) * pos["shares"]
            try:
                entry_dt  = ET.localize(datetime.strptime(pos["entry_time"],"%Y-%m-%dT%H:%M"))
                mins_held = int((n-entry_dt).total_seconds()/60)
            except Exception:
                mins_held = 0
            parts.append(f"{sym} ${unreal:+.2f} ({mins_held}m)")
        emit("  HOLDING: " + "  │  ".join(parts))

    # ── entry scan ──
    slots = cfg["max_positions"] - len(positions)
    if slots <= 0 or n.time() >= cfg["no_entry_after"]:
        if n.time() >= cfg["no_entry_after"]:
            emit("  No new entries (past cutoff).")
        else:
            emit(f"  Positions full ({cfg['max_positions']}/{cfg['max_positions']}).")
    else:
        # Build candidate list — skip already-held unless re-entry allowed
        skip_syms = set() if cfg.get("allow_reentry") else set(positions.keys())
        # Also skip stocks closed today if re-entry disabled
        if not cfg.get("allow_reentry"):
            today_exits = _trades_today(cfg["label"], date)
            skip_syms.update(t["symbol"] for t in today_exits)

        actionable, watch_list = [], []
        for sym in WATCHLIST:
            if sym in skip_syms or sym not in quotes: continue
            ind    = calc_indicators(sym, quotes[sym], spy_pct)
            signal, score = cfg["decision_fn"](ind)
            if signal in ("STRONG BUY","BUY"):
                stop   = round(ind["price"] * (1 - cfg["stop_pct"]/100), 2)
                target = round(ind["price"] * (1 + cfg["target_pct"]/100), 2)
                rsk    = ind["price"] - stop
                shares = math.floor(min(4/rsk, 40/ind["price"])) if rsk > 0 else 0
                if shares > 0:
                    actionable.append({**ind,"signal":signal,"score":score,
                                       "stop":stop,"target":target,
                                       "shares":shares,"deployed":round(shares*ind["price"],2)})
            elif signal == "WATCH":
                watch_list.append(f"{sym}[{score}]")

        for s in actionable:
            emit(f"  ► {s['signal']:<11} {s['symbol']:<5} [{s['score']}/11] "
                 f"${s['price']}  mom:{s['pct_from_open']:+.1f}%  rs:{s['rs_vs_spy']:+.1f}%  "
                 f"→ stop${s['stop']} tgt${s['target']} ({s['shares']}sh)")
        if watch_list:
            emit(f"  WATCH: {', '.join(watch_list)}")
        if not actionable:
            emit("  No signals.")
        else:
            judgment = ask_claude(actionable[:slots], cfg)
            for jline in judgment.splitlines():
                emit(f"  LLM: {jline}")
            for jline in judgment.splitlines():
                parts = jline.strip().split()
                if len(parts) >= 2 and parts[1].upper() == "ACT":
                    sym   = parts[0].upper()
                    match = next((s for s in actionable if s["symbol"]==sym), None)
                    if match and sym not in positions and len(positions) < cfg["max_positions"]:
                        positions[sym] = {
                            "symbol":      sym,
                            "entry_price": match["price"],
                            "shares":      match["shares"],
                            "stop":        match["stop"],
                            "target":      match["target"],
                            "signal":      match["signal"],
                            "score":       match["score"],
                            "entry_time":  n.strftime("%Y-%m-%dT%H:%M"),
                        }
                        emit(f"  ENTERED {sym} @ ${match['price']}  "
                             f"{match['shares']}sh  stop${match['stop']}  tgt${match['target']}")
            save_positions(cfg["label"], positions)

    # ── P&L snapshot ──
    trades = load_trades(cfg["label"])
    today  = [t for t in trades if t["entry_time"].startswith(date)]
    if today:
        day_pnl  = sum(t["pnl"] for t in today)
        day_wins = sum(1 for t in today if t["pnl"] > 0)
        emit(f"  Today: {len(today)} trades  {day_wins}W/{len(today)-day_wins}L  "
             f"P&L ${day_pnl:+.2f}")
    total_pnl = sum(t["pnl"] for t in trades)
    if trades:
        wr = sum(1 for t in trades if t["pnl"]>0) / len(trades) * 100
        emit(f"  All-time: {len(trades)} trades  {wr:.0f}% win rate  "
             f"net ${total_pnl:+.2f}")

    _append_log(cfg["log_file"], date, lines)

# ── log file helpers ──────────────────────────────────────────────────────────
def _append_log(log_file, date, lines):
    path = os.path.join(DIR, log_file)
    with open(path, "a") as f:
        for line in lines:
            f.write(line + "\n")

def _trades_today(label, date):
    return [t for t in load_trades(label) if t["entry_time"].startswith(date)]

