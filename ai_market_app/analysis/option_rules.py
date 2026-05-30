import pandas as pd

LIQUIDITY_MAX_SPREAD_PCT = 2.0
LIQUIDITY_MIN_VOLUME = 500
MONEYNESS_STRIKES = 3


def calculate_pcr(option_df: pd.DataFrame) -> float:
    total_put_oi = option_df["put_oi"].sum()
    total_call_oi = option_df["call_oi"].sum()
    if total_call_oi == 0:
        return 0.0
    return round(total_put_oi / total_call_oi, 2)


def calculate_max_pain(option_df: pd.DataFrame) -> float:
    strikes = option_df["strike"].tolist()
    pain_values = {}

    for expiry_price in strikes:
        total_pain = 0
        for _, row in option_df.iterrows():
            strike = float(row["strike"])
            call_loss = max(0.0, expiry_price - strike) * float(row["call_oi"])
            put_loss = max(0.0, strike - expiry_price) * float(row["put_oi"])
            total_pain += call_loss + put_loss
        pain_values[expiry_price] = total_pain

    if not pain_values:
        return 0.0
    return float(min(pain_values, key=pain_values.get))


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _nearest_strike_step(strikes: list[float]) -> float:
    sorted_strikes = sorted(set(float(strike) for strike in strikes))
    gaps = [
        round(sorted_strikes[i + 1] - sorted_strikes[i], 2)
        for i in range(len(sorted_strikes) - 1)
        if sorted_strikes[i + 1] > sorted_strikes[i]
    ]
    return min(gaps) if gaps else 1.0


def _spread_pct(bid: float, ask: float) -> float | None:
    mid = (bid + ask) / 2
    if mid <= 0:
        return None
    return round((ask - bid) / mid * 100, 2)


def _liquidity_snapshot(option_df: pd.DataFrame, spot_price: float) -> dict:
    strike_step = _nearest_strike_step(option_df["strike"].tolist())
    atm_strike = min(option_df["strike"].tolist(), key=lambda strike: abs(float(strike) - spot_price))
    lower_bound = float(atm_strike) - MONEYNESS_STRIKES * strike_step
    upper_bound = float(atm_strike) + MONEYNESS_STRIKES * strike_step
    tradable_rows = option_df[
        (option_df["strike"].astype(float) >= lower_bound)
        & (option_df["strike"].astype(float) <= upper_bound)
    ]

    failures = []
    spread_values = []
    total_near_volume = 0
    for _, row in tradable_rows.iterrows():
        for side in ("call", "put"):
            bid = _safe_float(row.get(f"{side}_bid"))
            ask = _safe_float(row.get(f"{side}_ask"))
            volume = int(_safe_float(row.get(f"{side}_volume")))
            total_near_volume += volume
            spread = _spread_pct(bid, ask)
            if spread is not None:
                spread_values.append(spread)
                if spread > LIQUIDITY_MAX_SPREAD_PCT:
                    failures.append(f"{side.title()} {row['strike']} spread {spread}%")
            if volume < LIQUIDITY_MIN_VOLUME:
                failures.append(f"{side.title()} {row['strike']} volume {volume}")

    avg_spread = round(sum(spread_values) / len(spread_values), 2) if spread_values else None
    passes = not failures
    return {
        "passes": passes,
        "avg_spread_pct": avg_spread,
        "near_volume": total_near_volume,
        "atm_strike": float(atm_strike),
        "moneyness_window": [round(lower_bound, 2), round(upper_bound, 2)],
        "failures": failures[:5],
    }


def _iv_snapshot(option_df: pd.DataFrame, spot_price: float) -> dict:
    atm_row = option_df.iloc[(option_df["strike"].astype(float) - spot_price).abs().argsort()[:1]]
    atm_call_iv = _safe_float(atm_row.iloc[0].get("call_iv")) if not atm_row.empty else 0.0
    atm_put_iv = _safe_float(atm_row.iloc[0].get("put_iv")) if not atm_row.empty else 0.0
    avg_iv = round((atm_call_iv + atm_put_iv) / 2, 2) if atm_call_iv or atm_put_iv else None

    otm_calls = option_df[option_df["strike"].astype(float) > spot_price]
    otm_puts = option_df[option_df["strike"].astype(float) < spot_price]
    otm_call_iv = otm_calls["call_iv"].astype(float).mean() if not otm_calls.empty else 0
    otm_put_iv = otm_puts["put_iv"].astype(float).mean() if not otm_puts.empty else 0
    call_minus_put_skew = round(float(otm_call_iv - otm_put_iv), 2)

    if avg_iv is None:
        strategy = "UNKNOWN"
        strategy_reason = "IV data unavailable"
    elif avg_iv >= 35:
        strategy = "SELL_PREMIUM"
        strategy_reason = "Modeled ATM IV is high; favor defined-risk premium selling"
    elif avg_iv <= 18:
        strategy = "BUY_PREMIUM"
        strategy_reason = "Modeled ATM IV is low; favor debit structures"
    else:
        strategy = "BALANCED"
        strategy_reason = "Modeled ATM IV is mid-range"

    skew_signal = "CALL_FOMO" if call_minus_put_skew > 2 else "PUT_PROTECTION" if call_minus_put_skew < -2 else "BALANCED"
    return {
        "atm_iv": avg_iv,
        "call_minus_put_skew": call_minus_put_skew,
        "skew_signal": skew_signal,
        "strategy": strategy,
        "strategy_reason": strategy_reason,
        "iv_basis": "modeled_current_iv_no_52w_history",
    }


