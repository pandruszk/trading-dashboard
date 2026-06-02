"""
Kell Screener — full-market scan for Oliver Kell buy setups
===========================================================
Sweeps the US common-stock universe and surfaces the names *currently* in
Kell's buy phases (EMA Crossback / Base & Break) with stacked moving averages,
positive relative strength, and enough price/liquidity to be tradeable —
ranked strongest-first.

A full-market scan hits thousands of tickers, so it runs as a background job:
`start_scan_async()` spawns a thread, progress is exposed via `scan_status()`,
and results are cached to disk (`load_scan()`). The dashboard reads the cache
and polls the status while a scan is running.

Can also be run standalone for a cron/launchd job:  python3 kell_scan.py
"""
import os
import json
import time
import threading
from datetime import datetime
from urllib.request import urlopen, Request

import kell

# --- Where the scan results + cached symbol list live (both gitignored) -----
HERE = os.path.dirname(os.path.abspath(__file__))
SCAN_FILE = os.path.join(HERE, "kell_scan.json")
UNIVERSE_FILE = os.path.join(HERE, "universe_cache.json")

# Nasdaq Trader publishes the full list of US-listed symbols (pipe-delimited)
NASDAQ_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"
UNIVERSE_TTL = 24 * 3600        # refresh the symbol list at most once a day

# Keep the scan to tradeable names
MIN_PRICE = 5.0                 # ignore sub-$5 names
MIN_DOLLAR_VOL = 3_000_000      # 50-day avg of close * volume
BUY_PHASES = ("EMA Crossback", "Base & Break")

BATCH_SIZE = 150               # tickers per yfinance batch download
MAX_RESULTS = 100              # top matches (by RS) to keep + enrich with company info

# Used only if the symbol-list download is unavailable (offline / blocked)
FALLBACK_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "TSLA", "AVGO",
    "AMD", "NFLX", "ADBE", "CRM", "ORCL", "CSCO", "INTC", "QCOM", "TXN",
    "MU", "AMAT", "LRCX", "KLAC", "SNPS", "CDNS", "MRVL", "PLTR", "SMCI",
    "ARM", "PANW", "CRWD", "ZS", "NET", "DDOG", "SNOW", "MDB", "FTNT",
    "NOW", "INTU", "WDAY", "TEAM", "SHOP", "UBER", "ABNB", "DASH", "COIN",
    "HOOD", "SOFI", "SQ", "PYPL", "RBLX", "U", "DKNG", "AFRM", "TTD",
    "JPM", "BAC", "WFC", "GS", "MS", "V", "MA", "AXP", "BLK", "SCHW",
    "BRK-B", "UNH", "LLY", "JNJ", "MRK", "ABBV", "PFE", "TMO", "ABT",
    "DHR", "ISRG", "VRTX", "REGN", "AMGN", "GILD", "MRNA", "BIIB",
    "XOM", "CVX", "COP", "SLB", "EOG", "OXY", "MPC", "PSX",
    "WMT", "COST", "HD", "LOW", "TGT", "NKE", "SBUX", "MCD", "CMG",
    "PG", "KO", "PEP", "PM", "MDLZ", "CL",
    "DIS", "CMCSA", "T", "VZ", "TMUS",
    "BA", "CAT", "DE", "GE", "HON", "LMT", "RTX", "UPS", "FDX",
    "F", "GM", "RIVN", "LCID", "LULU", "DECK", "ANF", "CELH", "ELF",
    "VST", "CEG", "NEE", "ENPH", "FSLR", "SEDG",
]

# --- Background scan state --------------------------------------------------
_scan_status = {
    "state": "idle",            # idle | running | done | error
    "stage": None,              # scanning | enriching (while running)
    "scanned": 0,
    "total": 0,
    "matches": 0,
    "started": None,
    "finished": None,
    "error": None,
}
_scan_lock = threading.Lock()


def _parse_nasdaq(text):
    """Parse the nasdaqtraded.txt file into a clean list of common-stock tickers."""
    lines = text.splitlines()
    if not lines:
        return []
    header = lines[0].split("|")
    idx = {name.strip(): i for i, name in enumerate(header)}
    sym_i = idx.get("Symbol")
    etf_i = idx.get("ETF")
    test_i = idx.get("Test Issue")
    if sym_i is None:
        return []

    out = set()
    for line in lines[1:]:
        if line.startswith("File Creation Time"):
            continue
        parts = line.split("|")
        if len(parts) <= sym_i:
            continue
        sym = parts[sym_i].strip().upper()
        etf = parts[etf_i].strip() if etf_i is not None and len(parts) > etf_i else "N"
        test = parts[test_i].strip() if test_i is not None and len(parts) > test_i else "N"
        if not sym or etf == "Y" or test == "Y":
            continue
        # Drop warrants / units / preferreds / rights (non-alpha or 5-letter class tickers)
        if not sym.isalpha() or len(sym) > 5:
            continue
        out.add(sym)
    return sorted(out)


