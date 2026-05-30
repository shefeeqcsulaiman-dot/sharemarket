import pandas as pd
import ta


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema_9"] = ta.trend.ema_indicator(df["close"], window=9)
    df["ema_20"] = ta.trend.ema_indicator(df["close"], window=20)
    df["ema_50"] = ta.trend.ema_indicator(df["close"], window=50)
    df["rsi"] = ta.momentum.rsi(df["close"], window=14)

    macd = ta.trend.MACD(df["close"])
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()

    df["atr"] = ta.volatility.average_true_range(
        df["high"], df["low"], df["close"], window=14
    )

    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    df["vwap"] = (typical_price * df["volume"]).cumsum() / df["volume"].cumsum()

    return df


def technical_score(df: pd.DataFrame) -> dict:
    latest = df.iloc[-1]
    score = 0
    reasons = []

    if latest["close"] > latest["vwap"]:
        score += 15
        reasons.append("Price is above VWAP")

    if latest["ema_9"] > latest["ema_20"]:
        score += 15
        reasons.append("EMA 9 is above EMA 20")

    if latest["close"] > latest["ema_50"]:
        score += 10
        reasons.append("Price is above EMA 50")

    if latest["rsi"] > 55:
        score += 10
        reasons.append("RSI is bullish")

    if latest["macd"] > latest["macd_signal"]:
        score += 10
        reasons.append("MACD is positive")

    if latest["rsi"] > 75:
        score -= 10
        reasons.append("RSI is overbought")

    return {
        "score": min(max(int(score), 0), 100),
        "reasons": reasons,
        "latest": latest.to_dict()
    }
