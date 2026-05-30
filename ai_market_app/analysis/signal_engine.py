"""
Signal engine: combines technical, options, news, volatility and pattern scores
into a final trading signal with confidence and risk rating.
"""

from datetime import datetime


def generate_signal(
    symbol: str,
    technical: dict,
    option: dict,
    news: dict,
    market_score: int = 50,
    volatility: dict | None = None,
    patterns: dict | None = None,
) -> dict:
    vol   = volatility or {}
    pat   = patterns   or {}

    t_score = float(technical.get("score", 50) or 50)
    o_score = float(option.get("score",    50) or 50)
    n_score = float(news.get("score",      50) or 50)
    m_score = float(market_score)

    # Volatility regime weight adjustment
    regime = vol.get("regime", "normal")
    if regime == "high":
        # In high-vol, options data is more reliable; weight it heavier
        weights = (0.30, 0.40, 0.15, 0.10, 0.05)
    elif regime == "low":
        # In low-vol, technicals are cleaner signals
        weights = (0.50, 0.20, 0.20, 0.10, 0.00)
    else:
        weights = (0.40, 0.30, 0.20, 0.10, 0.00)

    # Pattern bonus (max +8)
    p_bonus = min(pat.get("score_bonus", 0), 8)

    final_score = (
        t_score * weights[0] +
        o_score * weights[1] +
        n_score * weights[2] +
        m_score * weights[3] +
        p_bonus * weights[4] if len(weights) > 4 else 0
    )
    final_score = min(max(round(final_score, 2), 0), 100)

    # ── Signal classification (non-overlapping, no duplicate ranges) ──────
    if   final_score >= 72: signal = "BUY"
    elif final_score >= 60: signal = "WATCH"
    elif final_score >= 45: signal = "NEUTRAL"
    elif final_score >= 32: signal = "WATCH"    # bearish watch
    else:                   signal = "AVOID"

    # ── Option chain overrides ────────────────────────────────────────────
    if option.get("trade_permission") == "AVOID":
        signal = "AVOID"
    elif option.get("directional_bias") == "BEARISH" and final_score <= 45:
        signal = "SELL"
    elif option.get("directional_bias") == "BULLISH_BREAKOUT" and final_score >= 55:
        signal = "BUY"

    # ── Pattern override (strong reversal patterns override NEUTRAL) ──────
    if pat.get("strong_reversal") and signal == "NEUTRAL":
        signal = "WATCH" if pat.get("direction") == "bullish" else "WATCH"

    # ── News override (high-impact negative blocks new longs) ─────────────
    if news.get("impact") == "high" and news.get("sentiment") == "negative":
        if signal == "BUY":
            signal = "WATCH"

    # ── Risk rating ───────────────────────────────────────────────────────
    risk = "LOW"
    if final_score < 40 or final_score > 85:
        risk = "MEDIUM"
    if option.get("pcr") and float(option["pcr"] or 0) > 1.8:
        risk = "HIGH"
    if "LIQUIDITY_FAIL" in option.get("risk_flags", []):
        risk = "HIGH"
    if regime == "high":
        risk = "HIGH" if risk != "HIGH" else risk
    if news.get("impact") == "high" and news.get("sentiment") == "negative":
        risk = "HIGH"

    # ── Confidence adjustment ─────────────────────────────────────────────
    confidence = final_score
    # Boost if multiple factors agree
    bullish_signals = sum([
        t_score > 55,
        o_score > 60,
        n_score > 55,
        pat.get("direction") == "bullish",
        option.get("directional_bias") in ("BULLISH", "BULLISH_BREAKOUT"),
    ])
    if bullish_signals >= 4:
        confidence = min(confidence + 5, 98)

    # ── Collect reasons ───────────────────────────────────────────────────
    all_reasons = []
    all_reasons.extend(technical.get("reasons",  []))
    all_reasons.extend(option.get("reasons",     []))
    all_reasons.extend(news.get("reasons",        [])[:3])
    all_reasons.extend(vol.get("reasons",         []))
    all_reasons.extend(pat.get("reasons",         []))

    # ── Invalid-if (single, signal-specific) ─────────────────────────────
    if signal == "BUY":
        invalid_if = f"Price closes below SL or RSI drops under 35"
    elif signal == "SELL":
        invalid_if = "Price closes above Call OI resistance wall"
    elif signal == "AVOID":
        invalid_if = "No trade — wait for option chain gates to open"
    else:
        invalid_if = "Setup invalidates if any key level breaks"

    return {
        "timestamp":           datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "symbol":              symbol,
        "signal":              signal,
        "confidence":          round(confidence, 1),
        "risk":                risk,
        "reasons":             all_reasons[:10],
        "technical_score":     int(t_score),
        "option_score":        int(o_score),
        "news_score":          int(n_score),
        "market_score":        int(m_score),
        "volatility_regime":   regime,
        "pattern_signal":      pat.get("strongest_pattern", ""),
        "option_strategy":     option.get("strategy",          "UNKNOWN"),
        "option_bias":         option.get("directional_bias",  "NEUTRAL"),
        "option_trade_permission": option.get("trade_permission", "ALLOW"),
        "invalid_if":          invalid_if,
        "weights_used":        weights[:4],
    }
