# Kell Cycle — Oliver Kell's *Cycle of Price Action* in this dashboard

This documents how Oliver Kell's trading methodology (from *Victory in Stock
Trading: Strategy and Tactics of the 2020 U.S. Investing Champion*) is
incorporated into the dashboard, and exactly how each idea maps to the code in
[`kell.py`](kell.py) and the `/api/kell` endpoint in [`app.py`](app.py).

> **Disclaimer.** This is a software interpretation of a *publicly described,
> discretionary* methodology, applied to a **paper** account for educational
> purposes. Phase detection is a heuristic approximation of chart reading — it
> is **not** financial advice, and it is not a substitute for reading the book.

---

## 1. The methodology, in brief

Oliver Kell won the 2020 U.S. Investing Championship (~941% return). His
framework, the **Cycle of Price Action** ("the Kell Cycle"), describes the
repeating phases a stock moves through, read primarily from price action and a
small set of moving averages.

**The "goalpost" moving averages**

| MA | Role |
|----|------|
| **10 EMA** | Primary line for entries, exits, and trailing in a strong trend |
| **21 EMA** | Slightly looser trend line; the other half of the "crossback" zone |
| **50 SMA** | Trend / structure; gauge of how extended a name is |
| **200 SMA** | Long-term trend context |

**The cycle phases (in order)**

1. **Reversal Extension** — capitulation; price stretched far *below* the EMAs, peak fear. A potential bottom, but not yet a buy.
2. **Wedge Pop** — first bullish thrust: price reclaims the 10/21 EMA out of a tightening range. First buy trigger.
3. **EMA Crossback** — first pullback *to* the 10/21 EMA after a pop that holds. Kell's signature low-risk add.
4. **Base & Break** — consolidation at/above the MAs, then a breakout (often a cup-with-handle). Buy the break.
5. **Exhaustion Extension** — parabolic move stretched far *above* the 10 EMA, often on a volume blow-off. **Sell into strength.**
6. **Wedge Drop** — the move rolls over and loses the EMAs, leading back toward a reversal. Reduce / avoid.

**Risk management**

- Initial stop just below the pivot / base low.
- Then **trail the stop up along the moving averages** (10 EMA for aggressive, 21 EMA / 50 SMA for looser) rather than using a fixed percentage.
- Cut losses quickly; concentrate in **relative-strength leaders** trading near highs with the MAs stacked bullishly.

---

## 2. How it maps to the code

`kell.analyze_series(closes, volumes, bench_closes)` turns one daily-close
series into a phase + signal. The classification (see `kell.py`):

| Condition (relative to the goalposts) | Phase | Signal | Status |
|---|---|---|---|
| Below 21 EMA & 50 SMA, and **> 15% below** 10 EMA | Reversal Extension | `WATCH` | 🔴 |
| Below 21 EMA & 50 SMA (but not capitulating) | Wedge Drop / Downtrend | `REDUCE / EXIT` | 🔴 |
| **> 12% above** 10 EMA (× volume spike) | Exhaustion Extension | `TRIM — sell into strength` | 🟡 |
| Within **±4%** of the 10 EMA, trend intact | EMA Crossback | `BUY / ADD` | 🟢 |
| Within **5%** of the 52-week high | Base & Break | `BUY — breakout` | 🟢 |
| Otherwise above the MAs | Uptrend (holding MAs) | `HOLD` | 🟢 |

The thresholds live at the top of `kell.py` and are tunable:

```python
EXT_EXHAUSTION = 0.12   # > +12% above 10 EMA -> sell into strength
EXT_CROSSBACK  = 0.04   # within ±4% of 10 EMA -> low-risk add
EXT_REVERSAL   = -0.15  # > -15% below 10 EMA -> capitulation
NEAR_HIGH      = 0.05   # within 5% of 52-week high -> breakout
VOL_SPIKE      = 1.5    # daily volume vs 50-day avg -> blow-off
```

