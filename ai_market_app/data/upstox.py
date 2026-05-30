"""
Upstox v2 live data — quotes, candles, option chain.
Uses the official upstox-python-sdk with SSL verify disabled for Windows.

Env var required:  UPSTOX_ACCESS_TOKEN
"""

import logging
import os
import ssl
import urllib3
from datetime import datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

log = logging.getLogger(__name__)

_BASE    = "https://api.upstox.com/v2"
_NSE_TZ  = ZoneInfo("Asia/Kolkata")

# ── Instrument keys (NSE_EQ|ISIN) ────────────────────────────────────────────
INSTRUMENT_KEYS = {
    "NIFTY":     "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "VEDL":      "NSE_EQ|INE205A01025",
    "SUZLON":    "NSE_EQ|INE040H01021",
    "CDSL":      "NSE_EQ|INE736A01011",
    "KITEX":     "NSE_EQ|INE602G01020",
    "INOXWIND":  "NSE_EQ|INE066P01011",
    "KPIGREEN":  "NSE_EQ|INE542W01025",
}

# Upstox only supports 1minute and 30minute for intraday
# Map our app's timeframes → (upstox_interval, resample_rule)
_INTERVAL_MAP = {
    "5m":  ("1minute",  "5min"),
    "15m": ("1minute",  "15min"),
    "1h":  ("30minute", "1h"),
}


def _token() -> str | None:
    return os.getenv("UPSTOX_ACCESS_TOKEN")


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_token()}",
        "Accept": "application/json",
    }


def _get(path: str, params: dict | None = None) -> dict | None:
    if not _token():
        return None
    try:
        r = requests.get(
            f"{_BASE}{path}",
            params=params,
            headers=_headers(),
            timeout=10,
            verify=False,
        )
        if r.status_code == 200:
            data = r.json()
            if data.get("status") == "success":
                return data.get("data")
        log.debug("Upstox %s → %s %s", path, r.status_code, r.text[:200])
    except Exception as exc:
        log.debug("Upstox request failed: %s", exc)
    return None


# ── Quotes ────────────────────────────────────────────────────────────────────
def get_ltp(symbol: str) -> dict | None:
    """Return live last-traded-price dict or None."""
    ikey = INSTRUMENT_KEYS.get(symbol.upper())
    if not ikey:
        return None
    data = _get("/market-quote/ltp", params={"symbol": ikey})
    if not data:
        return None
    # key in response uses colon: "NSE_EQ:VEDL"
    key = ikey.replace("|", ":")
    row = data.get(key) or next(iter(data.values()), None)
    if not row:
        return None
    return {"ltp": float(row.get("last_price", 0)), "instrument_token": row.get("instrument_token")}


def get_full_quote(symbol: str) -> dict | None:
    """Return full OHLC + volume quote dict or None."""
    ikey = INSTRUMENT_KEYS.get(symbol.upper())
    if not ikey:
        return None
    data = _get("/market-quote/quotes", params={"symbol": ikey})
    if not data:
        return None
    key = ikey.replace("|", ":")
    row = data.get(key) or next(iter(data.values()), None)
    if not row:
        return None
    ohlc = row.get("ohlc", {})
    return {
        "symbol":         symbol.upper(),
        "ltp":            float(row.get("last_price",  ohlc.get("close", 0))),
        "open":           float(ohlc.get("open",  0)),
        "high":           float(ohlc.get("high",  0)),
        "low":            float(ohlc.get("low",   0)),
        "close":          float(ohlc.get("close", 0)),
        "volume":         int(row.get("volume", 0) or 0),
        "average_price":  float(row.get("average_price", 0) or 0),
        "oi":             int(row.get("oi", 0) or 0),
        "change":         float(row.get("net_change", 0) or 0),
        "change_pct":     round(
            (float(row.get("net_change", 0) or 0) / float(ohlc.get("close", 1)) * 100)
            if ohlc.get("close") else 0, 2
        ),
        "source": "upstox",
    }


