"""
Optional second opinion from Claude.

The math engine decides what is *tradeable*; this asks a fast model to sanity-
check the final shortlist ("is this a clean setup or a trap?"). It is strictly a
filter — it can VETO a candidate the rules already approved, never invent a new
one. With no ANTHROPIC_API_KEY set, the engine simply acts on the rules.
"""

from __future__ import annotations

import os
from typing import Dict, List

from strategy import Signal

MODEL = "claude-haiku-4-5"


def confirm(candidates: List[Signal], strategy_name: str) -> Dict[str, bool]:
    """Return {symbol: act?}. Defaults to acting on everything if no API key."""
    if not candidates:
        return {}
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {c.symbol: True for c in candidates}

    try:
        import anthropic
    except ImportError:
        return {c.symbol: True for c in candidates}

    rows = "\n".join(
        f"{c.symbol} {c.action} [{c.score}/10] @ ${c.price} "
        f"stop ${c.stop} target ${c.target} ({c.shares}sh) — {'; '.join(c.reasons)}"
        for c in candidates
    )
    prompt = (
        f"You are a risk filter for a {strategy_name} day-trading strategy.\n"
        f"These candidates already passed strict quantitative gates:\n\n{rows}\n\n"
        "For each, reply on its own line exactly:\n"
        "TICKER ACT   — if the setup is clean\n"
        "TICKER WAIT  — if it looks like a trap (e.g. exhausted move, no room to target)\n"
        "Be selective. Reply with only those lines."
    )
    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=MODEL, max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text
    except Exception:
        return {c.symbol: True for c in candidates}

    verdict = {c.symbol: False for c in candidates}
    for line in text.splitlines():
        parts = line.strip().upper().split()
        if len(parts) >= 2 and parts[0] in verdict:
            verdict[parts[0]] = parts[1] == "ACT"
    return verdict
