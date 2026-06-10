"""
Persistence of open positions and the completed-trade ledger.

State is namespaced by (mode, strategy) so a test run can never read or clobber
live state, and momentum/mean-reversion books stay separate. Files live under
`state/` and are git-ignored.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Dict, List

from config import ET, POSITIONS_DIR, TRADES_DIR


def _positions_path(mode: str, strategy: str) -> str:
    return os.path.join(POSITIONS_DIR, f"positions_{mode}_{strategy}.json")


def _trades_path(mode: str, strategy: str) -> str:
    return os.path.join(TRADES_DIR, f"trades_{mode}_{strategy}.jsonl")


def load_positions(mode: str, strategy: str) -> Dict[str, dict]:
    path = _positions_path(mode, strategy)
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_positions(mode: str, strategy: str, positions: Dict[str, dict]) -> None:
    os.makedirs(POSITIONS_DIR, exist_ok=True)
    with open(_positions_path(mode, strategy), "w") as f:
        json.dump(positions, f, indent=2)


def log_trade(mode: str, strategy: str, pos: dict, exit_price: float, reason: str) -> dict:
    """Append a closed trade to the ledger and return the record (with P&L)."""
    os.makedirs(TRADES_DIR, exist_ok=True)
    entry, shares = pos["entry_price"], pos["shares"]
    pnl = round((exit_price - entry) * shares, 2)
    pnl_pct = round((exit_price - entry) / entry * 100, 3) if entry else 0.0
    record = {
        "symbol": pos["symbol"],
        "strategy": strategy,
        "mode": mode,
        "entry_time": pos["entry_time"],
        "entry_price": entry,
        "shares": shares,
        "stop": pos["stop"],
        "target": pos["target"],
        "score": pos.get("score", 0),
        "exit_time": datetime.now(ET).strftime("%Y-%m-%dT%H:%M"),
        "exit_price": round(exit_price, 2),
        "exit_reason": reason,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
    }
    with open(_trades_path(mode, strategy), "a") as f:
        f.write(json.dumps(record) + "\n")
    return record


def load_trades(mode: str, strategy: str) -> List[dict]:
    path = _trades_path(mode, strategy)
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def stats(trades: List[dict]) -> dict:
    """Standard performance summary: win rate, expectancy, R:R, profit factor."""
    if not trades:
        return {"n": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "net": 0.0,
                "avg_win": 0.0, "avg_loss": 0.0, "rr": 0.0, "profit_factor": 0.0,
                "expectancy": 0.0}
    wins = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in trades if t["pnl"] <= 0]
    gross_win, gross_loss = sum(wins), sum(losses)
    avg_win = gross_win / len(wins) if wins else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0
    n = len(trades)
    return {
        "n": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / n * 100,
        "net": round(gross_win + gross_loss, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "rr": round(abs(avg_win / avg_loss), 2) if avg_loss else float("inf"),
        "profit_factor": round(abs(gross_win / gross_loss), 2) if gross_loss else float("inf"),
        "expectancy": round((gross_win + gross_loss) / n, 2),
    }


def realized_pnl_today(mode: str, strategy: str, date_str: str) -> float:
    return round(sum(t["pnl"] for t in load_trades(mode, strategy)
                     if t["entry_time"].startswith(date_str)), 2)