# ── Historical candles ────────────────────────────────────────────────────────
def get_candles(symbol: str, timeframe: str = "5m", days: int = 7) -> pd.DataFrame:
    """
    Return OHLCV DataFrame resampled to requested timeframe.
    Columns: timestamp, open, high, low, close, volume
    """
    ikey = INSTRUMENT_KEYS.get(symbol.upper())
    if not ikey:
        return pd.DataFrame()

    upstox_interval, resample_rule = _INTERVAL_MAP.get(timeframe, ("1minute", "5min"))

    today   = datetime.now(_NSE_TZ).strftime("%Y-%m-%d")
    from_dt = (datetime.now(_NSE_TZ) - timedelta(days=days)).strftime("%Y-%m-%d")
    key_enc = quote(ikey, safe="")

    data = _get(f"/historical-candle/{key_enc}/{upstox_interval}/{today}/{from_dt}")
    if not data:
        return pd.DataFrame()

    raw = data.get("candles", [])
    if not raw:
        return pd.DataFrame()

    # Each candle: [timestamp_iso, open, high, low, close, volume, oi]
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume", "oi"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=False).dt.tz_convert(_NSE_TZ)
    df = df.sort_values("timestamp").reset_index(drop=True)
    df = df[["timestamp", "open", "high", "low", "close", "volume"]]
    df[["open","high","low","close"]] = df[["open","high","low","close"]].astype(float)
    df["volume"] = df["volume"].astype(int)

    # Resample to target timeframe if needed
    if resample_rule != "1min":
        df = df.set_index("timestamp")
        df = df.resample(resample_rule, label="left", closed="left").agg({
            "open":   "first",
            "high":   "max",
            "low":    "min",
            "close":  "last",
            "volume": "sum",
        }).dropna(subset=["open"]).reset_index()

    df.attrs["source"] = "upstox"
    return df.tail(120).reset_index(drop=True)


# ── Option chain ──────────────────────────────────────────────────────────────
def get_option_expiries(symbol: str) -> list[str]:
    """Return sorted list of unique expiry dates (YYYY-MM-DD) for symbol."""
    ikey = INSTRUMENT_KEYS.get(symbol.upper())
    if not ikey:
        return []
    data = _get("/option/contract", params={"instrument_key": ikey})
    if not data:
        return []
    expiries = sorted(set(c["expiry"] for c in data if c.get("expiry")))
    return expiries


def get_option_chain(symbol: str, expiry: str | None = None) -> pd.DataFrame:
    """
    Return live option chain DataFrame or empty DataFrame on failure.
    Columns: strike, call_ltp, put_ltp, call_oi, put_oi,
             call_change_oi, put_change_oi, call_volume, put_volume,
             call_iv, put_iv, call_bid, call_ask, put_bid, put_ask
    """
    ikey = INSTRUMENT_KEYS.get(symbol.upper())
    if not ikey:
        return pd.DataFrame()

    # Pick nearest expiry if not specified
    if not expiry:
        expiries = get_option_expiries(symbol)
        if not expiries:
            return pd.DataFrame()
        expiry = expiries[0]

    data = _get("/option/chain", params={"instrument_key": ikey, "expiry_date": expiry})
    if not data:
        return pd.DataFrame()

    rows = []
    for entry in data:
        strike = float(entry.get("strike_price", 0))
        ce = entry.get("call_options", {})
        pe = entry.get("put_options",  {})
        ce_mkt = ce.get("market_data", {})
        pe_mkt = pe.get("market_data", {})
        ce_oi  = ce.get("option_greeks", {})
        pe_oi  = pe.get("option_greeks", {})

        rows.append({
            "strike":          strike,
            "call_ltp":        float(ce_mkt.get("ltp",          0) or 0),
            "put_ltp":         float(pe_mkt.get("ltp",          0) or 0),
            "call_oi":         int(ce_mkt.get("oi",             0) or 0),
            "put_oi":          int(pe_mkt.get("oi",             0) or 0),
            "call_change_oi":  int(ce_mkt.get("change_oi",      0) or 0),
            "put_change_oi":   int(pe_mkt.get("change_oi",      0) or 0),
            "call_volume":     int(ce_mkt.get("volume",         0) or 0),
            "put_volume":      int(pe_mkt.get("volume",         0) or 0),
            "call_iv":         float(ce_oi.get("iv",            0) or 0),
            "put_iv":          float(pe_oi.get("iv",            0) or 0),
            "call_bid":        float(ce_mkt.get("bid_price",    0) or 0),
            "call_ask":        float(ce_mkt.get("ask_price",    0) or 0),
            "put_bid":         float(pe_mkt.get("bid_price",    0) or 0),
            "put_ask":         float(pe_mkt.get("ask_price",    0) or 0),
        })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df.attrs["source"] = "upstox"
    df.attrs["expiry"] = expiry
    return df
