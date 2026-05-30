"""
Risk engine: calculates precise entry, stop-loss, and target levels.
Uses ATR for baseline + option OI walls for true support/resistance.
"""

import pandas as pd


def _find_oi_levels(option_df: pd.DataFrame, spot: float) -> dict:
    """Extract key OI support/resistance levels from the option chain."""
    if option_df is None or option_df.empty:
        return {}

    # Strikes above and below spot
    above = option_df[option_df["strike"].astype(float) > spot]
    below = option_df[option_df["strike"].astype(float) < spot]

    # Nearest Put OI wall below spot (support)
    put_support = None
    if not below.empty:
        row = below.loc[below["put_oi"].idxmax()]
        put_support = float(row["strike"])

    # Nearest Call OI wall above spot (resistance)
    call_resistance = None
    if not above.empty:
        row = above.loc[above["call_oi"].idxmax()]
        call_resistance = float(row["strike"])

    # 2nd call wall (second target)
    call_resistance_2 = None
    if not above.empty and call_resistance is not None:
        remaining = above[above["strike"].astype(float) > call_resistance]
        if not remaining.empty:
            row2 = remaining.loc[remaining["call_oi"].idxmax()]
            call_resistance_2 = float(row2["strike"])

    # 2nd put wall (second SL zone)
    put_support_2 = None
    if not below.empty and put_support is not None:
        remaining = below[below["strike"].astype(float) < put_support]
        if not remaining.empty:
            row2 = remaining.loc[remaining["put_oi"].idxmax()]
            put_support_2 = float(row2["strike"])

    return {
        "put_support":      put_support,
        "put_support_2":    put_support_2,
        "call_resistance":  call_resistance,
        "call_resistance_2":call_resistance_2,
    }


def calculate_trade_levels(
    signal: str,
    latest: dict,
    option_df: pd.DataFrame | None = None,
) -> dict:
    """
    Return entry, stop_loss, target_1, target_2, risk_reward, method.

    Logic:
      - Entry  : current price (BUY/WATCH) or VWAP if below spot
      - SL     : Put OI support wall (or 1.5×ATR if no option data)
      - T1     : Call OI resistance wall (or 1.5×ATR)
      - T2     : Next Call OI wall (or 2.5×ATR)
    """
    close = float(latest.get("close", 0) or 0)
    atr   = float(latest.get("atr",   0) or 0)
    vwap  = float(latest.get("vwap",  0) or 0)
    if atr == 0:
        atr = close * 0.015

    oi = _find_oi_levels(option_df, close) if option_df is not None else {}

    if signal in ("BUY", "WATCH"):
        # Entry: slight pullback to VWAP if it's close (within 0.5%), else current price
        if vwap and abs(close - vwap) / close < 0.005:
            entry = round(vwap, 2)
        else:
            entry = round(close, 2)

        # Stop-loss: Put OI support wall (buffer of 0.3%) → else ATR
        if oi.get("put_support") and oi["put_support"] < close:
            sl = round(oi["put_support"] * 0.997, 2)
            method = "oi_walls"
        else:
            sl = round(close - 1.5 * atr, 2)
            method = "atr"

        # Targets: Call OI walls → else ATR
        if oi.get("call_resistance") and oi["call_resistance"] > close:
            t1 = round(oi["call_resistance"], 2)
            t2 = round(oi.get("call_resistance_2") or (close + 2.5 * atr), 2)
        else:
            t1 = round(close + 1.5 * atr, 2)
            t2 = round(close + 2.5 * atr, 2)

    elif signal in ("SELL",):
        entry = round(close, 2)

        if oi.get("call_resistance") and oi["call_resistance"] > close:
            sl = round(oi["call_resistance"] * 1.003, 2)
            method = "oi_walls"
        else:
            sl = round(close + 1.5 * atr, 2)
            method = "atr"

        if oi.get("put_support") and oi["put_support"] < close:
            t1 = round(oi["put_support"], 2)
            t2 = round(oi.get("put_support_2") or (close - 2.5 * atr), 2)
        else:
            t1 = round(close - 1.5 * atr, 2)
            t2 = round(close - 2.5 * atr, 2)

    else:  # AVOID / NEUTRAL
        return {
            "entry":        None,
            "stop_loss":    None,
            "target_1":     None,
            "target_2":     None,
            "risk_reward":  None,
            "method":       "none",
            "invalid_if":   "No active trade — wait for clearer setup",
            "oi_support":   round(oi.get("put_support", 0), 2) if oi.get("put_support") else None,
            "oi_resistance":round(oi.get("call_resistance", 0), 2) if oi.get("call_resistance") else None,
        }

    # Risk-reward
    risk   = abs(entry - sl)
    reward = abs(t1 - entry)
    rr     = round(reward / risk, 2) if risk > 0 else None

    invalid_if = (
        f"Close below ₹{sl}" if signal in ("BUY", "WATCH")
        else f"Close above ₹{sl}"
    )

    return {
        "entry":         entry,
        "stop_loss":     sl,
        "target_1":      t1,
        "target_2":      t2,
        "risk_reward":   rr,
        "method":        method,
        "invalid_if":    invalid_if,
        "oi_support":    round(oi.get("put_support", 0), 2) if oi.get("put_support") else None,
        "oi_resistance": round(oi.get("call_resistance", 0), 2) if oi.get("call_resistance") else None,
    }


