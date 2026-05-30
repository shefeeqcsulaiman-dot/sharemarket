def build_features(df, option_features: dict, news_score: int, market_score: int) -> dict:
    latest = df.iloc[-1]
    return {
        "rsi": float(latest["rsi"]),
        "ema_9_above_20": int(latest["ema_9"] > latest["ema_20"]),
        "close_above_vwap": int(latest["close"] > latest["vwap"]),
        "macd_positive": int(latest["macd"] > latest["macd_signal"]),
        "atr_percent": float(latest["atr"] / latest["close"] if latest["close"] else 0),
        "option_pcr": float(option_features.get("pcr", 1.0)),
        "max_pain_distance": (
            float(latest["close"] - (option_features.get("max_pain") or latest["close"])) / float(latest["close"])
            if latest["close"] else 0
        ),
        "news_score": int(news_score),
        "market_score": int(market_score)
    }
