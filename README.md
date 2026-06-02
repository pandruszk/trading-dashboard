# Kell Autopilot

An automated, end-to-end stock-trading system built around **Oliver Kell's
*Cycle of Price Action*** (from *Victory in Stock Trading*, the 2020 U.S.
Investing Champion). It runs on an Alpaca **paper** account.

It has three parts that share one engine:

| Component | File | What it does |
|-----------|------|--------------|
| **Cycle classifier** | `kell.py` | Reads any stock through Kell's cycle (reversal → crossback → base & break → exhaustion → wedge drop) using the 10/21 EMA and 50/200 SMA; emits a buy/hold/trim/exit signal. |
| **Whole-market screener** | `kell_scan.py` | Sweeps the full US common-stock universe for names in Kell **buy phases** with stacked MAs, relative strength, and liquidity — ranked best-first. |
| **Autopilot** | `autopilot.py` | Closes out positions, builds a concentrated book from the top screener setups, manages it with Kell sell rules, and runs on a schedule. |
| **Dashboard** | `app.py` | A live web dashboard (holdings, risk, the Kell Cycle card, the screener, and the autopilot's state). |
| **Shared broker/config** | `broker.py` | Alpaca connection + risk/strategy constants used by all of the above. |

See **[KELL.md](KELL.md)** for the methodology and **[AUTOPILOT.md](AUTOPILOT.md)**
for how the automated trading works.

> ⚠️ Educational software for a **paper** account. Automated trading carries
> real financial risk if pointed at a live account. **Not financial advice.**

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Provide Alpaca **paper** keys via `~/.alpaca_keys` (or env vars):

```
APCA_API_KEY_ID=PK...
APCA_API_SECRET_KEY=...
APCA_API_BASE_URL=https://paper-api.alpaca.markets
```

**Watch it** (dashboard): double-click `start.command`, then open
<http://localhost:5050> (password set in `app.py`).

**Run it** (autopilot): double-click `autopilot.command`, or:

```bash
python3 autopilot.py status              # see account + positions
python3 autopilot.py run --dry-run       # show what it WOULD trade
python3 autopilot.py run                 # go live on autopilot (paper)
```

The autopilot writes `~/Documents/portfolio_state.json`,
`trading_schedule.json`, and `trading_logs/` — which the dashboard reads, so
the two stay in sync.
