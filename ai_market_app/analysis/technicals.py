"""
Technical analysis: indicators and scoring.
Fixed: VWAP per-bar, symmetric RSI penalties, EMA-50 used in scoring.
"""

import pandas as pd
import numpy as np
import ta


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    df["ema_9"]  = ta.trend.ema_indicator(df["close"], window=9)
    df["ema_20"] = ta.trend.ema_indicator(df["close"], window=20)
    df["ema_50"] = ta.trend.ema_indicator(df["close"], window=50)
    df["rsi"]    = ta.momentum.rsi(df["close"], window=14)

    macd = ta.trend.MACD(df["close"])
    df["macd"]        = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    df["macd_hist"]   = macd.macd_diff()

    df["atr"] = ta.volatility.average_true_range(
        df["high"], df["low"], df["close"], window=14
    )

    # Correct per-bar VWAP: cumsum(TP×Vol) / cumsum(Vol)
    tp = (df["high"] + df["low"] + df["close"]) / 3
    cum_vol = df["volume"].cumsum().replace(0, np.nan)
    df["vwap"] = (tp * df["volume"]).cumsum() / cum_vol

    # Bollinger Bands (useful for squeeze detection)
    bb = ta.volatility.BollingerBands(df["close"], window=20, window_dev=2)
    df["bb_upper"] = bb.bollinger_hband()
    df["bb_lower"] = bb.bollinger_lband()
    df["bb_mid"]   = bb.bollinger_mavg()

    # Rate of Change (momentum)
    df["roc_5"]  = df["close"].pct_change(5)  * 100
    df["roc_10"] = df["close"].pct_change(10) * 100

    return df


def technical_score(df: pd.DataFrame) -> dict:
    latest = df.iloc[-1]
    prev   = df.iloc[-2] if len(df) > 1 else latest
    score  = 50
    reasons = []

    close  = float(latest["close"] or 0)
    vwap   = float(latest["vwap"]  or close)
    ema9   = float(latest["ema_9"] or close)
    ema20  = float(latest["ema_20"] or close)
    ema50  = float(latest.get("ema_50") or close)
    rsi    = float(latest["rsi"]   or 50)
    macd   = float(latest["macd"]  or 0)
    macd_s = float(latest["macd_signal"] or 0)
    macd_h = float(latest.get("macd_hist") or 0)
    atr    = float(latest["atr"]   or 0)
    roc5   = float(latest.get("roc_5") or 0)

    # ── VWAP position ──────────────────────────────────────────────────────
    if close > vwap:
        score += 15
        reasons.append(f"Price ₹{close:.1f} above VWAP ₹{vwap:.1f} — bullish intraday bias")
    else:
        score -= 10
        reasons.append(f"Price below VWAP ₹{vwap:.1f} — bearish intraday bias")

    # ── EMA alignment ─────────────────────────────────────────────────────
    if ema9 > ema20:
        score += 12
        reasons.append("EMA 9 above EMA 20 — short-term momentum positive")
    else:
        score -= 8
        reasons.append("EMA 9 below EMA 20 — short-term momentum negative")

    if close > ema50 and not pd.isna(ema50):
        score += 8
        reasons.append("Price above EMA 50 — medium-term trend intact")
    elif not pd.isna(ema50):
        score -= 6
        reasons.append("Price below EMA 50 — medium-term trend weak")

    # ── RSI — symmetric overbought/oversold ───────────────────────────────
    if 45 <= rsi <= 65:
        score += 10
        reasons.append(f"RSI {rsi:.1f} — neutral-bullish momentum zone")
    elif rsi > 70:
        score -= 15
        reasons.append(f"RSI {rsi:.1f} — overbought, caution on new longs")
    elif rsi < 30:
        score -= 10
        reasons.append(f"RSI {rsi:.1f} — oversold, potential for reversal bounce")
    elif rsi > 55:
        score += 6
        reasons.append(f"RSI {rsi:.1f} — bullish")
    elif rsi < 40:
        score -= 5
        reasons.append(f"RSI {rsi:.1f} — weak momentum")

    # ── MACD ──────────────────────────────────────────────────────────────
    if macd > macd_s:
        score += 8
        if macd_h > float(prev.get("macd_hist") or 0):
            score += 3
            reasons.append("MACD above signal and histogram expanding — strong momentum")
        else:
            reasons.append("MACD above signal line — bullish")
    else:
        score -= 6
        reasons.append("MACD below signal line — bearish pressure")

    # ── Price momentum (Rate of Change) ───────────────────────────────────
    if roc5 > 1.5:
        score += 5
        reasons.append(f"5-bar momentum +{roc5:.1f}% — strong upward thrust")
    elif roc5 < -1.5:
        score -= 5
        reasons.append(f"5-bar momentum {roc5:.1f}% — downside pressure")

    # ── Bollinger position ────────────────────────────────────────────────
    bb_upper = float(latest.get("bb_upper") or 0)
    bb_lower = float(latest.get("bb_lower") or 0)
    if bb_upper and close > bb_upper:
        score -= 5
        reasons.append("Price above Bollinger upper band — stretched")
    elif bb_lower and close < bb_lower:
        score += 5
        reasons.append("Price at Bollinger lower band — potential support")

    score = min(max(int(score), 0), 100)

    return {
        "score":   score,
        "reasons": reasons,
        "latest":  latest.to_dict(),
    }
