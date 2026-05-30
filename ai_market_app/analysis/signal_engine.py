def generate_signal(symbol: str, technical: dict, option: dict, news: dict, market_score: int = 50) -> dict:
    final_score = (
        technical["score"] * 0.40 +
        option["score"] * 0.30 +
        news["score"] * 0.20 +
        market_score * 0.10
    )
    final_score = round(final_score, 2)

    all_reasons = []
    all_reasons.extend(technical.get("reasons", []))
    all_reasons.extend(option.get("reasons", []))
    all_reasons.extend(news.get("reasons", []))

    if final_score >= 75:
        signal = "BUY"
    elif 65 <= final_score < 75:
        signal = "BUY"
    elif 55 <= final_score < 65:
        signal = "WATCH"
    elif 45 <= final_score < 55:
        signal = "NEUTRAL"
    elif 35 <= final_score < 45:
        signal = "WATCH"
    else:
        signal = "AVOID"

    if option.get("trade_permission") == "AVOID":
        signal = "AVOID"
        all_reasons.insert(0, "Trade blocked by option-chain liquidity gate")
    elif option.get("directional_bias") in ["BEARISH"] and final_score <= 45:
        signal = "SELL"
        all_reasons.insert(0, "Bearish option-chain structure supports a sell-side setup")
    elif option.get("directional_bias") == "BULLISH_BREAKOUT" and final_score >= 60:
        signal = "BUY"
        all_reasons.insert(0, "Spot has cleared the major Call OI wall; short-covering risk supports upside")

    if final_score < 40 or final_score > 80:
        risk = "MEDIUM"
    else:
        risk = "LOW"

    if option.get("pcr") and option["pcr"] > 1.8:
        risk = "HIGH"
        all_reasons.append("PCR is elevated, increasing trade risk")
    if option.get("risk_flags"):
        risk = "HIGH" if "LIQUIDITY_FAIL" in option["risk_flags"] else risk
        all_reasons.append("Option risk flags: " + ", ".join(option["risk_flags"]))
    if news.get("impact") == "high" and news.get("sentiment") == "negative":
        risk = "HIGH"
        all_reasons.append("High-impact negative news is present")

    if signal == "SELL":
        invalid_if = "Invalidate if price closes above the Call OI resistance wall or bearish setup stop"
    elif signal == "AVOID":
        invalid_if = "No active trade recommended until option-chain gates improve"
    else:
        invalid_if = "Review stop-loss level and invalidate if price moves against the setup"
    return {
        "timestamp": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": symbol,
        "signal": signal,
        "confidence": final_score,
        "risk": risk,
        "reasons": all_reasons[:10],
        "technical_score": technical["score"],
        "option_score": option["score"],
        "news_score": news["score"],
        "market_score": market_score,
        "option_strategy": option.get("strategy", "UNKNOWN"),
        "option_bias": option.get("directional_bias", "NEUTRAL"),
        "option_trade_permission": option.get("trade_permission", "ALLOW"),
        "invalid_if": invalid_if
    }
