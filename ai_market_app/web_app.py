from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Flask, render_template, jsonify, request

from config.symbols import SYMBOLS
from config.settings import DEFAULT_MARKET_SCORE
from data.market_data import get_live_quote, get_historical_candles
from data.option_chain import get_option_chain
from data.news_data import fetch_google_news
from analysis.technicals import add_indicators, technical_score
from analysis.option_rules import option_score
from analysis.news_ai import simple_news_score
from analysis.signal_engine import generate_signal
from analysis.risk_engine import calculate_trade_levels, recommend_ce_strikes

app = Flask(__name__)
NSE_TZ = ZoneInfo("Asia/Kolkata")
SUPPORTED_TIMEFRAMES = {"5m", "15m", "1h"}


def _iso(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _age_minutes(value) -> int | None:
    if not hasattr(value, "astimezone"):
        return None
    now = datetime.now(NSE_TZ)
    return max(0, int((now - value.astimezone(NSE_TZ)).total_seconds() // 60))


def _option_summary(symbol: str, name: str, spot_price: float) -> dict:
    option_df = get_option_chain(symbol, spot_price)
    option    = option_score(option_df, spot_price)
    return {
        "symbol":           symbol,
        "name":             name,
        "spot":             round(spot_price, 2),
        "pcr":              option.get("pcr"),
        "max_pain":         option.get("max_pain"),
        "put_oi_support":   option.get("highest_put_oi_strike"),
        "call_oi_resistance": option.get("highest_call_oi_strike"),
        "score":            option.get("score"),
        "bias":             option.get("directional_bias"),
        "strategy":         option.get("strategy"),
        "liquidity_pass":   option.get("liquidity", {}).get("passes"),
        "source":           option_df.attrs.get("source", "model") if not option_df.empty else "model",
    }


@app.route("/")
def index():
    return render_template("index.html", symbols=list(SYMBOLS.keys()))


# ── Fast price-tick endpoint (polls every 5 s from the frontend) ─────────────
@app.route("/api/quote/<symbol>")
def quick_quote(symbol: str):
    symbol = symbol.upper()
    if symbol not in SYMBOLS:
        return jsonify({"error": "Unknown symbol"}), 400
    q = get_live_quote(symbol)
    return jsonify({
        "symbol":      symbol,
        "ltp":         q.get("ltp", 0),
        "change_text": q.get("change_text", "—"),
        "change_pct":  q.get("change_pct", 0),
        "volume":      q.get("volume", 0),
        "source":      q.get("source", "unknown"),
        "session":     q.get("session", "unknown"),
        "timestamp":   _iso(q.get("timestamp")),
    })


# ── Full signal endpoint ─────────────────────────────────────────────────────
@app.route("/api/signal", methods=["POST"])
def get_signal():
    body      = request.get_json(silent=True) or {}
    symbol    = body.get("symbol", "VEDL")
    timeframe = body.get("timeframe", "15m")
    market_score = body.get("market_score", DEFAULT_MARKET_SCORE)

    if symbol not in SYMBOLS:
        return jsonify({"error": "Invalid symbol"}), 400
    if timeframe not in SUPPORTED_TIMEFRAMES:
        return jsonify({"error": "Invalid timeframe"}), 400
    try:
        market_score = min(max(int(market_score), 0), 100)
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid market score"}), 400

    company_name    = SYMBOLS[symbol]["name"]
    sector          = SYMBOLS[symbol]["sector"]
    related_symbols = SYMBOLS[symbol].get("related", [])

    quote        = get_live_quote(symbol)
    df           = get_historical_candles(symbol, timeframe)
    quote_source = quote.get("source", "sample")
    candle_source = df.attrs.get("source", quote_source)
    data_mode    = "live" if quote_source != "sample" and candle_source != "sample" else "sample"
    if quote_source != candle_source:
        data_mode = "mixed"

    if df.empty:
        return jsonify({"error": "No candle data available"}), 400

    analysis_df  = add_indicators(df)
    technical    = technical_score(analysis_df)
    spot_price   = float(analysis_df.iloc[-1]["close"])

    option_df    = get_option_chain(symbol, spot_price)
    option       = option_score(option_df, spot_price)
    option_source = option_df.attrs.get("source", "model") if not option_df.empty else "model"

    related_options = []
    for rsym in related_symbols:
        rinfo  = SYMBOLS.get(rsym, {"name": rsym, "sector": sector})
        rquote = get_live_quote(rsym)
        related_options.append(
            _option_summary(rsym, rinfo.get("name", rsym), float(rquote.get("ltp", 0) or 0))
        )

    news_items = fetch_google_news(company_name)
    news       = simple_news_score(news_items)

    signal = generate_signal(
        symbol=symbol,
        technical=technical,
        option=option,
        news=news,
        market_score=market_score,
    )
    levels = calculate_trade_levels(signal["signal"], technical["latest"], option_df)
    ce_list = recommend_ce_strikes(signal["signal"], spot_price, option_df)

    return jsonify({
        "symbol":       symbol,
        "company_name": company_name,
        "sector":       sector,
        "quote": {
            "ltp":            round(quote.get("ltp", 0.0), 2),
            "change":         quote.get("change_text", "N/A"),
            "change_pct":     quote.get("change_pct", 0),
            "volume":         quote.get("volume", 0),
            "vwap_deviation": round(spot_price - technical["latest"].get("vwap", spot_price), 2),
            "timestamp":      _iso(quote.get("timestamp")),
            "source":         quote_source,
            "session":        quote.get("session", "unknown"),
        },
        "data": {
            "mode":          data_mode,
            "quote_source":  quote_source,
            "candle_source": candle_source,
            "option_source": option_source,
            "as_of":         _iso(quote.get("timestamp")),
            "latest_candle": _iso(analysis_df.iloc[-1]["timestamp"]),
            "age_minutes":   _age_minutes(quote.get("timestamp")),
        },
        "signal": {
            "status":               signal["signal"],
            "confidence":           signal["confidence"],
            "risk":                 signal["risk"],
            "option_strategy":      signal.get("option_strategy", "UNKNOWN"),
            "option_bias":          signal.get("option_bias", "NEUTRAL"),
            "option_trade_permission": signal.get("option_trade_permission", "ALLOW"),
            "invalid_if":           signal.get("invalid_if", "N/A"),
        },
        "levels": {
            "entry":         levels["entry"],
            "stop_loss":     levels["stop_loss"],
            "target_1":      levels["target_1"],
            "target_2":      levels["target_2"],
            "risk_reward":   levels.get("risk_reward"),
            "method":        levels.get("method", "atr"),
            "invalid_if":    levels.get("invalid_if", ""),
            "oi_support":    levels.get("oi_support"),
            "oi_resistance": levels.get("oi_resistance"),
        },
        "ce_list": ce_list,
        "scores": {
            "technical": signal["technical_score"],
            "options":   signal["option_score"],
            "news":      signal["news_score"],
            "market":    signal["market_score"],
        },
        "technicals": {
            "rsi":        round(technical["latest"].get("rsi",    0), 2),
            "ema_9":      round(technical["latest"].get("ema_9",  0), 2),
            "ema_20":     round(technical["latest"].get("ema_20", 0), 2),
            "macd":       round(technical["latest"].get("macd",   0), 2),
            "atr":        round(technical["latest"].get("atr",    0), 2),
            "last_close": round(technical["latest"].get("close",  0), 2),
            "last_volume":int(technical["latest"].get("volume",   0) or 0),
        },
        "options": {
            "pcr":               option.get("pcr", "N/A"),
            "max_pain":          option.get("max_pain", "N/A"),
            "put_oi_support":    option.get("highest_put_oi_strike", "N/A"),
            "call_oi_resistance":option.get("highest_call_oi_strike", "N/A"),
            "directional_bias":  option.get("directional_bias", "NEUTRAL"),
            "trade_permission":  option.get("trade_permission", "ALLOW"),
            "strategy":          option.get("strategy", "UNKNOWN"),
            "liquidity":         option.get("liquidity", {}),
            "iv":                option.get("iv", {}),
            "risk_flags":        option.get("risk_flags", []),
            "source":            option_source,
        },
        "related_options": related_options,
        "reasons":  signal["reasons"][:8],
        "news": {
            "sentiment": news.get("sentiment", "neutral"),
            "impact":    news.get("impact", "low"),
            "items":     news.get("items", [])[:4],
        },
        "chart": {
            "timestamps": [_iso(ts) for ts in analysis_df["timestamp"].tail(60).tolist()],
            "opens":      analysis_df["open"].tail(60).round(2).tolist(),
            "highs":      analysis_df["high"].tail(60).round(2).tolist(),
            "lows":       analysis_df["low"].tail(60).round(2).tolist(),
            "closes":     analysis_df["close"].tail(60).round(2).tolist(),
            "volumes":    analysis_df["volume"].tail(60).tolist(),
            "ema_9":      analysis_df["ema_9"].tail(60).round(2).tolist(),
            "ema_20":     analysis_df["ema_20"].tail(60).round(2).tolist(),
            "vwap":       analysis_df["vwap"].tail(60).round(2).tolist(),
        },
    })


if __name__ == "__main__":
    app.run(debug=True, port=5000)
