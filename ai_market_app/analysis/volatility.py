"""
Volatility regime classifier.
Labels the current market as low / normal / high volatility
and adjusts signal confidence and strategy recommendations accordingly.
"""

import numpy as np
import pandas as pd


def classify(df: pd.DataFrame, option_iv: float | None = None) -> dict:
    """
    Classify current volatility regime from candle data and optional IV.

    Returns:
        regime       : 'low' | 'normal' | 'high'
        atr_pct      : ATR as % of price
        iv           : implied volatility if available
        percentile   : where current ATR sits in last-30-bar history (0-100)
        recommendation : strategy note
        reasons      : list of explanation strings
    """
    if df.empty or len(df) < 10:
        return _default()

    close   = float(df.iloc[-1]["close"] or 1)
    atr_col = df["atr"] if "atr" in df.columns else None

    if atr_col is None or atr_col.dropna().empty:
        # Compute ATR on the fly
        hi  = df["high"].astype(float)
        lo  = df["low"].astype(float)
        cl  = df["close"].astype(float)
        tr  = pd.concat([hi - lo, (hi - cl.shift()).abs(), (lo - cl.shift()).abs()], axis=1).max(axis=1)
        atr_col = tr.rolling(14).mean()

    atr_series  = atr_col.dropna()
    current_atr = float(atr_series.iloc[-1]) if not atr_series.empty else 0

    atr_pct = round(current_atr / close * 100, 2) if close else 0

    # Percentile of current ATR in last 30 bars
    recent = atr_series.tail(30)
    pct    = float(pd.Series(recent).rank(pct=True).iloc[-1] * 100) if len(recent) > 1 else 50.0

    # Historical volatility (20-bar std of returns, annualised)
    returns = df["close"].astype(float).pct_change().dropna().tail(20)
    hv20    = float(returns.std() * np.sqrt(252) * 100) if len(returns) >= 5 else 0.0

    # Combine ATR% and HV to determine regime
    reasons = []

    if atr_pct > 2.5 or hv20 > 35 or pct > 75:
        regime = "high"
        reasons.append(f"High volatility — ATR {atr_pct}% of price, HV {hv20:.1f}%")
        reasons.append("Use wider stops; reduce position size; prefer option selling")
        rec = "High-vol environment: consider selling spreads / iron condors. Wider SL required."
    elif atr_pct < 0.8 or (hv20 < 15 and pct < 30):
        regime = "low"
        reasons.append(f"Low volatility — ATR {atr_pct}% of price, HV {hv20:.1f}%")
        reasons.append("Breakout setups more reliable; EMA crossovers cleaner in low-vol")
        rec = "Low-vol environment: breakout/momentum plays preferred. Tight stops valid."
    else:
        regime = "normal"
        reasons.append(f"Normal volatility — ATR {atr_pct}% of price, HV {hv20:.1f}%")
        rec = "Normal conditions: standard signal weights apply."

    # IV overlay (from option chain)
    iv_note = ""
    if option_iv and option_iv > 0:
        if option_iv > 40:
            iv_note = f"IV {option_iv}% elevated — option premium expensive (sell premium favoured)"
            if regime != "high":
                regime = "high"
                reasons.append(iv_note)
        elif option_iv < 18:
            iv_note = f"IV {option_iv}% low — options cheap (buy premium favoured)"
            reasons.append(iv_note)
        else:
            reasons.append(f"IV {option_iv}% in normal range")

    return {
        "regime":         regime,
        "atr_pct":        atr_pct,
        "hv20":           round(hv20, 1),
        "iv":             option_iv,
        "percentile":     round(pct, 1),
        "recommendation": rec,
        "reasons":        reasons[:4],
    }


def _default() -> dict:
    return {
        "regime": "normal", "atr_pct": 0, "hv20": 0,
        "iv": None, "percentile": 50,
        "recommendation": "Insufficient data for volatility classification.",
        "reasons": [],
    }
