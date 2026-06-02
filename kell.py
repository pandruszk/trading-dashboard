"""
Kell Cycle analysis
===================
Incorporates Oliver Kell's "Cycle of Price Action" — the framework from his
book *Victory in Stock Trading* (Kell won the 2020 U.S. Investing Championship
with a ~941% return) — into the dashboard.

For each ticker we compute Kell's "goalpost" moving averages (10 & 21 EMA,
50 & 200 SMA), measure how extended price is from them, then classify where
the stock sits in the cycle and emit a Kell-style signal:

    Reversal Extension  -> WATCH  (capitulation; wait for a reclaim)
    Wedge Drop          -> REDUCE/EXIT (lost the moving averages)
    EMA Crossback       -> BUY/ADD (low-risk pullback entry at the EMAs)
    Base & Break        -> BUY (breakout/continuation near highs)
    Uptrend (holds MAs) -> HOLD (trend intact)
    Exhaustion Extension-> TRIM (sell into strength; stretched far above 10 EMA)

See KELL.md for the methodology write-up and how each rule maps to this code.

NOTE: This is an interpretation of a publicly described, discretionary
methodology, applied to a *paper* account for educational purposes. It is
not financial advice.
"""
from datetime import datetime

# --- Kell's moving-average "goalposts" -------------------------------------
EMA_FAST = 10
EMA_SLOW = 21
SMA_TREND = 50
SMA_LONG = 200

# --- Heuristic thresholds (tunable) ----------------------------------------
EXT_EXHAUSTION = 0.12    # > +12% above the 10 EMA -> exhaustion / sell into strength
EXT_CROSSBACK = 0.04     # within +/-4% of the 10 EMA -> crossback buy zone
EXT_REVERSAL = -0.15     # > -15% below the 10 EMA -> reversal extension (capitulation)
NEAR_HIGH = 0.05         # within 5% of the 52-week high -> base & break / breakout
VOL_SPIKE = 1.5          # daily volume vs 50-day avg that flags a blow-off

_CACHE_TTL = 900         # seconds (15 min) to cache yfinance pulls


def ema(values, period):
    """Exponential moving average; returns a list the same length as values."""
    if not values:
        return []
    k = 2.0 / (period + 1)
    out = [float(values[0])]
    for v in values[1:]:
        out.append(float(v) * k + out[-1] * (1 - k))
    return out


def sma_last(values, period):
    """Simple moving average of the most recent `period` values, or None."""
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _pct(a, b):
    return (a - b) / b if b else 0.0


