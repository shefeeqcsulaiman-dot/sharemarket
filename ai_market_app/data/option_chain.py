"""
Option chain data: live from NSE India API, with a modelled fallback.
"""

import logging
import random

import pandas as pd

from data.nse_session import fetch_option_chain as _nse_chain
import data.upstox as _upstox

log = logging.getLogger(__name__)


# ── NSE live option chain ────────────────────────────────────────────────────
def _parse_nse_chain(raw: dict, spot_price: float, expiry: str | None) -> pd.DataFrame:
    records     = raw.get("records", {})
    all_expiries = records.get("expiryDates", [])
    target_exp  = expiry or (all_expiries[0] if all_expiries else None)

    rows = []
    for entry in records.get("data", []):
        if target_exp and entry.get("expiryDate") != target_exp:
            continue
        strike = entry.get("strikePrice", 0)
        ce     = entry.get("CE", {})
        pe     = entry.get("PE", {})
        rows.append({
            "strike":          float(strike),
            "call_ltp":        float(ce.get("lastPrice",        0) or 0),
            "put_ltp":         float(pe.get("lastPrice",        0) or 0),
            "call_oi":         int(ce.get("openInterest",       0) or 0),
            "put_oi":          int(pe.get("openInterest",       0) or 0),
            "call_change_oi":  int(ce.get("changeinOpenInterest", 0) or 0),
            "put_change_oi":   int(pe.get("changeinOpenInterest", 0) or 0),
            "call_volume":     int(ce.get("totalTradedVolume",  0) or 0),
            "put_volume":      int(pe.get("totalTradedVolume",  0) or 0),
            "call_iv":         float(ce.get("impliedVolatility", 0) or 0),
            "put_iv":          float(pe.get("impliedVolatility", 0) or 0),
            "call_bid":        float(ce.get("bidprice",         0) or 0),
            "call_ask":        float(ce.get("askPrice",         0) or 0),
            "put_bid":         float(pe.get("bidprice",         0) or 0),
            "put_ask":         float(pe.get("askPrice",         0) or 0),
        })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df.attrs["source"]  = "nse_live"
    df.attrs["expiry"]  = target_exp or ""
    df.attrs["spot"]    = records.get("underlyingValue", spot_price)
    return df


# ── Modelled fallback ────────────────────────────────────────────────────────
def _modelled_chain(symbol: str, spot_price: float, expiry: str | None) -> pd.DataFrame:
    rng     = random.Random(f"{symbol}:{round(spot_price, 1)}:{expiry or 'nearest'}")
    step    = 5 if spot_price < 500 else (10 if spot_price < 2000 else 50)
    strikes = [round(spot_price + i * step, 2) for i in range(-6, 7)]
    rows    = []
    for strike in strikes:
        dist       = abs(strike - spot_price)
        call_oi    = max(0, int((strike - spot_price) * 35 + 1000))
        put_oi     = max(0, int((spot_price - strike) * 35 + 1000))
        call_ltp   = round(1.5 + dist * 0.05, 2)
        put_ltp    = round(1.2 + dist * 0.05, 2)
        spread     = lambda p: max(0.01, p * 0.006)
        rows.append({
            "strike":         strike,
            "call_ltp":       call_ltp,
            "put_ltp":        put_ltp,
            "call_oi":        call_oi,
            "put_oi":         put_oi,
            "call_change_oi": int(call_oi * rng.uniform(-0.05, 0.05)),
            "put_change_oi":  int(put_oi  * rng.uniform(-0.05, 0.05)),
            "call_volume":    max(500, int(call_oi * rng.uniform(0.7, 1.5))),
            "put_volume":     max(500, int(put_oi  * rng.uniform(0.7, 1.5))),
            "call_iv":        round(15 + dist * 0.5, 2),
            "put_iv":         round(16 + dist * 0.5, 2),
            "call_bid":       round(call_ltp - spread(call_ltp), 2),
            "call_ask":       round(call_ltp + spread(call_ltp), 2),
            "put_bid":        round(put_ltp  - spread(put_ltp),  2),
            "put_ask":        round(put_ltp  + spread(put_ltp),  2),
        })
    df = pd.DataFrame(rows)
    df.attrs["source"] = "model"
    df.attrs["expiry"] = expiry or "modelled"
    df.attrs["spot"]   = spot_price
    return df


# ── Public API ───────────────────────────────────────────────────────────────
def get_option_chain(symbol: str, spot_price: float, expiry: str | None = None) -> pd.DataFrame:
    """
    Return option chain DataFrame (one row per strike).
    Priority: Upstox live → NSE India live → modelled fallback.
    df.attrs['source'] is 'upstox', 'nse_live', or 'model'.
    """
    if spot_price <= 0:
        return pd.DataFrame()

    # 1. Upstox live option chain
    try:
        df = _upstox.get_option_chain(symbol, expiry)
        if not df.empty:
            log.debug("Option chain for %s: Upstox live (%d strikes)", symbol, len(df))
            return df
    except Exception as exc:
        log.debug("Upstox option chain failed for %s: %s", symbol, exc)

    # 2. NSE India live option chain
    try:
        raw = _nse_chain(symbol)
        if raw:
            df = _parse_nse_chain(raw, spot_price, expiry)
            if not df.empty:
                log.debug("Option chain for %s: NSE live (%d strikes)", symbol, len(df))
                return df
    except Exception as exc:
        log.debug("NSE option chain failed for %s: %s", symbol, exc)

    # 3. Modelled fallback
    log.debug("Option chain for %s: using modelled fallback", symbol)
    return _modelled_chain(symbol, spot_price, expiry)
