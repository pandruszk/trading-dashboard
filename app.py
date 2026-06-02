#!/usr/bin/env python3
"""
Trading Dashboard — Flask Backend
===================================
Serves a live web dashboard for the automated trading system.

Run locally:  python3 app.py
Deploy:       railway up  (or push to GitHub + connect Railway)

API Endpoints:
  GET /api/account   — Account summary (equity, cash, P&L)
  GET /api/positions — Current holdings with live prices + risk data
  GET /api/trades    — Trade history from portfolio_state.json
  GET /api/risk      — Stop-loss status, sector concentration, alerts
  GET /api/history   — Portfolio value history (parsed from logs)
  GET /api/status    — System status (schedule, market open, etc.)
"""

import os
import json
import re
import glob
import secrets
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, jsonify, request, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash

import alpaca_trade_api as tradeapi
import yfinance as yf

import kell
import kell_scan

# ============================================================
# CONFIG
# ============================================================
HOME = os.path.expanduser("~")
KEYS_FILE = os.path.join(HOME, ".alpaca_keys")
STATE_FILE = os.path.join(HOME, "Documents", "portfolio_state.json")
SCHEDULE_FILE = os.path.join(HOME, "Documents", "trading_schedule.json")
LOG_DIR = os.path.join(HOME, "Documents", "trading_logs")

TRAILING_STOP_PCT = 0.20
HARD_STOP_PCT = 0.25
PROFIT_TAKE_PCT = 0.50
MAX_SECTOR_WEIGHT = 0.45
MAX_PER_SECTOR = 3
INITIAL_CAPITAL = 100000  # Alpaca paper account starting equity

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

DASHBOARD_PASSWORD_HASH = generate_password_hash("M0n3yM@ch1n3", method="pbkdf2")


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("authenticated"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "unauthorized"}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

