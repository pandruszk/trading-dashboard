# Kell Autopilot — automated trading

`autopilot.py` runs an Alpaca **paper** portfolio automatically using Oliver
Kell's methodology. It is the "act on the signals" layer on top of the
[Kell screener](KELL.md).

## Strategy

**Selection (rebalance).** Take the whole-market Kell screen (buy phases —
*EMA Crossback* / *Base & Break* — with stacked MAs, positive 3-month relative
strength vs SPY, and a price/liquidity floor), then pick the **top
`TARGET_POSITIONS` (8)** by relative strength, **capped at `MAX_PER_SECTOR` (3)
names per sector**. Equal weight, sized as `equity / 8` per name — so a weak
market with few setups naturally leaves cash on the sidelines.

**Management (daily).** For each holding, re-read its Kell phase:

| Phase | Action |
|-------|--------|
| Wedge Drop / Downtrend, Reversal Extension | **EXIT** (lost its moving averages) |
| Exhaustion Extension | **TRIM** `TRIM_FRACTION` (33%) — sell into strength |
| everything else | **HOLD** |

**Schedule.** The `run` loop wakes every 30 min and, while the market is open,
runs daily maintenance once per day and a full rebalance every
`REBALANCE_DAYS` (30). On first launch (or `--reset`) it **closes all existing
positions** and builds the Kell book from scratch.

## Commands

```bash
python3 autopilot.py status                 # account + positions + schedule
python3 autopilot.py close-all [--dry-run]  # liquidate everything
python3 autopilot.py rebalance [--dry-run]  # build/refresh the book now
python3 autopilot.py daily     [--dry-run]  # run daily sells/trims now
python3 autopilot.py run [--reset] [--dry-run]   # go on autopilot
```

`--dry-run` logs every intended order **without sending it** — always worth a
look before the first real run. Double-clicking `autopilot.command` is the same
as `python3 autopilot.py run`.

## Safety rails

- **Paper-only by default.** If `APCA_API_BASE_URL` is *not* a paper endpoint,
  the autopilot **refuses to trade** unless you set `KELL_ALLOW_LIVE=yes`.
- **Dry-run** mode for a no-orders preview.
- **Cash-raising** behavior: fixed `1/8` sizing means fewer setups → more cash,
  never forced full investment.
- Never run two instances at once (you'd double orders). The trader is a
  separate process from the dashboard *by design* — the dashboard only reads
  state, it never trades.

## State it writes (consumed by the dashboard)

- `~/Documents/portfolio_state.json` — positions, entry dates, target weights, high-water marks, trade log, `last_rebalance`.
- `~/Documents/trading_schedule.json` — `last_daily`, `last_monthly`.
- `~/Documents/trading_logs/autopilot_YYYY-MM-DD.log` — every action plus an `Equity: $…` line the dashboard's history chart parses.

## Tuning

Edit the constants in `broker.py` (`TARGET_POSITIONS`, `MAX_PER_SECTOR`,
`TRIM_FRACTION`, stop percentages) and `autopilot.py` (`REBALANCE_DAYS`,
`SELL_PHASES`). The screener's filters live in `kell_scan.py`.

> Educational interpretation of a discretionary methodology on a paper
> account — **not financial advice**.
