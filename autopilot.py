#!/usr/bin/env python3
"""
Kell Autopilot — automated Kell-Cycle trading on Alpaca
=======================================================
Closes out existing positions and runs the portfolio on autopilot using Oliver
Kell's methodology: it builds a concentrated book of the strongest Kell buy
setups from the whole-market screener, manages them with Kell sell rules (exit
when a name loses its moving averages, trim into exhaustion extensions), and
writes the state + logs the dashboard reads.

SAFETY
------
* Trades whatever account APCA_API_BASE_URL points to. It REFUSES to trade a
  non-paper (live) account unless KELL_ALLOW_LIVE=yes is set.
* --dry-run prints intended orders without sending any.
* Educational tool for a paper account. Automated trading carries real
  financial risk if pointed at a live account. Not financial advice.

USAGE
-----
  python3 autopilot.py status                 account + positions + schedule
  python3 autopilot.py close-all [--dry-run]  liquidate all positions & orders
  python3 autopilot.py rebalance [--dry-run]  build/refresh the Kell book now
  python3 autopilot.py daily     [--dry-run]  daily maintenance (sells/trims)
  python3 autopilot.py run [--reset] [--dry-run]
                                              go on autopilot (schedule loop);
                                              first run (or --reset) closes all
                                              positions then builds the book.
"""
import os
import sys
import json
import time
import argparse
from datetime import datetime

import broker
import kell
import kell_scan

REBALANCE_DAYS = 30
SCAN_MAX_AGE = 6 * 3600          # reuse a cached market screen younger than this
SELL_PHASES = ("Wedge Drop / Downtrend", "Reversal Extension")


