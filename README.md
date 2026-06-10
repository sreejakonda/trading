# Minimal Statistical Day-Trading System

A small, auditable intraday trading engine. A pure-Python statistics core turns
live market data into graded, risk-sized signals; an optional Claude pass vetoes
weak setups; a pluggable broker layer executes them — **simulated by default,
real Robinhood orders only when you explicitly opt in.**

It is deliberately *minimal*: every decision is traceable to a named, well-known
statistical pattern, and account-level risk limits are enforced independently of
the strategy.

> ⚠️ **Not financial advice.** Trading involves real risk of loss. Run in `test`
> mode until you understand the behaviour, and never trade money you can't lose.

---

## How it works

```
market_data  →  indicators  →  strategy  →  advisor  →  broker
 (Yahoo 1m)     (the math)     (signals)   (Claude,    (SIM | plug in real broker)
                                            optional)
                         engine.py sequences all of it
                         trader.py is the CLI
```

| File | Responsibility |
|------|----------------|
| `config.py` | All tunables + mode/risk settings (env-overridable) |
| `market_data.py` | Yahoo Finance intraday bars (verified TLS) |
| `indicators.py` | Pure statistical functions (VWAP, RSI, Bollinger, ATR, EMA, ORB…) |
| `strategy.py` | Combines indicators into a graded, ATR-sized `Signal` |
| `advisor.py` | Optional Claude "is this a trap?" filter |
| `broker.py` | Execution: `SimBroker` (default) — extend to add a real broker |
| `state.py` | Open positions + trade ledger, namespaced by mode/strategy |
| `engine.py` | One decision cycle: data → exits → risk → entries → report |
| `trader.py` | CLI (`scan`, `run`, `report`, `status`, `doctor`) |

### The statistical patterns

Each is standard, documented, and used as either a **gate** (must pass) or a
**score** component (0–10):

- **VWAP + bands** — the session's volume-weighted fair value. Holding above
  VWAP is the institutional trend confirmation; distance in σ measures strength.
- **EMA(9/21) crossover** — fast-over-slow EMA defines the intraday trend.
- **RSI(14)** — momentum oscillator. Momentum wants 50–80 (rising, not blown
  out); mean-reversion wants < 35 (oversold).
- **Bollinger Bands / z-score** — price's deviation from its rolling mean in
  standard deviations; the core mean-reversion edge (fade ≥ 2σ stretches).
- **ATR(14)** — average true range; sizes volatility-adaptive stops and targets.
- **Opening-Range Breakout** — break of the first 30 minutes' high, a classic
  intraday momentum trigger.
- **Relative strength vs SPY** — only buy names leading the market.
- **Volume ratio** — recent vs session-average volume confirms conviction.

Two strategy profiles ship (`--strategy`):

- **`momentum`** *(default)* — buy strength: above VWAP, EMA uptrend, RSI rising,
  breaking the opening range, leading the market on rising volume.
- **`mean_reversion`** — buy weakness: ≥ 2σ below the rolling mean with RSI
  oversold and a reversal tick; targets reversion to the mean.

### Risk management (enforced regardless of strategy)

- Fixed fractional risk: position size = `risk$ / (entry − stop)`, capped by a
  max notional per position.
- Max concurrent positions.
- **Daily loss kill-switch** — once the day's realized loss hits the limit, no
  new entries.
- Time-stop on dead trades, no new entries late in the session, and a hard
  end-of-day flatten before the close.

---

## Install

Requires Python 3.8+.

```bash
git clone https://github.com/sreejakonda/trading.git
cd trading
./scripts/setup.sh          # installs deps, creates .env, runs a config check
```

Or manually:

```bash
python3 -m pip install -r requirements.txt
cp .env.example .env        # then edit .env
```

`anthropic` (Claude advisor) and `robin_stocks`/`pyotp` (live trading) are
optional — test mode needs neither.

---

## Configure

All settings live in `.env` (auto-loaded; no `source` needed). See
`.env.example` for the full list. The essentials:

```ini
TRADING_MODE=test           # test | live
CAPITAL=2000
RISK_PER_TRADE_PCT=1.0
MAX_POSITIONS=4
DAILY_LOSS_LIMIT_PCT=3.0
STRATEGY=momentum
# ANTHROPIC_API_KEY=sk-ant-...   # optional Claude advisor
```

Check what the system sees at any time:

```bash
python3 trader.py doctor
```

---

## Run

```bash
python3 trader.py scan                           # one cycle, default strategy
python3 trader.py scan --strategy mean_reversion
python3 trader.py scan --force                   # run even when market is closed
python3 trader.py run --interval 300             # loop every 5 min while open
python3 trader.py status                         # open positions
python3 trader.py report                         # win rate, expectancy, R:R, P&L
python3 trader.py backtest                       # replay the past week (see below)
```

### Backtesting

