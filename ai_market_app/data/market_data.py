"""
Market data: live quotes and OHLCV candles for NSE-listed stocks.

Priority order:
  Quote  : NSE India API → Yahoo Finance → sample fallback
  Candles: Yahoo Finance → sample fallback
"""

import logging
import os
import random
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from dotenv import load_dotenv

from data.nse_session import fetch_quote as _nse_quote
import data.upstox as _upstox

load_dotenv()
log = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
NSE_TZ   = ZoneInfo("Asia/Kolkata")
NSE_OPEN = time(9, 15)
NSE_CLOSE = time(15, 30)

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YAHOO_INTERVALS = {
    "5m":  ("5m",  "5d"),
    "15m": ("15m", "5d"),
    "1h":  ("60m", "1mo"),
}

_YAHOO_CACHE: dict[tuple, tuple[datetime, dict | None]] = {}
_CACHE_TTL = timedelta(seconds=30)

_SAMPLE_PRICE = {
    "NIFTY":    23500.0,
    "VEDL":       352.0,
    "SUZLON":      57.0,
    "CDSL":      1244.0,
    "KITEX":      161.0,
    "INOXWIND":   145.0,
    "KPIGREEN":   980.0,
}


# ── Helpers ──────────────────────────────────────────────────────────────────
def _market_state(ts: datetime | None = None) -> str:
    now = (ts or datetime.now(NSE_TZ)).astimezone(NSE_TZ)
    if now.weekday() >= 5:
        return "closed"
    if now.time() < NSE_OPEN:
        return "pre-open"
    if now.time() >= NSE_CLOSE:
        return "closed"
    return "open"


def _yahoo_symbol(sym: str) -> str:
    return sym if "." in sym else f"{sym}.NS"


def _prev_weekday(dt: datetime) -> datetime:
    while dt.weekday() >= 5:
        dt -= timedelta(days=1)
    return dt


def latest_market_timestamp(now: datetime | None = None) -> datetime:
    cur = (now or datetime.now(NSE_TZ)).astimezone(NSE_TZ)
    close_today = cur.replace(hour=NSE_CLOSE.hour, minute=NSE_CLOSE.minute, second=0, microsecond=0)
    open_today  = cur.replace(hour=NSE_OPEN.hour,  minute=NSE_OPEN.minute,  second=0, microsecond=0)
    if cur.weekday() >= 5:
        return _prev_weekday(close_today)
    if cur < open_today:
        return _prev_weekday(close_today - timedelta(days=1))
    if cur > close_today:
        return close_today
    return cur.replace(second=0, microsecond=0)