# ===========================================================================
# Logging + state (writes the files the dashboard reads)
# ===========================================================================
def log(msg):
    os.makedirs(broker.LOG_DIR, exist_ok=True)
    line = f"{datetime.now().isoformat(timespec='seconds')}  {msg}"
    print(line, flush=True)
    try:
        fn = os.path.join(broker.LOG_DIR, f"autopilot_{datetime.now():%Y-%m-%d}.log")
        with open(fn, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def load_state():
    return _load_json(broker.STATE_FILE, {})


def save_state(state):
    _save_json(broker.STATE_FILE, state)


def load_schedule():
    return _load_json(broker.SCHEDULE_FILE, {"last_daily": None, "last_monthly": None})


def save_schedule(s):
    _save_json(broker.SCHEDULE_FILE, s)


def record_trade(state, action, ticker, shares, price, reason):
    state.setdefault("trades", []).append({
        "date": datetime.now().isoformat(timespec="seconds"),
        "action": action,
        "ticker": ticker,
        "shares": shares,
        "price": price,
        "value": round((shares or 0) * (price or 0), 2),
        "reason": reason,
    })


# ===========================================================================
# Safety
# ===========================================================================
def guard_live():
    """Allow paper freely; require explicit opt-in for live trading."""
    if broker.is_paper():
        return "PAPER"
    if os.environ.get("KELL_ALLOW_LIVE", "").lower() in ("1", "yes", "true"):
        return "LIVE"
    log("REFUSING to trade: APCA_API_BASE_URL is not a paper endpoint and "
        "KELL_ALLOW_LIVE is not set. Aborting for safety.")
    sys.exit(2)


# ===========================================================================
# Pure decision logic (unit-tested, no broker calls)
# ===========================================================================
def sell_decision(phase):
    """Kell management rule for a held name, from its current cycle phase."""
    if phase in SELL_PHASES:
        return "EXIT"            # lost the moving averages -> out
    if phase == "Exhaustion Extension":
        return "TRIM"            # sell into strength
    return "HOLD"


def select_target(candidates, n=None, max_per_sector=None):
    """Pick the target book from RS-ranked screener candidates, capping the
    number of names per sector (candidates: dicts with 'ticker'/'sector')."""
    n = n or broker.TARGET_POSITIONS
    max_per_sector = max_per_sector or broker.MAX_PER_SECTOR
    chosen, by_sector = [], {}
    for c in candidates:
        if len(chosen) >= n:
            break
        sec = c.get("sector") or "Unknown"
        if by_sector.get(sec, 0) >= max_per_sector:
            continue
        chosen.append(c)
        by_sector[sec] = by_sector.get(sec, 0) + 1
    return chosen


def plan_rebalance(held, target):
    """Names to sell (held, not in target) and to buy (target, not held)."""
    held = set(held)
    target_syms = [c["ticker"] for c in target]
    sells = [s for s in held if s not in target_syms]
    buys = [t for t in target_syms if t not in held]
    return sells, buys


# ===========================================================================
# Execution
# ===========================================================================
def _last_price(api, symbol):
    try:
        return float(api.get_latest_trade(symbol).price)
    except Exception:
        try:
            return float(api.get_position(symbol).current_price)
        except Exception:
            return None


def close_all(api, dry_run=False):
    guard_live()
    state = load_state()
    try:
        positions = api.list_positions()
    except Exception as e:
        log(f"close-all: could not list positions: {e}")
        return
    if not positions:
        log("close-all: no open positions.")
    for p in positions:
        sym, qty, px = p.symbol, abs(int(float(p.qty))), float(p.current_price)
        if dry_run:
            log(f"[dry-run] would CLOSE {sym} ({qty} sh @ ~${px})")
            continue
        try:
            api.close_position(sym)
            record_trade(state, "SELL", sym, qty, px, "autopilot close-all")
            log(f"CLOSED {sym} ({qty} sh @ ~${px})")
        except Exception as e:
            log(f"close {sym} failed: {e}")
    if not dry_run:
        try:
            api.cancel_all_orders()
        except Exception:
            pass
        save_state(state)


def rebalance(api, dry_run=False):
    guard = guard_live()
    log(f"REBALANCE start ({guard}{' / dry-run' if dry_run else ''})")

    # 1) candidates from the screener (reuse a recent scan, else run one)
    scan = kell_scan.load_scan()
    fresh = False
    if scan and scan.get("generated"):
        try:
            age = (datetime.now() - datetime.fromisoformat(scan["generated"])).total_seconds()
            fresh = age < SCAN_MAX_AGE
        except Exception:
            fresh = False
    if not fresh:
        log("running a fresh Kell market scan…")
        kell_scan.run_scan()
        scan = kell_scan.load_scan() or {"results": []}
    candidates = scan.get("results", [])
    log(f"{len(candidates)} buy candidates from the screen")
    if not candidates:
        log("no candidates found — skipping rebalance (no buys, no forced sells).")
        return

    target = select_target(candidates)
    target_syms = [c["ticker"] for c in target]
    log(f"target book ({len(target_syms)}): {', '.join(target_syms)}")

    try:
        held = [p.symbol for p in api.list_positions()]
        equity = float(api.get_account().equity)
    except Exception as e:
        log(f"rebalance: account/positions error: {e}")
        return

    sells, buys = plan_rebalance(held, target)
    state = load_state()

    for s in sells:
        if dry_run:
            log(f"[dry-run] would SELL {s} (dropped from target)")
            continue
        try:
            px = _last_price(api, s)
            api.close_position(s)
            record_trade(state, "SELL", s, None, px, "rebalance: dropped from target")
            log(f"SELL {s} (dropped from target)")
        except Exception as e:
            log(f"sell {s} failed: {e}")

    per_name = equity / broker.TARGET_POSITIONS   # fixed 1/N -> raises cash in weak markets
    for b in buys:
        if dry_run:
            log(f"[dry-run] would BUY ${per_name:,.0f} of {b}")
            continue
        try:
            api.submit_order(symbol=b, notional=round(per_name, 2),
                             side="buy", type="market", time_in_force="day")
            px = _last_price(api, b)
            record_trade(state, "BUY", b, None, px, "rebalance: new Kell setup")
            log(f"BUY ${per_name:,.0f} of {b}")
        except Exception as e:
            log(f"buy {b} failed: {e}")

    if not dry_run:
        _persist(api, state, rebalanced=True)
    log("REBALANCE done")


def run_daily(api, dry_run=False):
    guard = guard_live()
    log(f"DAILY start ({guard}{' / dry-run' if dry_run else ''})")
    try:
        positions = api.list_positions()
    except Exception as e:
        log(f"daily: positions error: {e}")
        return
    if not positions:
        log("daily: no positions to manage.")
        if not dry_run:
            _persist(api, load_state())
        return

    bench = kell._benchmark_closes("SPY")
    state = load_state()
    for p in positions:
        sym, qty, px = p.symbol, abs(int(float(p.qty))), float(p.current_price)
        try:
            closes, vols = kell._fetch_closes(sym)
            res = kell.analyze_series(closes, vols, bench) if closes else {}
        except Exception:
            res = {}
        phase = res.get("phase", "?")
        action = sell_decision(phase)

        if action == "EXIT":
            if dry_run:
                log(f"[dry-run] {sym}: {phase} -> EXIT")
            else:
                try:
                    api.close_position(sym)
                    record_trade(state, "SELL", sym, qty, px, f"Kell exit ({phase})")
                    log(f"EXIT {sym} ({phase})")
                except Exception as e:
                    log(f"exit {sym} failed: {e}")
        elif action == "TRIM":
            trim_qty = max(1, int(qty * broker.TRIM_FRACTION))
            if dry_run:
                log(f"[dry-run] {sym}: {phase} -> TRIM {trim_qty} sh")
            else:
                try:
                    api.submit_order(symbol=sym, qty=trim_qty, side="sell",
                                     type="market", time_in_force="day")
                    record_trade(state, "TRIM", sym, trim_qty, px, f"Kell trim ({phase})")
                    log(f"TRIM {sym} {trim_qty} sh ({phase})")
                except Exception as e:
                    log(f"trim {sym} failed: {e}")
        else:
            log(f"HOLD {sym} ({phase})")

    if not dry_run:
        _persist(api, state)
    log("DAILY done")


def _persist(api, state, rebalanced=False):
    """Write portfolio_state.json + schedule + an equity log line so the
    dashboard's holdings/history/status panels stay in sync."""
    try:
        equity = float(api.get_account().equity)
        positions = api.list_positions()
    except Exception as e:
        log(f"persist: account error: {e}")
        return

    state.setdefault("created", datetime.now().isoformat())
    if rebalanced:
        state["last_rebalance"] = datetime.now().isoformat()

    pos_meta = state.setdefault("positions", {})
    hwm = state.setdefault("high_water_marks", {})
    held = []
    for p in positions:
        sym, cur = p.symbol, float(p.current_price)
        held.append(sym)
        hwm[sym] = max(hwm.get(sym, 0), cur, float(p.avg_entry_price))
        meta = pos_meta.setdefault(sym, {})
        meta.setdefault("entry_date", datetime.now().isoformat()[:10])
        meta["target_weight"] = 1.0 / broker.TARGET_POSITIONS
    for sym in list(pos_meta):
        if sym not in held:
            pos_meta.pop(sym, None)
    save_state(state)

    sched = load_schedule()
    today = datetime.now().isoformat()[:10]
    sched["last_daily"] = today
    if rebalanced:
        sched["last_monthly"] = today
    save_schedule(sched)

    log(f"Equity: $ {equity:,.2f}")   # parsed by the dashboard's history endpoint


# ===========================================================================
# Scheduler loop
# ===========================================================================
def _market_open(api):
    try:
        return bool(api.get_clock().is_open)
    except Exception:
        return False


def loop(api, reset=False, dry_run=False, poll=1800):
    guard_live()
    state = load_state()
    initialized = bool(state.get("created"))
    if reset or not initialized:
        log("initializing: closing existing positions, then building the Kell book")
        close_all(api, dry_run=dry_run)
        rebalance(api, dry_run=dry_run)

    log(f"AUTOPILOT running — checking every {poll // 60} min (Ctrl+C to stop)")
    while True:
        try:
            today = datetime.now().isoformat()[:10]
            sched = load_schedule()
            state = load_state()
            open_now = _market_open(api)

            if open_now and sched.get("last_daily") != today:
                run_daily(api, dry_run=dry_run)

            last_reb = state.get("last_rebalance")
            due = True
            if last_reb:
                try:
                    due = (datetime.now() - datetime.fromisoformat(last_reb)).days >= REBALANCE_DAYS
                except Exception:
                    due = True
            if open_now and due:
                rebalance(api, dry_run=dry_run)
        except Exception as e:
            log(f"loop error: {e}")
        time.sleep(poll)


def status(api):
    try:
        acct = api.get_account()
        mode = "PAPER" if broker.is_paper() else "LIVE"
        print(f"Account [{mode}]  equity=${float(acct.equity):,.2f}  cash=${float(acct.cash):,.2f}")
        positions = api.list_positions()
        print(f"Positions ({len(positions)}):")
        for p in positions:
            print(f"  {p.symbol:6} {int(float(p.qty)):>6} sh  "
                  f"${float(p.market_value):>12,.0f}  P&L {float(p.unrealized_plpc) * 100:+6.1f}%")
        sched = load_schedule()
        print(f"Schedule: last_daily={sched.get('last_daily')}  last_monthly={sched.get('last_monthly')}")
    except Exception as e:
        print(f"status error: {e}")


def main():
    ap = argparse.ArgumentParser(description="Kell Autopilot")
    ap.add_argument("command", choices=["status", "close-all", "rebalance", "daily", "run"])
    ap.add_argument("--dry-run", action="store_true", help="log intended orders, send none")
    ap.add_argument("--reset", action="store_true", help="(run) close all + rebuild first")
    args = ap.parse_args()

    try:
        api = broker.get_api()
    except Exception as e:
        print(f"Could not connect to Alpaca: {e}")
        print("Set APCA_API_KEY_ID / APCA_API_SECRET_KEY / APCA_API_BASE_URL "
              "or create ~/.alpaca_keys.")
        sys.exit(1)

    if args.command == "status":
        status(api)
    elif args.command == "close-all":
        close_all(api, dry_run=args.dry_run)
    elif args.command == "rebalance":
        rebalance(api, dry_run=args.dry_run)
    elif args.command == "daily":
        run_daily(api, dry_run=args.dry_run)
    elif args.command == "run":
        loop(api, reset=args.reset, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