Replay the strategy over the last few days of 1-minute history and print a clean
trade blotter plus P&L — same rules and risk limits as live, no look-ahead, and
the Claude advisor disabled so the result reflects the mechanical edge:

```bash
python3 trader.py backtest                          # default strategy, ~1 week
python3 trader.py backtest --strategy mean_reversion --days 5
```

```
  Date        In     Out    Symbol  Sh   Entry     Exit      P&L       Reason
  --------------------------------------------------------------------------
  2026-06-03  10:05  10:10  TSLA    1  $ 425.57  $ 429.15  $  +3.58 ✓  target hit
  ...
  Summary
    Trades 26  ·  10W / 16L  ·  win rate 38%
    Net P&L $+2.26  ·  return on capital +0.11%  ·  R:R 1.86  ·  profit factor 1.16
```

Reports are also written to `output/backtests/`. Yahoo serves at most ~7 trading
days of 1-minute bars, so that is the backtest's reach.

### Scheduling (cron)

`scripts/run.sh` is a cron-friendly wrapper. Example — every 5 minutes on
weekdays during US market hours (EST; shift one hour for daylight saving):

```cron
*/5 13-20 * * 1-5  /full/path/to/trading/scripts/run.sh >> /full/path/to/trading/scan.log 2>&1
```

---

## Going live with Alpaca

**Test mode is the default** — it runs the full decision loop against live
market data and simulates fills with modelled slippage. No account, no money at
risk. When the strategy's behaviour looks right, step up through paper and live.

### Three modes

| `TRADING_MODE` | Broker | Money at risk |
|---|---|---|
| `test` | `SimBroker` | No — simulated fills |
| `paper` | Alpaca paper API | No — real infrastructure, fake money |
| `live` | Alpaca live API | **Yes — real orders** |

### 1. Get Alpaca credentials

Create a free account at [alpaca.markets](https://alpaca.markets). Under
**API Keys**, generate a key pair. The same key works for both paper and live
endpoints.

### 2. Install and configure

```bash
python3 -m pip install 'alpaca-py>=0.8.2'
```

In `.env`:

```ini
TRADING_MODE=paper          # start here
ALPACA_API_KEY=PKxxxxxxxxxxxxxxxxxx
ALPACA_SECRET_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

Verify:

```bash
python3 trader.py doctor    # should show mode=paper, keys present
python3 trader.py scan --force   # fires a cycle (force past market-hours check)
```

### 3. Go live

Two guards must both be set:

```ini
TRADING_MODE=live
LIVE_CONFIRM=yes            # explicit opt-in; orders refused without this
```

Paper and live keep **separate** state under `output/` and never mix.

### Adding a different broker

`broker.py` exposes a `Broker` ABC (`buy`, `sell`, `account_equity`). Add one
subclass and return it from `make_broker()` — nothing upstream changes.

---

## Autonomous vs. agentic

This repo is the **autonomous** engine: a standalone Python program suitable for
unattended cron runs that executes through a broker API. The human-in-the-loop
model — where a Claude agent proposes orders and **you approve each one** via the
Robinhood official Agentic MCP — lives in the sibling repo
[`../agentic_trading`](../agentic_trading).

---

## Quick reference

```
# ── Daily workflow ──────────────────────────────────────────────────────────
python3 trader.py run --interval 300       # start the loop (prints every 5 min)
python3 trader.py run --interval 60        # faster loop — 1 min cycles
python3 trader.py scan                     # fire one cycle and exit
python3 trader.py scan --force             # one cycle even when market is closed
python3 trader.py scan --strategy mean_reversion  # use the mean-reversion profile

# ── What's happening ────────────────────────────────────────────────────────
python3 trader.py status                   # open positions, stops, targets
python3 trader.py report                   # win rate, R:R, P&L, last 5 trades
python3 trader.py doctor                   # config, mode, API key check

# ── Research ────────────────────────────────────────────────────────────────
python3 trader.py backtest                 # replay past ~7 days, show blotter
python3 trader.py backtest --strategy mean_reversion
python3 trader.py backtest --days 5        # shorter window
```

The loop prints a timestamped block every cycle — header, market %, signals found
(or "No entry signals"), any exits, and a running P&L snapshot. A quiet cycle still
prints so you always know it's alive.

---

## Output layout

All generated files live under `output/` in a predictable, git-tracked tree:

```
output/
├── positions/   positions_<mode>_<strategy>.json   open positions
├── trades/      trades_<mode>_<strategy>.jsonl     closed-trade ledger
├── logs/        scan_<mode>_<date>.log             one line-per-cycle run log
└── backtests/   backtest_<strategy>_<date>.txt     saved backtest reports
```

Everything is namespaced by **mode** and **strategy**, so `test` and `live`
books — and `momentum` vs `mean_reversion` — never collide. Delete a file to
reset that book. Only `.env` is git-ignored.