Each result also carries the Kell-style **trailing stop suggestion** — the
**21 EMA** (`suggested_stop`), reflecting "trail up the moving averages"
instead of the dashboard's existing fixed 20% trailing stop — plus relative
strength vs SPY over ~3 months (`rs_3m_vs_spy`) to favor leaders.

## 3. Where it shows up

- **Endpoint:** `GET /api/kell` — analyzes your current Alpaca holdings, plus any `?tickers=NVDA,AAPL` watchlist. Results are cached 15 minutes and ordered most-actionable-first (exits/trims → buys → holds).
- **Dashboard:** a **Kell Cycle** card with a saved watchlist box, showing each name's phase, signal, extension from the 10/21 EMA and 50 SMA, 3-month relative strength, and the 21-EMA stop.
- **Context / thesis:** results are enriched (via `kell.enrich`) with the **company name**, **sector/industry**, **market-cap size**, and a one-line **thesis** that combines them with the Kell reason the name surfaced (e.g. *"Large-cap Technology name pulled back to its 10/21 EMA with the trend intact; +18% relative strength vs the market (3M)."*). Company metadata is cached 24h and, in the screener, fetched only for the filtered matches to keep the scan fast.

## 3b. Whole-market screener ([`kell_scan.py`](kell_scan.py))

The cycle classifier above *reads names you give it*. The screener does the
**discovery** half of Kell's workflow — sweeping the market to find names
*currently* setting up.

- **Universe:** the full list of US-listed common stocks from the public
  [NASDAQ Trader symbol file](https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt)
  (ETFs / warrants / units / test issues filtered out; cached daily). Falls
  back to a built-in large-cap list if that download is unavailable.
- **Filter:** keeps names in a **buy phase** (EMA Crossback / Base & Break)
  with **stacked MAs** and **positive 3-month relative strength vs SPY**, above
  a price (`MIN_PRICE`) and dollar-volume (`MIN_DOLLAR_VOL`) floor so results
  are tradeable. Ranked by relative strength, strongest first.
- **Runs as a background job** — a full-market sweep hits thousands of tickers
  and takes minutes, so it batch-downloads via yfinance on a worker thread,
  writes results to `kell_scan.json`, and the dashboard polls progress.
  - `POST /api/kell/scan/run[?max=N]` — start a scan (optionally cap the universe)
  - `GET  /api/kell/scan` — latest cached results + live status
  - `python3 kell_scan.py [N]` — run standalone (for a cron/launchd schedule)
- **Dashboard:** a **Kell Screener — Whole Market** card with a *Rescan
  market* button, live progress, and the ranked candidate list.

## 4. Possible next steps

- Feed Kell signals into the **Alerts** panel (e.g., surface every `TRIM`/`EXIT`).
- Replace the fixed 20% trailing stop in the risk engine with the **21-EMA / 50-SMA trail** when a position is in an uptrend phase.
- Add true **wedge pop / wedge drop** detection (range-tightening + reclaim/loss) for the two transition phases currently folded into adjacent states.
- If/when the autopilot bot is built, have it **act** on these signals (trim exhaustion, add on crossbacks) instead of only displaying them.

---

### Sources

- [Cycle of Price Action — Oliver Kell (TraderLion)](https://traderlion.com/technical-analysis/chart-patterns/cycle-of-price-action-by-oliver-kell/)
- [Oliver Kell's EMA Crossback (TraderLion)](https://traderlion.com/technical-analysis/trading-the-ema-crossback/)
- [Oliver Kell: 5 Screens to Find Stocks Like a Champion (Deepvue)](https://deepvue.com/screener/oliver-kell-screens/)
- [Victory in Stock Trading — Google Books](https://books.google.com/books/about/Victory_in_Stock_Trading_Strategies_and.html?id=QhctEAAAQBAJ)
- [kelltrading.com](https://www.kelltrading.com/)