def get_universe(force=False):
    """Return the scan universe, refreshing the cached symbol list daily."""
    now = time.time()
    if not force and os.path.exists(UNIVERSE_FILE):
        try:
            with open(UNIVERSE_FILE) as f:
                cached = json.load(f)
            if now - cached.get("ts", 0) < UNIVERSE_TTL and cached.get("tickers"):
                return cached["tickers"]
        except Exception:
            pass
    try:
        req = Request(NASDAQ_URL, headers={"User-Agent": "Mozilla/5.0"})
        text = urlopen(req, timeout=30).read().decode("utf-8", "ignore")
        tickers = _parse_nasdaq(text)
        if tickers:
            try:
                with open(UNIVERSE_FILE, "w") as f:
                    json.dump({"ts": now, "tickers": tickers}, f)
            except Exception:
                pass
            return tickers
    except Exception:
        pass
    return list(FALLBACK_UNIVERSE)


def _is_candidate(res):
    """A Kell buy candidate: in a buy phase, stacked MAs, outperforming the market."""
    if res.get("phase") not in BUY_PHASES:
        return False
    if not res.get("ma_stacked"):
        return False
    rs = res.get("rs_3m_vs_spy")
    return rs is not None and rs > 0


def _extract(data, ticker, single):
    """Pull (closes, volumes) for a ticker out of a yfinance download frame."""
    try:
        sub = data if single else data[ticker]
        if sub is None or sub.empty:
            return None, None
        closes = [c for c in sub["Close"].tolist() if c == c]
        volumes = sub["Volume"].tolist() if "Volume" in sub else None
        return closes, volumes
    except Exception:
        return None, None


def run_scan(max_tickers=None, batch_size=BATCH_SIZE):
    """Scan the universe for Kell buy setups (blocking). Writes results to disk."""
    import yfinance as yf

    with _scan_lock:
        if _scan_status["state"] == "running":
            return dict(_scan_status)
        _scan_status.update(state="running", stage="scanning", scanned=0, total=0,
                            matches=0, started=datetime.now().isoformat(),
                            finished=None, error=None)

    try:
        universe = get_universe()
        if max_tickers:
            universe = universe[:max_tickers]
        _scan_status["total"] = len(universe)

        bench = kell._benchmark_closes("SPY")
        matches = []

        for i in range(0, len(universe), batch_size):
            batch = universe[i:i + batch_size]
            try:
                data = yf.download(batch, period="1y", interval="1d",
                                   group_by="ticker", threads=True,
                                   progress=False, auto_adjust=True)
            except Exception:
                data = None

            single = len(batch) == 1
            for t in batch:
                if data is not None:
                    closes, volumes = _extract(data, t, single)
                    if closes and len(closes) >= 50:
                        try:
                            res = kell.analyze_series(closes, volumes, bench)
                            res["ticker"] = t
                            price = res.get("price") or 0
                            avgvol = (sum(volumes[-50:]) / 50.0
                                      if volumes and len(volumes) >= 50 else 0)
                            res["dollar_vol"] = round(price * avgvol)
                            if (price >= MIN_PRICE
                                    and res["dollar_vol"] >= MIN_DOLLAR_VOL
                                    and _is_candidate(res)):
                                matches.append(res)
                        except Exception:
                            pass
                _scan_status["scanned"] += 1
            _scan_status["matches"] = len(matches)
            time.sleep(0.3)   # be polite to the data source between batches

        matches.sort(key=lambda r: r.get("rs_3m_vs_spy") or 0, reverse=True)
        matches = matches[:MAX_RESULTS]

        # Enrich the survivors with company name / sector / market cap / thesis
        _scan_status["stage"] = "enriching"
        for r in matches:
            kell.enrich(r)

        payload = {
            "results": matches,
            "scanned": _scan_status["scanned"],
            "universe": len(universe),
            "generated": datetime.now().isoformat(),
        }
        try:
            with open(SCAN_FILE, "w") as f:
                json.dump(payload, f)
        except Exception:
            pass
        _scan_status.update(state="done", finished=datetime.now().isoformat())
    except Exception as e:
        _scan_status.update(state="error", error=str(e)[:200],
                            finished=datetime.now().isoformat())
    return dict(_scan_status)


def start_scan_async(max_tickers=None):
    """Kick off a scan in a background thread (no-op if one is already running)."""
    if _scan_status["state"] == "running":
        return dict(_scan_status)
    threading.Thread(target=run_scan, kwargs={"max_tickers": max_tickers},
                     daemon=True).start()
    return {"state": "running", "started": datetime.now().isoformat()}


def scan_status():
    return dict(_scan_status)


def load_scan():
    """Return the most recent cached scan results, or None."""
    if os.path.exists(SCAN_FILE):
        try:
            with open(SCAN_FILE) as f:
                return json.load(f)
        except Exception:
            return None
    return None


if __name__ == "__main__":
    import sys
    cap = int(sys.argv[1]) if len(sys.argv) > 1 else None
    print(f"Scanning {'whole market' if not cap else f'{cap} names'}…")
    run_scan(max_tickers=cap)
    s = scan_status()
    print(f"State={s['state']} scanned={s['scanned']} matches={s['matches']}")
    data = load_scan() or {"results": []}
    for r in data["results"][:25]:
        print(f"  {r['ticker']:6} {r['phase']:16} RS3M={r.get('rs_3m_vs_spy', 0):+.1%}  {r['signal']}")
