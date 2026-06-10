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
 (Yahoo 1m)     (the math)     (signals)   (Claude,    (SIM | Robinhood)
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
| `broker.py` | Execution: `SimBroker` (test) / `RobinhoodBroker` (live) |
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
python3 trader.py scan                      # one cycle, default strategy
python3 trader.py scan --strategy mean_reversion
python3 trader.py scan --force              # run even when the market is closed
python3 trader.py run --interval 300        # loop every 5 min while open
python3 trader.py status                    # open positions
python3 trader.py report                    # win rate, expectancy, R:R, P&L
python3 trader.py backtest                  # replay the past week (see below)
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

## Switching from test mode to live mode

**Test mode is the default and risks no money** — it places simulated fills
(with modelled slippage) against live market data, so you can validate the
strategy and your P&L expectations first.

Robinhood has **no paper-trading API**, so going live means *real orders on a
real account*. Three independent guards must all be satisfied:

1. **`TRADING_MODE=live`** — selects the Robinhood broker.
2. **`LIVE_CONFIRM=yes`** — without this, live orders are *refused* even in live
   mode (the engine raises rather than trade).
3. **Robinhood credentials** present in `.env`.

Step by step:

```ini
# .env
TRADING_MODE=live
LIVE_CONFIRM=yes
ROBINHOOD_USERNAME=you@example.com
ROBINHOOD_PASSWORD=your-password
ROBINHOOD_TOTP=YOURBASE32MFASECRET   # required for unattended/cron runs
```

Then install the live dependencies, log in once, and confirm:

```bash
python3 -m pip install robin_stocks pyotp
python3 trader.py login       # interactive: you type your own credentials/MFA
python3 trader.py doctor      # should show mode=live, creds present, confirmed
```

`login` prompts for your username, password, and MFA via `getpass` and hands
them straight to Robinhood; nothing is logged or stored by this tool except the
session token Robinhood itself caches (`~/.tokens/robinhood.pickle`), which lets
later runs skip the prompt. For unattended/cron use, set `ROBINHOOD_TOTP` so a
fresh MFA code can be generated automatically.

**Recommended first step into live: dry run.** Set `DRY_RUN=true` to run the
*entire* live pipeline (real data, real risk checks, real decisions) while
sending **no** orders — they're logged as `LIVE-DRYRUN` fills instead. When the
log looks right, set `DRY_RUN=false`.

To go back to safety at any time, set `TRADING_MODE=test` (or `LIVE_CONFIRM=no`).

> Test and live keep **separate** state under `output/`, so they never mix.

### Autonomous vs. agentic — and the Robinhood MCP

This repo is the **autonomous** engine: a standalone program that executes
through a broker **API** (currently `robin_stocks`), suitable for unattended/cron
runs. A standalone program **cannot** call MCP tools — those are only callable by
a Claude agent — so Robinhood's official Agentic **MCP** does not belong here.

That human-in-the-loop, MCP-based model lives in the sibling repo
[`../agentic_trading`](../agentic_trading): a Claude agent analyses, proposes
sized orders, and **you approve** each one.

For *this* autonomous repo, execution is isolated behind the `Broker` interface
in `broker.py` (`buy`, `sell`, `account_equity`). To use a broker with an
official API (Alpaca is the usual reliable choice — official REST + real paper
trading), add one `Broker` subclass and return it from `make_broker()` — no
strategy, risk, or engine code changes.

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
