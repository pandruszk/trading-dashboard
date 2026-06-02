"""
Broker + shared config for the Kell trading system
==================================================
Centralizes Alpaca credentials/connection and the risk + strategy constants
used by both the dashboard (app.py) and the autopilot (autopilot.py), so there
is a single source of truth.
"""
import os

HOME = os.path.expanduser("~")
KEYS_FILE = os.path.join(HOME, ".alpaca_keys")
STATE_FILE = os.path.join(HOME, "Documents", "portfolio_state.json")
SCHEDULE_FILE = os.path.join(HOME, "Documents", "trading_schedule.json")
LOG_DIR = os.path.join(HOME, "Documents", "trading_logs")

# --- Risk parameters (shared with the dashboard) ---------------------------
TRAILING_STOP_PCT = 0.20
HARD_STOP_PCT = 0.25
PROFIT_TAKE_PCT = 0.50
MAX_SECTOR_WEIGHT = 0.45
MAX_PER_SECTOR = 3
INITIAL_CAPITAL = 100000     # Alpaca paper account starting equity

# --- Kell autopilot strategy ----------------------------------------------
TARGET_POSITIONS = 8         # concentrated book of the strongest setups
TRIM_FRACTION = 0.33         # fraction sold when trimming an exhaustion move


def load_keys():
    """Alpaca keys from environment first (deploy), then ~/.alpaca_keys."""
    key_id = os.environ.get("APCA_API_KEY_ID")
    secret = os.environ.get("APCA_API_SECRET_KEY")
    base_url = os.environ.get("APCA_API_BASE_URL")
    if key_id and secret and base_url:
        return {"APCA_API_KEY_ID": key_id,
                "APCA_API_SECRET_KEY": secret,
                "APCA_API_BASE_URL": base_url}
    keys = {}
    with open(KEYS_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                keys[k.strip()] = v.strip()
    return keys


def get_api():
    """An authenticated Alpaca REST client (alpaca-trade-api imported lazily)."""
    import alpaca_trade_api as tradeapi
    keys = load_keys()
    return tradeapi.REST(
        key_id=keys["APCA_API_KEY_ID"],
        secret_key=keys["APCA_API_SECRET_KEY"],
        base_url=keys["APCA_API_BASE_URL"],
        api_version="v2",
    )


def is_paper():
    """True if the configured endpoint is an Alpaca *paper* account."""
    try:
        return "paper" in load_keys().get("APCA_API_BASE_URL", "").lower()
    except Exception:
        return True   # fail safe: assume paper rather than risk live trading
