from datetime import datetime

DEFAULT_MARKET_SCORE = 50
SUPPORTED_TIMEFRAMES = ["5m", "15m", "1h"]

API_CONFIG = {
    "dhan": {
        "base_url": "https://api.dhan.co/v1",
        "api_key_env": "DHAN_API_KEY",
        "api_secret_env": "DHAN_API_SECRET"
    },
    "upstox": {
        "base_url": "https://api.upstox.com/v2",
        "api_key_env": "UPSTOX_API_KEY",
        "api_secret_env": "UPSTOX_API_SECRET"
    }
}

DEFAULT_SYMBOLS = ["VEDL", "SUZLON"]

DISCLAIMER = (
    "This is a personal analytics tool. Signals are generated from rule-based and news scoring. "
    "They may be wrong. I am responsible for my own trades."
)

START_TIME = datetime.utcnow()