def option_score(option_df: pd.DataFrame, spot_price: float) -> dict:
    if option_df.empty:
        return {
            "score": 50,
            "pcr": None,
            "max_pain": None,
            "directional_bias": "NEUTRAL",
            "trade_permission": "AVOID",
            "strategy": "UNKNOWN",
            "reasons": ["Option-chain data unavailable"]
        }

    pcr = calculate_pcr(option_df)
    max_pain = calculate_max_pain(option_df)
    liquidity = _liquidity_snapshot(option_df, spot_price)
    iv = _iv_snapshot(option_df, spot_price)
    score = 50
    reasons = []
    risk_flags = []
    directional_bias = "NEUTRAL"
    trade_permission = "ALLOW"

    if liquidity["passes"]:
        score += 10
        reasons.append(
            f"Liquidity gate passed near ATM; avg spread {liquidity['avg_spread_pct']}%"
        )
    else:
        score -= 25
        trade_permission = "AVOID"
        risk_flags.append("LIQUIDITY_FAIL")
        reasons.append("Liquidity gate failed: " + "; ".join(liquidity["failures"]))

    if 1.0 <= pcr <= 1.5:
        score += 15
        directional_bias = "BULLISH"
        reasons.append(f"PCR is supportive at {pcr}")
    elif pcr < 0.70:
        directional_bias = "BEARISH"
        score -= 20
        risk_flags.append("PCR_BEARISH")
        reasons.append(f"PCR is bearish at {pcr}; call writing dominates")
    elif pcr < 0.8:
        directional_bias = "BEARISH"
        score -= 15
        reasons.append(f"PCR is bearish at {pcr}")
    elif 0.8 <= pcr < 1.0:
        reasons.append(f"PCR is neutral at {pcr}")
    elif 1.5 < pcr <= 1.6:
        directional_bias = "BULLISH"
        score += 5
        reasons.append(f"PCR is elevated but still constructive at {pcr}")
    elif pcr > 1.6:
        score -= 15
        risk_flags.append("PCR_EXTREME")
        reasons.append(f"PCR is crowded above 1.60 at {pcr}; avoid chasing long entries")

    highest_put_oi_row = option_df.loc[option_df["put_oi"].idxmax()]
    highest_call_oi_row = option_df.loc[option_df["call_oi"].idxmax()]
    put_wall = float(highest_put_oi_row["strike"])
    call_wall = float(highest_call_oi_row["strike"])

    if put_wall < spot_price:
        score += 10
        reasons.append("Highest Put OI is below spot, showing support")
    else:
        score -= 10
        risk_flags.append("PUT_WALL_ABOVE_SPOT")
        reasons.append("Put OI support is not below spot")

    if call_wall > spot_price:
        reasons.append("Highest Call OI is above spot, showing resistance")
    else:
        directional_bias = "BULLISH_BREAKOUT"
        score += 12
        reasons.append("Spot is above highest Call OI; short-covering rally risk is active")

    if max_pain and spot_price > max_pain:
        distance_pct = (spot_price - max_pain) / spot_price * 100
        score += 5 if distance_pct <= 3 else 0
        reasons.append(f"Spot is above max pain by {distance_pct:.1f}%")
    elif max_pain and spot_price < max_pain:
        distance_pct = (max_pain - spot_price) / spot_price * 100
        score -= 5
        reasons.append(f"Spot is below max pain by {distance_pct:.1f}%")

    reasons.append(iv["strategy_reason"])
    if iv["skew_signal"] == "CALL_FOMO":
        score += 5
        reasons.append("OTM Call IV exceeds Put IV; speculative upside demand is present")
    elif iv["skew_signal"] == "PUT_PROTECTION":
        risk_flags.append("PUT_SKEW")
        reasons.append("OTM Put IV exceeds Call IV; downside protection demand is present")

    return {
        "score": min(max(int(score), 0), 100),
        "pcr": pcr,
        "max_pain": max_pain,
        "highest_put_oi_strike": put_wall,
        "highest_call_oi_strike": call_wall,
        "directional_bias": directional_bias,
        "trade_permission": trade_permission,
        "risk_flags": risk_flags,
        "liquidity": liquidity,
        "iv": iv,
        "strategy": iv["strategy"],
        "reasons": reasons
    }