def recommend_ce_strikes(
    signal: str,
    spot: float,
    option_df: pd.DataFrame,
    budget_per_lot: float = 20000,
) -> list[dict]:
    """
    Return a ranked list of CE (Call option) strikes to consider.
    Filters for liquidity (volume > 0, spread < 5%) and sorts by recommendation score.
    """
    if option_df is None or option_df.empty:
        return []

    ce_rows = option_df[option_df["call_ltp"] > 0].copy()
    ce_rows["strike"] = ce_rows["strike"].astype(float)

    results = []
    for _, row in ce_rows.iterrows():
        strike   = float(row["strike"])
        ltp      = float(row["call_ltp"])
        bid      = float(row["call_bid"])
        ask      = float(row["call_ask"])
        oi       = int(row["call_oi"])
        vol      = int(row["call_volume"])
        iv       = float(row["call_iv"])
        chg_oi   = int(row["call_change_oi"])

        if ltp <= 0:
            continue

        # Moneyness (call option: strike vs spot)
        diff = strike - spot
        if abs(diff) <= 5:
            moneyness = "ATM"
        elif diff < -20:
            moneyness = "Deep ITM"
        elif diff < 0:
            moneyness = "ITM"
        elif diff <= 20:
            moneyness = "OTM"
        else:
            moneyness = "Deep OTM"

        # Spread %
        spread_pct = round((ask - bid) / ((ask + bid) / 2) * 100, 2) if (ask + bid) > 0 else 99

        # Recommendation score
        score = 0
        if signal in ("BUY", "WATCH"):
            if moneyness == "ATM":           score += 40
            elif moneyness == "OTM":         score += 30
            elif moneyness == "ITM":         score += 20
        if vol > 100000:                     score += 20
        elif vol > 10000:                    score += 10
        if oi > 500000:                      score += 15
        elif oi > 100000:                    score += 8
        if spread_pct < 2:                   score += 10
        elif spread_pct < 5:                 score += 5
        if chg_oi > 0:                       score += 5  # fresh OI buildup

        # Tag
        if signal in ("BUY", "WATCH"):
            if moneyness == "ATM":           tag = "⭐ Recommended"
            elif moneyness == "OTM" and score >= 50: tag = "Aggressive"
            elif moneyness == "ITM":         tag = "Conservative"
            else:                            tag = ""
        else:
            tag = ""

        results.append({
            "strike":      strike,
            "ltp":         ltp,
            "bid":         bid,
            "ask":         ask,
            "oi":          oi,
            "change_oi":   chg_oi,
            "volume":      vol,
            "iv":          iv,
            "moneyness":   moneyness,
            "spread_pct":  spread_pct,
            "score":       score,
            "tag":         tag,
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:10]