def analyze_series(closes, volumes=None, bench_closes=None):
    """Classify one daily-close series into a Kell Cycle phase + signal.

    closes:       list of daily closing prices, oldest -> newest
    volumes:      optional matching daily volumes (for blow-off detection)
    bench_closes: optional benchmark (e.g. SPY) closes for relative strength

    Returns a dict of metrics plus phase / signal / status / note.
    """
    closes = [float(c) for c in closes if c is not None]
    n = len(closes)
    if n < 50:
        return {"phase": "Insufficient data", "signal": "n/a",
                "status": "gray", "note": "Need ~50+ daily bars of history."}

    price = closes[-1]
    e10 = ema(closes, EMA_FAST)[-1]
    e21 = ema(closes, EMA_SLOW)[-1]
    s50 = sma_last(closes, SMA_TREND)
    s200 = sma_last(closes, SMA_LONG)

    ext10 = _pct(price, e10)
    ext21 = _pct(price, e21)
    ext50 = _pct(price, s50) if s50 else None

    # MAs "stacked" bullishly: 10 EMA > 21 EMA > 50 SMA > 200 SMA
    stacked = ((e10 > e21)
               and (s50 is None or e21 > s50)
               and (s200 is None or s50 is None or s50 > s200))

    window = closes[-252:] if n >= 252 else closes
    hi = max(window)
    off_high = _pct(price, hi)   # <= 0

    vol_ratio = None
    if volumes:
        vols = [float(v) for v in volumes if v is not None]
        if len(vols) >= 50 and sum(vols[-50:]):
            vol_ratio = vols[-1] / (sum(vols[-50:]) / 50.0)

    def ret(days):
        return _pct(price, closes[-(days + 1)]) if n > days else None

    ret_1m, ret_3m, ret_6m = ret(21), ret(63), ret(126)

    rs_3m = None
    if bench_closes and len(bench_closes) > 63 and ret_3m is not None:
        bench_ret = _pct(float(bench_closes[-1]), float(bench_closes[-64]))
        rs_3m = ret_3m - bench_ret   # excess return vs benchmark over ~3 months

    below_21 = price < e21
    below_50 = (s50 is not None) and (price < s50)

    # ---- classify into the Kell Cycle ----------------------------------
    if below_21 and (s50 is None or below_50):
        # Lost the goalposts -> downside of the cycle
        if ext10 <= EXT_REVERSAL:
            phase, signal, status = "Reversal Extension", "WATCH", "red"
            note = (f"{ext10:.0%} below the 10 EMA — capitulation. "
                    "Wait for a reclaim (wedge pop) before buying.")
        else:
            phase, signal, status = "Wedge Drop / Downtrend", "REDUCE / EXIT", "red"
            note = ("Price has lost the 10/21 EMA"
                    + (" and 50 SMA" if below_50 else "") + " — trend broken.")
    else:
        # Above the goalposts -> upside of the cycle
        if ext10 >= EXT_EXHAUSTION:
            phase, signal, status = "Exhaustion Extension", "TRIM — sell into strength", "yellow"
            spike = " on a volume spike" if (vol_ratio and vol_ratio >= VOL_SPIKE) else ""
            note = f"{ext10:+.0%} above the 10 EMA{spike} — climactic; sell into strength."
        elif abs(ext10) <= EXT_CROSSBACK:
            phase, signal, status = "EMA Crossback", "BUY / ADD", "green"
            note = "Pulled back to the 10/21 EMA with the trend intact — Kell's low-risk add zone."
        elif off_high >= -NEAR_HIGH:
            phase, signal, status = "Base & Break", "BUY — breakout", "green"
            note = f"Within {abs(off_high):.0%} of the 52-week high — breakout / continuation."
        else:
            phase, signal, status = "Uptrend (holding MAs)", "HOLD", "green"
            note = "Trend intact above the moving averages."

    return {
        "price": round(price, 2),
        "ema10": round(e10, 2),
        "ema21": round(e21, 2),
        "sma50": round(s50, 2) if s50 else None,
        "sma200": round(s200, 2) if s200 else None,
        "ext_10ema": ext10,
        "ext_21ema": ext21,
        "ext_50sma": ext50,
        "ma_stacked": stacked,
        "off_52w_high": off_high,
        "vol_vs_avg": round(vol_ratio, 2) if vol_ratio else None,
        "ret_1m": ret_1m,
        "ret_3m": ret_3m,
        "ret_6m": ret_6m,
        "rs_3m_vs_spy": rs_3m,
        # Kell trails the stop up along the moving averages rather than a fixed %
        "suggested_stop": round(e21, 2),
        "stop_basis": "21 EMA",
        "dist_to_stop": _pct(price, e21),
        "phase": phase,
        "signal": signal,
        "status": status,
        "note": note,
    }


# --- yfinance-backed fetch + cache -----------------------------------------
_cache = {}          # ticker -> (timestamp, analysis dict)
_bench_cache = {}    # symbol -> (timestamp, closes)


def _fetch_closes(ticker, period="1y"):
    """Pull daily closes + volumes from yfinance (imported lazily)."""
    import yfinance as yf
    df = yf.Ticker(ticker).history(period=period, interval="1d")
    if df is None or df.empty:
        return None, None
    closes = [c for c in df["Close"].tolist() if c == c]   # drop NaN
    volumes = df["Volume"].tolist() if "Volume" in df else None
    return closes, volumes


def _benchmark_closes(symbol="SPY"):
    now = datetime.now().timestamp()
    hit = _bench_cache.get(symbol)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    try:
        closes, _ = _fetch_closes(symbol)
    except Exception:
        closes = None
    _bench_cache[symbol] = (now, closes)
    return closes


def analyze_tickers(tickers, benchmark="SPY", limit=25):
    """Analyze a list of tickers and return Kell Cycle results (cached 15 min).

    Results are ordered with the most actionable signals first
    (red -> yellow -> green) so trims/exits and buys surface at the top.
    """
    bench = _benchmark_closes(benchmark)
    now = datetime.now().timestamp()
    out, seen = [], []

    for raw in tickers:
        t = (raw or "").strip().upper()
        if not t or t in seen:
            continue
        seen.append(t)
        if len(seen) > limit:
            break

        hit = _cache.get(t)
        if hit and now - hit[0] < _CACHE_TTL:
            out.append(hit[1])
            continue

        try:
            closes, volumes = _fetch_closes(t)
            if not closes:
                res = {"ticker": t, "phase": "No data", "signal": "n/a",
                       "status": "gray", "note": "No price data from yfinance."}
            else:
                res = analyze_series(closes, volumes, bench)
                res["ticker"] = t
        except Exception as e:  # network/parse failure -> degrade gracefully
            res = {"ticker": t, "phase": "Error", "signal": "n/a",
                   "status": "gray", "note": str(e)[:120]}

        _cache[t] = (now, res)
        out.append(res)

    rank = {"red": 0, "yellow": 1, "green": 2, "gray": 3}
    out.sort(key=lambda r: rank.get(r.get("status"), 9))
    return out