# ============================================================
# HELPERS
# ============================================================
def load_keys():
    # Environment variables first (for deployment), then file
    key_id = os.environ.get("APCA_API_KEY_ID")
    secret = os.environ.get("APCA_API_SECRET_KEY")
    base_url = os.environ.get("APCA_API_BASE_URL")

    if key_id and secret and base_url:
        return {"APCA_API_KEY_ID": key_id, "APCA_API_SECRET_KEY": secret, "APCA_API_BASE_URL": base_url}

    keys = {}
    with open(KEYS_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                keys[k.strip()] = v.strip()
    return keys


def get_api():
    keys = load_keys()
    return tradeapi.REST(
        key_id=keys["APCA_API_KEY_ID"],
        secret_key=keys["APCA_API_SECRET_KEY"],
        base_url=keys["APCA_API_BASE_URL"],
        api_version="v2",
    )


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return None


def load_schedule():
    if os.path.exists(SCHEDULE_FILE):
        with open(SCHEDULE_FILE, "r") as f:
            return json.load(f)
    return {"last_daily": None, "last_monthly": None}


def get_sector_map(tickers):
    """Fetch sector info from yfinance for given tickers."""
    sectors = {}
    for t in tickers:
        try:
            info = yf.Ticker(t).info
            sectors[t] = info.get("sector", "Unknown")
        except Exception:
            sectors[t] = "Unknown"
    return sectors


# Cache sectors for 1 hour to avoid hammering yfinance
_sector_cache = {}
_sector_cache_time = None


def get_cached_sectors(tickers):
    global _sector_cache, _sector_cache_time
    now = datetime.now()
    if _sector_cache_time and (now - _sector_cache_time).seconds < 3600:
        # Return cached, but fetch any missing tickers
        missing = [t for t in tickers if t not in _sector_cache]
        if missing:
            _sector_cache.update(get_sector_map(missing))
        return _sector_cache

    _sector_cache = get_sector_map(tickers)
    _sector_cache_time = now
    return _sector_cache


# ============================================================
# ROUTES
# ============================================================
@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("authenticated"):
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        if check_password_hash(DASHBOARD_PASSWORD_HASH, request.form.get("password", "")):
            session["authenticated"] = True
            return redirect(url_for("index"))
        error = "Wrong password"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    return render_template("index.html")


@app.route("/api/account")
@login_required
def api_account():
    try:
        api = get_api()
        account = api.get_account()
        equity = float(account.equity)
        last_equity = float(account.last_equity)
        cash = float(account.cash)

        clock = api.get_clock()

        return jsonify({
            "equity": equity,
            "cash": cash,
            "buying_power": float(account.buying_power),
            "last_equity": last_equity,
            "today_pl": equity - last_equity,
            "today_pl_pct": (equity - last_equity) / last_equity if last_equity > 0 else 0,
            "total_pl": equity - INITIAL_CAPITAL,
            "total_pl_pct": (equity - INITIAL_CAPITAL) / INITIAL_CAPITAL,
            "initial_capital": INITIAL_CAPITAL,
            "market_open": clock.is_open,
            "next_open": clock.next_open.isoformat() if not clock.is_open else None,
            "next_close": clock.next_close.isoformat() if clock.is_open else None,
            "timestamp": datetime.now().isoformat(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/positions")
@login_required
def api_positions():
    try:
        api = get_api()
        positions = api.list_positions()
        state = load_state()

        if not positions:
            return jsonify({"positions": [], "total_value": 0})

        tickers = [p.symbol for p in positions]
        sectors = get_cached_sectors(tickers)

        total_mkt_value = sum(float(p.market_value) for p in positions)
        account = api.get_account()
        equity = float(account.equity)

        result = []
        for p in positions:
            ticker = p.symbol
            shares = int(float(p.qty))
            entry = float(p.avg_entry_price)
            current = float(p.current_price)
            mkt_val = float(p.market_value)
            pnl = float(p.unrealized_pl)
            pnl_pct = float(p.unrealized_plpc)
            weight = mkt_val / equity if equity > 0 else 0

            # Get HWM and stop levels from state
            hwm = entry
            if state and state.get("high_water_marks"):
                hwm = state["high_water_marks"].get(ticker, entry)
            hwm = max(hwm, current)

            trail_stop = hwm * (1 - TRAILING_STOP_PCT)
            hard_stop = entry * (1 - HARD_STOP_PCT)
            effective_stop = max(trail_stop, hard_stop)
            distance_to_stop = (current - effective_stop) / current if current > 0 else 0

            # Target weight from state
            target_weight = 1.0 / 8
            if state and state.get("positions", {}).get(ticker):
                target_weight = state["positions"][ticker].get("target_weight", target_weight)

            result.append({
                "ticker": ticker,
                "shares": shares,
                "entry_price": entry,
                "current_price": current,
                "market_value": mkt_val,
                "weight": weight,
                "target_weight": target_weight,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "sector": sectors.get(ticker, "Unknown"),
                "high_water_mark": hwm,
                "trailing_stop": trail_stop,
                "hard_stop": hard_stop,
                "effective_stop": effective_stop,
                "distance_to_stop": distance_to_stop,
                "entry_date": state["positions"][ticker].get("entry_date") if state and state.get("positions", {}).get(ticker) else None,
            })

        result.sort(key=lambda x: -x["market_value"])

        return jsonify({
            "positions": result,
            "total_value": total_mkt_value,
            "equity": equity,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/trades")
@login_required
def api_trades():
    try:
        state = load_state()
        if not state or not state.get("trades"):
            return jsonify({"trades": []})

        trades = []
        for t in reversed(state["trades"]):
            trades.append({
                "date": t.get("date", ""),
                "action": t.get("action", ""),
                "ticker": t.get("ticker", ""),
                "shares": t.get("shares", 0),
                "price": t.get("price", 0),
                "value": t.get("value", 0),
                "reason": t.get("reason", ""),
            })

        return jsonify({"trades": trades})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/risk")
@login_required
def api_risk():
    try:
        api = get_api()
        positions = api.list_positions()
        state = load_state()

        if not positions:
            return jsonify({
                "stops": [],
                "sectors": [],
                "alerts": [],
            })

        tickers = [p.symbol for p in positions]
        sectors = get_cached_sectors(tickers)

        account = api.get_account()
        equity = float(account.equity)

        # Open stop orders from Alpaca
        open_orders = api.list_orders(status="open")
        stop_orders = {o.symbol: o for o in open_orders if o.type in ("stop", "trailing_stop")}

        # Build stop-loss status
        stops = []
        alerts = []
        for p in positions:
            ticker = p.symbol
            current = float(p.current_price)
            entry = float(p.avg_entry_price)
            shares = int(float(p.qty))

            hwm = entry
            if state and state.get("high_water_marks"):
                hwm = state["high_water_marks"].get(ticker, entry)
            hwm = max(hwm, current)

            trail_stop = hwm * (1 - TRAILING_STOP_PCT)
            hard_stop = entry * (1 - HARD_STOP_PCT)
            effective_stop = max(trail_stop, hard_stop)
            dd_from_peak = (hwm - current) / hwm if hwm > 0 else 0
            distance = (current - effective_stop) / current if current > 0 else 0

            # Status: green/yellow/red
            if dd_from_peak >= TRAILING_STOP_PCT:
                status = "red"
                alerts.append(f"TRAILING STOP TRIGGERED: {ticker} is {dd_from_peak:.1%} below peak")
            elif dd_from_peak >= 0.15:
                status = "yellow"
                alerts.append(f"WARNING: {ticker} is {dd_from_peak:.1%} below peak (stop at {TRAILING_STOP_PCT:.0%})")
            else:
                status = "green"

            # Check for profit take
            gain = (current - entry) / entry if entry > 0 else 0
            if gain >= PROFIT_TAKE_PCT:
                profit_taken = False
                if state and state.get("positions", {}).get(ticker):
                    profit_taken = state["positions"][ticker].get("profit_taken", False)
                if not profit_taken:
                    alerts.append(f"PROFIT TAKE: {ticker} is up {gain:.1%} — eligible for trim")

            # Check server-side stop
            has_server_stop = ticker in stop_orders
            server_stop_price = float(stop_orders[ticker].stop_price) if has_server_stop else None
            if not has_server_stop:
                alerts.append(f"MISSING STOP: {ticker} has no server-side stop order")

            stops.append({
                "ticker": ticker,
                "current": current,
                "high_water_mark": hwm,
                "trailing_stop": trail_stop,
                "hard_stop": hard_stop,
                "effective_stop": effective_stop,
                "dd_from_peak": dd_from_peak,
                "distance_to_stop": distance,
                "status": status,
                "has_server_stop": has_server_stop,
                "server_stop_price": server_stop_price,
            })

        # Sector concentration
        sector_data = {}
        for p in positions:
            ticker = p.symbol
            mkt_val = float(p.market_value)
            sector = sectors.get(ticker, "Unknown")
            if sector not in sector_data:
                sector_data[sector] = {"value": 0, "count": 0, "tickers": []}
            sector_data[sector]["value"] += mkt_val
            sector_data[sector]["count"] += 1
            sector_data[sector]["tickers"].append(ticker)

        sector_list = []
        for sector, data in sorted(sector_data.items(), key=lambda x: -x[1]["value"]):
            weight = data["value"] / equity if equity > 0 else 0
            over_weight = weight > MAX_SECTOR_WEIGHT
            over_count = data["count"] > MAX_PER_SECTOR
            if over_weight:
                alerts.append(f"SECTOR OVERWEIGHT: {sector} at {weight:.1%} (limit {MAX_SECTOR_WEIGHT:.0%})")
            if over_count:
                alerts.append(f"SECTOR OVER-COUNT: {sector} has {data['count']} positions (limit {MAX_PER_SECTOR})")
            sector_list.append({
                "sector": sector,
                "value": data["value"],
                "weight": weight,
                "count": data["count"],
                "tickers": data["tickers"],
                "over_weight": over_weight,
                "over_count": over_count,
                "limit": MAX_SECTOR_WEIGHT,
            })

        return jsonify({
            "stops": stops,
            "sectors": sector_list,
            "alerts": alerts,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/history")
@login_required
def api_history():
    """Parse equity values from autopilot log files."""
    try:
        history = []

        # Parse all log files for equity values
        log_pattern = os.path.join(LOG_DIR, "autopilot_*.log")
        log_files = sorted(glob.glob(log_pattern))

        for log_file in log_files:
            # Extract date from filename
            basename = os.path.basename(log_file)
            match = re.search(r"autopilot_(\d{4}-\d{2}-\d{2})\.log", basename)
            if not match:
                continue
            date_str = match.group(1)

            with open(log_file, "r") as f:
                content = f.read()

            # Look for equity values in log
            equity_matches = re.findall(r"Equity:\s+\$\s*([\d,]+\.\d+)", content)
            if equity_matches:
                # Take the last equity value from the day's log
                equity_str = equity_matches[-1].replace(",", "")
                history.append({
                    "date": date_str,
                    "equity": float(equity_str),
                })

            # Also look for "Account equity: $X" format
            acct_matches = re.findall(r"Account equity: \$([\d,]+\.\d+)", content)
            if acct_matches and not equity_matches:
                equity_str = acct_matches[-1].replace(",", "")
                history.append({
                    "date": date_str,
                    "equity": float(equity_str),
                })

        # Add initial capital as starting point if we have state
        state = load_state()
        if state and state.get("created"):
            created_date = state["created"][:10]
            if not any(h["date"] == created_date for h in history):
                history.insert(0, {
                    "date": created_date,
                    "equity": INITIAL_CAPITAL,
                })

        # Add current equity
        try:
            api = get_api()
            account = api.get_account()
            today = datetime.now().strftime("%Y-%m-%d")
            # Replace or add today's entry
            history = [h for h in history if h["date"] != today]
            history.append({
                "date": today,
                "equity": float(account.equity),
            })
        except Exception:
            pass

        history.sort(key=lambda x: x["date"])

        return jsonify({
            "history": history,
            "initial_capital": INITIAL_CAPITAL,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/status")
@login_required
def api_status():
    try:
        api = get_api()
        state = load_state()
        schedule = load_schedule()
        clock = api.get_clock()

        # Last rebalance info
        last_rebalance = None
        next_rebalance = None
        days_since_rebalance = None
        if state and state.get("last_rebalance"):
            last_reb = datetime.fromisoformat(state["last_rebalance"])
            last_rebalance = last_reb.strftime("%Y-%m-%d %H:%M")
            days_since_rebalance = (datetime.now() - last_reb).days
            next_reb = last_reb + timedelta(days=30)
            next_rebalance = next_reb.strftime("%Y-%m-%d")

        # Open orders (stop orders)
        open_orders = api.list_orders(status="open")
        stop_count = len([o for o in open_orders if o.type in ("stop", "trailing_stop")])
        positions = api.list_positions()

        # Log file info
        latest_log = None
        log_files = sorted(glob.glob(os.path.join(LOG_DIR, "autopilot_*.log")))
        if log_files:
            latest = log_files[-1]
            mtime = os.path.getmtime(latest)
            latest_log = {
                "file": os.path.basename(latest),
                "modified": datetime.fromtimestamp(mtime).isoformat(),
            }

        return jsonify({
            "market_open": clock.is_open,
            "next_open": clock.next_open.isoformat() if not clock.is_open else None,
            "next_close": clock.next_close.isoformat() if clock.is_open else None,
            "last_daily": schedule.get("last_daily"),
            "last_monthly": schedule.get("last_monthly"),
            "last_rebalance": last_rebalance,
            "next_rebalance": next_rebalance,
            "days_since_rebalance": days_since_rebalance,
            "rebalance_overdue": days_since_rebalance > 30 if days_since_rebalance is not None else False,
            "stop_orders": stop_count,
            "total_positions": len(positions),
            "stops_coverage": f"{stop_count}/{len(positions)}",
            "latest_log": latest_log,
            "portfolio_created": state.get("created") if state else None,
            "timestamp": datetime.now().isoformat(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/kell")
@login_required
def api_kell():
    """Kell Cycle analysis for current holdings plus any ?tickers= watchlist.

    Implements Oliver Kell's "Cycle of Price Action": classifies each name's
    phase (reversal / crossback / base & break / uptrend / exhaustion / wedge
    drop) from its 10-21 EMA and 50-200 SMA structure and emits a Kell-style
    buy / hold / trim / exit signal. See kell.py and KELL.md.
    """
    try:
        tickers = []
        try:
            api = get_api()
            tickers = [p.symbol for p in api.list_positions()]
        except Exception:
            tickers = []

        # Optional watchlist passed from the dashboard, e.g. ?tickers=NVDA,AAPL
        extra = request.args.get("tickers", "")
        if extra:
            tickers += [t for t in re.split(r"[,\s]+", extra) if t]

        results = kell.analyze_tickers(tickers)
        return jsonify({
            "results": results,
            "params": {
                "ema_fast": kell.EMA_FAST,
                "ema_slow": kell.EMA_SLOW,
                "sma_trend": kell.SMA_TREND,
                "sma_long": kell.SMA_LONG,
            },
            "timestamp": datetime.now().isoformat(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/kell/scan")
@login_required
def api_kell_scan():
    """Return the latest cached full-market Kell screen + live scan status."""
    data = kell_scan.load_scan() or {"results": [], "scanned": 0, "universe": 0,
                                      "generated": None}
    data["status"] = kell_scan.scan_status()
    return jsonify(data)


@app.route("/api/kell/scan/run", methods=["POST"])
@login_required
def api_kell_scan_run():
    """Kick off a background full-market scan (optional ?max=N to cap the universe)."""
    max_tickers = request.args.get("max", type=int)
    return jsonify(kell_scan.start_scan_async(max_tickers))


# ============================================================
# MAIN
# ============================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