# ── Yahoo Finance ────────────────────────────────────────────────────────────
def _yahoo_chart(symbol: str, interval: str, range_: str) -> dict | None:
    key    = (_yahoo_symbol(symbol), interval, range_)
    cached = _YAHOO_CACHE.get(key)
    now    = datetime.now(NSE_TZ)
    if cached and (now - cached[0]) < _CACHE_TTL:
        return cached[1]

    try:
        resp = requests.get(
            YAHOO_CHART_URL.format(symbol=_yahoo_symbol(symbol)),
            params={"interval": interval, "range": range_, "includePrePost": "false"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8,
        )
        resp.raise_for_status()
        result = resp.json().get("chart", {}).get("result") or []
        chart  = result[0] if result else None
    except Exception as exc:
        log.debug("Yahoo chart fetch failed for %s: %s", symbol, exc)
        chart = None

    _YAHOO_CACHE[key] = (now, chart)
    return chart


def _quote_from_yahoo(symbol: str) -> dict | None:
    chart = _yahoo_chart(symbol, "1m", "1d")
    if not chart:
        return None
    try:
        meta    = chart.get("meta", {})
        q       = (chart.get("indicators", {}).get("quote") or [{}])[0]
        closes  = [v for v in q.get("close",  []) if v is not None]
        highs   = [v for v in q.get("high",   []) if v is not None]
        lows    = [v for v in q.get("low",    []) if v is not None]
        opens   = [v for v in q.get("open",   []) if v is not None]
        volumes = [v for v in q.get("volume", []) if v is not None]

        price  = meta.get("regularMarketPrice") or (closes[-1] if closes else None)
        if not price:
            return None

        prev   = meta.get("chartPreviousClose") or meta.get("previousClose") or price
        pct    = ((price - prev) / prev * 100) if prev else 0.0
        mt     = meta.get("regularMarketTime")
        ts     = (datetime.fromtimestamp(mt, tz=NSE_TZ) if mt else latest_market_timestamp())

        return {
            "symbol":        symbol.upper(),
            "ltp":           round(float(price), 2),
            "open":          round(float(opens[0]  if opens  else meta.get("regularMarketOpen",    price)), 2),
            "high":          round(float(max(highs) if highs  else meta.get("regularMarketDayHigh", price)), 2),
            "low":           round(float(min(lows)  if lows   else meta.get("regularMarketDayLow",  price)), 2),
            "previous_close":round(float(prev), 2),
            "change":        round(float(price - prev), 2),
            "change_pct":    round(float(pct), 2),
            "change_text":   f"{pct:+.2f}%",
            "volume":        int(sum(volumes) if volumes else meta.get("regularMarketVolume", 0) or 0),
            "vwap":          round(float(meta.get("regularMarketPrice", price)), 2),
            "timestamp_str": ts.strftime("%d-%b-%Y %H:%M:%S"),
            "source":        "yahoo",
        }
    except Exception as exc:
        log.debug("Yahoo quote parse error for %s: %s", symbol, exc)
        return None


def _candles_from_yahoo(symbol: str, timeframe: str) -> pd.DataFrame:
    interval, range_ = YAHOO_INTERVALS.get(timeframe, YAHOO_INTERVALS["5m"])
    chart = _yahoo_chart(symbol, interval, range_)
    if not chart:
        return pd.DataFrame()
    try:
        timestamps = chart.get("timestamp") or []
        q = (chart.get("indicators", {}).get("quote") or [{}])[0]
        opens, highs, lows, closes, volumes = (
            q.get("open", []), q.get("high", []), q.get("low", []),
            q.get("close", []), q.get("volume", []),
        )
        n = min(len(timestamps), len(opens), len(highs), len(lows), len(closes), len(volumes))
        if n == 0:
            return pd.DataFrame()

        df = pd.DataFrame({
            "timestamp": pd.to_datetime(timestamps[:n], unit="s", utc=True).tz_convert(NSE_TZ),
            "open":   opens[:n],
            "high":   highs[:n],
            "low":    lows[:n],
            "close":  closes[:n],
            "volume": volumes[:n],
        })
        df = df.dropna(subset=["open", "high", "low", "close"])
        df["volume"] = df["volume"].fillna(0).astype(int)
        df.attrs["source"] = "yahoo"
        return df.tail(120).reset_index(drop=True)
    except Exception as exc:
        log.debug("Yahoo candle parse error for %s: %s", symbol, exc)
        return pd.DataFrame()


# ── Sample fallback ──────────────────────────────────────────────────────────
def _sample_candles(symbol: str, timeframe: str) -> pd.DataFrame:
    rng        = random.Random(f"{symbol}:{timeframe}:{latest_market_timestamp().date().isoformat()}")
    base_price = _SAMPLE_PRICE.get(symbol, 100.0)
    periods    = 60
    freq       = {"5m": "5min", "15m": "15min", "1h": "1h"}.get(timeframe, "5min")
    timestamps = pd.date_range(end=latest_market_timestamp(), periods=periods, freq=freq, tz=NSE_TZ)

    prices  = [base_price]
    volumes = []
    for _ in range(periods - 1):
        prices.append(max(1.0, prices[-1] + rng.uniform(-0.5, 0.5)))
        volumes.append(rng.randint(1000, 5000))
    volumes = [rng.randint(1000, 5000)] + volumes

    rows = []
    for i in range(periods):
        o = prices[i] - rng.uniform(0.1, 0.4)
        c = prices[i] + rng.uniform(-0.3, 0.3)
        rows.append({
            "timestamp": timestamps[i],
            "open":   round(o, 2),
            "high":   round(max(o, c) + rng.uniform(0.1, 0.5), 2),
            "low":    round(min(o, c) - rng.uniform(0.1, 0.5), 2),
            "close":  round(c, 2),
            "volume": volumes[i],
        })
    df = pd.DataFrame(rows)
    df.attrs["source"] = "sample"
    return df


# ── Public API ───────────────────────────────────────────────────────────────
def get_live_quote(symbol: str) -> dict:
    """
    Return quote dict with keys: ltp, change_text, volume, timestamp, source, session.
    Priority: Upstox → NSE India API → Yahoo Finance → sample.
    """
    # 1. Try Upstox (real-time, works with token)
    try:
        q = _upstox.get_full_quote(symbol)
        if q and q.get("ltp"):
            ts = latest_market_timestamp()
            pct = q.get("change_pct", 0)
            return {
                **q,
                "change_text": f"{pct:+.2f}%",
                "timestamp":   ts,
                "session":     _market_state(ts),
                "source":      "upstox",
            }
    except Exception as exc:
        log.debug("Upstox quote failed for %s: %s", symbol, exc)

    # 2. Try NSE India (real-time during market hours)
    try:
        q = _nse_quote(symbol)
        if q:
            ts_str = q.get("timestamp_str", "")
            try:
                ts = datetime.strptime(ts_str, "%d-%b-%Y %H:%M:%S").replace(tzinfo=NSE_TZ)
            except Exception:
                ts = latest_market_timestamp()
            q["timestamp"] = ts
            q["session"]   = _market_state(ts)
            return q
    except Exception as exc:
        log.debug("NSE quote failed for %s: %s", symbol, exc)

    # 2. Try Yahoo Finance
    try:
        q = _quote_from_yahoo(symbol)
        if q:
            ts = latest_market_timestamp()
            q["timestamp"] = ts
            q["session"]   = _market_state(ts)
            return q
    except Exception as exc:
        log.debug("Yahoo quote failed for %s: %s", symbol, exc)

    # 3. Sample fallback
    df   = _sample_candles(symbol, "5m")
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else last
    pct  = ((last["close"] - prev["close"]) / prev["close"] * 100) if prev["close"] else 0.0
    ts   = latest_market_timestamp()
    return {
        "symbol":        symbol.upper(),
        "ltp":           round(float(last["close"]), 2),
        "open":          round(float(df.iloc[0]["open"]), 2),
        "high":          round(float(df["high"].max()), 2),
        "low":           round(float(df["low"].min()), 2),
        "previous_close":round(float(prev["close"]), 2),
        "change":        round(float(last["close"] - prev["close"]), 2),
        "change_pct":    round(float(pct), 2),
        "change_text":   f"{pct:+.2f}%",
        "volume":        int(df["volume"].sum()),
        "vwap":          round(float(last["close"]), 2),
        "timestamp_str": ts.strftime("%d-%b-%Y %H:%M:%S"),
        "timestamp":     ts,
        "source":        "sample",
        "session":       _market_state(ts),
    }


def get_historical_candles(symbol: str, timeframe: str = "5m") -> pd.DataFrame:
    """
    Return OHLCV DataFrame with columns: timestamp, open, high, low, close, volume.
    Priority: Upstox → Yahoo Finance → sample.
    """
    # 1. Try Upstox (live 1m data resampled to target timeframe)
    try:
        df = _upstox.get_candles(symbol, timeframe)
        if not df.empty:
            return df
    except Exception as exc:
        log.debug("Upstox candles failed for %s: %s", symbol, exc)

    # 2. Try Yahoo Finance
    try:
        df = _candles_from_yahoo(symbol, timeframe)
        if not df.empty:
            return df
    except Exception as exc:
        log.debug("Yahoo candles failed for %s: %s", symbol, exc)

    return _sample_candles(symbol, timeframe)
