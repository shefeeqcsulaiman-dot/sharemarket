from datetime import datetime
from zoneinfo import ZoneInfo
import os, urllib3, requests as _req
urllib3.disable_warnings()

from flask import Flask, render_template, jsonify, request, redirect

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
from analysis.patterns import detect as detect_patterns
from analysis.volatility import classify as classify_volatility
from analysis.portfolio_analyst import analyse_holding, analyse_position

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
        "symbol":             symbol,
        "name":               name,
        "spot":               round(spot_price, 2),
        "pcr":                option.get("pcr"),
        "max_pain":           option.get("max_pain"),
        "put_oi_support":     option.get("highest_put_oi_strike"),
        "call_oi_resistance": option.get("highest_call_oi_strike"),
        "score":              option.get("score"),
        "bias":               option.get("directional_bias"),
        "strategy":           option.get("strategy"),
        "liquidity_pass":     option.get("liquidity", {}).get("passes"),
        "source":             option_df.attrs.get("source", "model") if not option_df.empty else "model",
    }


def _ai_commentary(signal: str, confidence: float, technical: dict,
                   option: dict, vol: dict, patterns: dict,
                   levels: dict, symbol: str) -> str:
    """Generate a plain-English AI market summary paragraph."""
    regime   = vol.get("regime", "normal")
    pat      = patterns.get("strongest_pattern", "")
    bias     = option.get("directional_bias", "NEUTRAL")
    pcr      = option.get("pcr")
    rsi      = round(float(technical["latest"].get("rsi", 50) or 50), 1)
    close    = round(float(technical["latest"].get("close", 0) or 0), 2)
    vwap     = round(float(technical["latest"].get("vwap", 0) or 0), 2)
    ema9     = round(float(technical["latest"].get("ema_9", 0) or 0), 2)
    t1       = levels.get("target_1")
    sl       = levels.get("stop_loss")
    method   = levels.get("method", "atr")
    rr       = levels.get("risk_reward")

    lines = []

    # Opening — signal strength
    if signal == "BUY":
        lines.append(f"{symbol} is showing a strong BUY setup with {confidence}% confidence.")
    elif signal == "WATCH":
        lines.append(f"{symbol} is in a WATCH zone ({confidence}% confidence) — conditions are building but not confirmed yet.")
    elif signal == "SELL":
        lines.append(f"{symbol} is showing a SELL signal ({confidence}% confidence) driven by bearish option structure.")
    elif signal == "AVOID":
        lines.append(f"{symbol} signals AVOID — risk is elevated and setup quality is poor.")
    else:
        lines.append(f"{symbol} is NEUTRAL ({confidence}%) — no high-conviction directional edge detected.")

    # Technical context
    pos_vwap = close > vwap
    lines.append(
        f"Price ₹{close} is {'above' if pos_vwap else 'below'} VWAP ₹{vwap} "
        f"and {'above' if close > ema9 else 'below'} EMA-9 ₹{ema9}. "
        f"RSI is {rsi} ({'overbought' if rsi > 70 else 'oversold' if rsi < 30 else 'neutral-bullish' if rsi > 50 else 'bearish'})."
    )

    # Volatility regime
    lines.append(
        f"Market volatility is {regime}. "
        + vol.get("recommendation", "")
    )

    # Option chain
    if pcr:
        pcr_text = f"PCR {pcr} indicates {'bullish put writing' if float(pcr) > 1.0 else 'bearish call writing — sellers in control'}."
        lines.append(pcr_text)

    if bias not in ("NEUTRAL", None):
        lines.append(f"Option chain directional bias: {bias}.")

    # Pattern
    if pat:
        dir_ = patterns.get("direction", "neutral")
        lines.append(f"Candlestick pattern detected: {pat} — {dir_} signal.")

    # Levels
    if t1 and sl:
        method_text = "derived from option OI walls" if method == "oi_walls" else "ATR-based"
        rr_text = f"Risk-reward is 1:{rr}." if rr else ""
        lines.append(f"Trade levels ({method_text}): Target ₹{t1}, Stop ₹{sl}. {rr_text}")

    return " ".join(lines)


@app.route("/")
def index():
    return render_template("index.html", symbols=list(SYMBOLS.keys()))


## ── Upstox OAuth helpers ────────────────────────────────────────────────────

def _upstox_headers():
    from dotenv import load_dotenv; load_dotenv(override=True)
    token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _upstox_get(path: str, params: dict | None = None):
    r = _req.get(f"https://api.upstox.com{path}",
                 headers=_upstox_headers(), params=params,
                 timeout=10, verify=False)
    return r.json()


@app.route("/auth/upstox")
def auth_upstox():
    """Redirect browser to Upstox login page."""
    from dotenv import load_dotenv; load_dotenv()
    api_key  = os.getenv("UPSTOX_API_KEY", "")
    redir    = os.getenv("UPSTOX_REDIRECT_URI", "http://127.0.0.1:5000/auth/callback")
    url = (
        "https://api.upstox.com/v2/login/authorization/dialog"
        f"?response_type=code&client_id={api_key}&redirect_uri={redir}"
    )
    return redirect(url)


@app.route("/auth/callback")
def auth_callback():
    """Upstox redirects here with ?code=XXX — exchange for access token and save."""
    from dotenv import load_dotenv; load_dotenv()
    code     = request.args.get("code", "")
    api_key  = os.getenv("UPSTOX_API_KEY", "")
    secret   = os.getenv("UPSTOX_SECRET", "")
    redir    = os.getenv("UPSTOX_REDIRECT_URI", "http://127.0.0.1:5000/auth/callback")

    if not code:
        return "No code received from Upstox.", 400

    resp = _req.post(
        "https://api.upstox.com/v2/login/authorization/token",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        data={
            "code":          code,
            "client_id":     api_key,
            "client_secret": secret,
            "redirect_uri":  redir,
            "grant_type":    "authorization_code",
        },
        timeout=10, verify=False,
    )
    result = resp.json()

    if result.get("status") == "success" or result.get("access_token"):
        token = result.get("access_token") or result.get("data", {}).get("access_token", "")
        # Save token to .env
        env_path = os.path.join(os.path.dirname(__file__), ".env")
        lines = open(env_path).readlines() if os.path.exists(env_path) else []
        updated = [l for l in lines if not l.startswith("UPSTOX_ACCESS_TOKEN=")]
        updated.append(f"UPSTOX_ACCESS_TOKEN={token}\n")
        open(env_path, "w").writelines(updated)
        # Reload env
        os.environ["UPSTOX_ACCESS_TOKEN"] = token
        import data.upstox as _up
        _up.INSTRUMENT_KEYS  # ensure module reloaded
        return redirect("/?auth=success")
    else:
        return jsonify({"error": "Token exchange failed", "detail": result}), 400


@app.route("/api/portfolio/positions")
def portfolio_positions():
    """Live open positions from Upstox."""
    data = _upstox_get("/v2/portfolio/short-term-positions")
    if data.get("status") == "success":
        rows = data.get("data") or []
        result = []
        for p in rows:
            qty        = int(p.get("quantity", 0) or 0)
            avg_price  = float(p.get("average_price", 0) or 0)
            ltp        = float(p.get("last_price", 0) or 0)
            pnl        = float(p.get("pnl", 0) or 0)
            day_change = float(p.get("day_change_percentage", 0) or 0)
            result.append({
                "symbol":        p.get("tradingsymbol", ""),
                "product":       p.get("product", ""),
                "quantity":      qty,
                "avg_price":     round(avg_price, 2),
                "ltp":           round(ltp, 2),
                "pnl":           round(pnl, 2),
                "day_change_pct":round(day_change, 2),
                "exchange":      p.get("exchange", ""),
                "instrument_type": p.get("instrument_type", ""),
            })
        return jsonify({"status": "ok", "positions": result, "count": len(result)})
    else:
        return jsonify({"status": "error", "error": data.get("errors", "Unknown error"),
                        "need_auth": True}), 401


@app.route("/api/portfolio/holdings")
def portfolio_holdings():
    """Long-term holdings from Upstox."""
    data = _upstox_get("/v2/portfolio/long-term-holdings")
    if data.get("status") == "success":
        rows = data.get("data") or []
        result = []
        total_invested = 0; total_current = 0
        for h in rows:
            qty       = int(h.get("quantity", 0) or 0)
            avg       = float(h.get("average_price", 0) or 0)
            ltp       = float(h.get("last_price", 0) or 0)
            invested  = round(qty * avg, 2)
            current   = round(qty * ltp, 2)
            pnl       = round(current - invested, 2)
            pnl_pct   = round((pnl / invested * 100), 2) if invested else 0
            total_invested += invested; total_current += current
            result.append({
                "symbol":    h.get("tradingsymbol", ""),
                "exchange":  h.get("exchange", ""),
                "quantity":  qty,
                "avg_price": round(avg, 2),
                "ltp":       round(ltp, 2),
                "invested":  invested,
                "current":   current,
                "pnl":       pnl,
                "pnl_pct":   pnl_pct,
                "isin":      h.get("isin", ""),
            })
        total_pnl     = round(total_current - total_invested, 2)
        total_pnl_pct = round((total_pnl / total_invested * 100), 2) if total_invested else 0
        return jsonify({
            "status": "ok", "holdings": result,
            "summary": {
                "total_invested": round(total_invested, 2),
                "total_current":  round(total_current, 2),
                "total_pnl":      total_pnl,
                "total_pnl_pct":  total_pnl_pct,
            }
        })
    else:
        return jsonify({"status": "error", "error": data.get("errors", ""),
                        "need_auth": True}), 401


@app.route("/api/portfolio/analysis")
def portfolio_analysis():
    """
    Fetch live positions + holdings, run AI analysis on each,
    return unified list with BUY MORE / HOLD / EXIT signals.
    """
    from dotenv import load_dotenv; load_dotenv(override=True)

    pos_data  = _upstox_get("/v2/portfolio/short-term-positions")
    hold_data = _upstox_get("/v2/portfolio/long-term-holdings")

    if pos_data.get("status") != "success" or hold_data.get("status") != "success":
        return jsonify({"status": "error", "need_auth": True,
                        "error": "Portfolio access requires login"}), 401

    positions = pos_data.get("data") or []
    holdings  = hold_data.get("data") or []

    results = []

    # ── Analyse each holding ──────────────────────────────────────────────
    for h in holdings:
        sym = h.get("tradingsymbol", "")
        qty = int(h.get("quantity", 0) or 0)
        if qty == 0:
            continue
        holding_dict = {
            "symbol":    sym,
            "avg_price": float(h.get("average_price", 0) or 0),
            "ltp":       float(h.get("last_price", 0) or 0),
            "pnl":       float(h.get("pnl", 0) or 0),
            "pnl_pct":   float(h.get("day_change_percentage", 0) or 0),
            "quantity":  qty,
        }
        # Override pnl_pct with actual total return
        avg  = holding_dict["avg_price"]
        ltp  = holding_dict["ltp"]
        if avg > 0:
            holding_dict["pnl_pct"] = round((ltp - avg) / avg * 100, 2)

        try:
            df = get_historical_candles(sym, "15m")
            if not df.empty:
                from analysis.technicals import add_indicators
                df = add_indicators(df)
            opt_df  = get_option_chain(sym, ltp)
            opt_info = option_score(opt_df, ltp) if not opt_df.empty else {}
        except Exception:
            df = None; opt_info = {}

        result = analyse_holding(holding_dict, df, opt_info)
        result["exchange"] = h.get("exchange", "NSE")
        results.append(result)

    # ── Analyse each option position ──────────────────────────────────────
    for p in positions:
        sym = p.get("tradingsymbol", "")
        qty = int(p.get("quantity", 0) or 0)
        if qty == 0:
            continue
        pos_dict = {
            "symbol":    sym,
            "avg_price": float(p.get("average_price", 0) or 0),
            "ltp":       float(p.get("last_price", 0) or 0),
            "pnl":       float(p.get("pnl", 0) or 0),
            "quantity":  qty,
            "product":   p.get("product", ""),
        }

        # Extract underlying symbol from option name e.g. VEDL26JUN420CE → VEDL
        import re
        underlying = re.match(r"([A-Z]+)\d", sym)
        underlying = underlying.group(1) if underlying else None

        try:
            und_df  = get_historical_candles(underlying, "15m") if underlying else None
            if und_df is not None and not und_df.empty:
                from analysis.technicals import add_indicators
                und_df = add_indicators(und_df)
            und_ltp = float(get_live_quote(underlying).get("ltp", 0)) if underlying else 0
            opt_df  = get_option_chain(underlying, und_ltp) if underlying else None
            opt_info = option_score(opt_df, und_ltp) if (opt_df is not None and not opt_df.empty) else {}
        except Exception:
            und_df = None; opt_info = {}

        result = analyse_position(pos_dict, und_df, opt_info)
        result["exchange"] = p.get("exchange", "NFO")
        results.append(result)

    # Sort: EXIT first, then REVIEW, then BOOK PROFIT, then HOLD, BUY MORE last
    order = {"EXIT": 0, "REVIEW": 1, "BOOK PROFIT": 2, "HOLD": 3, "BUY MORE": 4}
    results.sort(key=lambda x: order.get(x["signal"], 5))

    return jsonify({"status": "ok", "analysis": results,
                    "updated_at": datetime.now(NSE_TZ).strftime("%H:%M:%S IST")})


@app.route("/api/market-bar")
def market_bar():
    """Fast endpoint: Nifty, BankNifty live + US index quotes via Yahoo."""
    import requests, urllib3
    urllib3.disable_warnings()
    from dotenv import load_dotenv; import os; load_dotenv()

    results = {}

    # Indian indices via Upstox
    import data.upstox as _up
    for sym in ["NIFTY", "BANKNIFTY"]:
        q = _up.get_ltp(sym)
        if q:
            results[sym] = {"ltp": q["ltp"], "change": None, "source": "upstox"}

    # US indices via Yahoo Finance (free, delayed)
    us_symbols = {"DOW": "^DJI", "NASDAQ": "^IXIC", "SP500": "^GSPC"}
    for label, yticker in us_symbols.items():
        try:
            url  = f"https://query1.finance.yahoo.com/v8/finance/chart/{yticker}"
            resp = requests.get(url, params={"interval": "1d", "range": "2d"},
                                headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
            meta = resp.json()["chart"]["result"][0]["meta"]
            ltp  = meta.get("regularMarketPrice", 0)
            prev = meta.get("chartPreviousClose",  ltp)
            pct  = round((ltp - prev) / prev * 100, 2) if prev else 0
            results[label] = {"ltp": round(ltp, 2), "change": f"{pct:+.2f}%",
                               "up": pct >= 0, "source": "yahoo"}
        except Exception:
            results[label] = {"ltp": None, "change": None, "source": "error"}

    return jsonify(results)


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
        "change_pct":  q.get("change_pct",  0),
        "volume":      q.get("volume", 0),
        "source":      q.get("source", "unknown"),
        "session":     q.get("session", "unknown"),
        "timestamp":   _iso(q.get("timestamp")),
    })


@app.route("/api/signal", methods=["POST"])
def get_signal():
    body         = request.get_json(silent=True) or {}
    symbol       = body.get("symbol", "VEDL")
    timeframe    = body.get("timeframe", "15m")
    market_score = body.get("market_score", DEFAULT_MARKET_SCORE)

    if symbol not in SYMBOLS:
        return jsonify({"error": "Invalid symbol"}), 400
    if timeframe not in SUPPORTED_TIMEFRAMES:
        return jsonify({"error": "Invalid timeframe"}), 400
    try:
        market_score = min(max(int(market_score), 0), 100)
    except (TypeError, ValueError):
        market_score = DEFAULT_MARKET_SCORE

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
    option_expiry = option_df.attrs.get("expiry", "") if not option_df.empty else ""

    # New AI modules
    atm_iv    = option.get("iv", {}).get("atm_iv")
    vol_data  = classify_volatility(analysis_df, atm_iv)
    pat_data  = detect_patterns(analysis_df)

    news_items  = fetch_google_news(company_name)
    news        = simple_news_score(news_items)

    signal = generate_signal(
        symbol=symbol,
        technical=technical,
        option=option,
        news=news,
        market_score=market_score,
        volatility=vol_data,
        patterns=pat_data,
    )

    levels  = calculate_trade_levels(signal["signal"], technical["latest"], option_df)
    ce_list = recommend_ce_strikes(signal["signal"], spot_price, option_df)

    # AI commentary
    commentary = _ai_commentary(
        signal["signal"], signal["confidence"], technical,
        option, vol_data, pat_data, levels, symbol,
    )

    related_options = []
    for rsym in related_symbols:
        rinfo  = SYMBOLS.get(rsym, {"name": rsym, "sector": sector})
        rquote = get_live_quote(rsym)
        related_options.append(
            _option_summary(rsym, rinfo.get("name", rsym), float(rquote.get("ltp", 0) or 0))
        )

    return jsonify({
        "symbol":       symbol,
        "company_name": company_name,
        "sector":       sector,
        "quote": {
            "ltp":            round(quote.get("ltp", 0.0), 2),
            "change":         quote.get("change_text", "N/A"),
            "change_pct":     quote.get("change_pct", 0),
            "volume":         quote.get("volume", 0),
            "vwap_deviation": round(spot_price - float(technical["latest"].get("vwap", spot_price) or spot_price), 2),
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
            "status":                 signal["signal"],
            "confidence":             signal["confidence"],
            "risk":                   signal["risk"],
            "option_strategy":        signal.get("option_strategy",  "UNKNOWN"),
            "option_bias":            signal.get("option_bias",       "NEUTRAL"),
            "option_trade_permission":signal.get("option_trade_permission", "ALLOW"),
            "invalid_if":             signal.get("invalid_if", ""),
            "volatility_regime":      signal.get("volatility_regime", "normal"),
            "pattern_signal":         signal.get("pattern_signal", ""),
            "weights_used":           signal.get("weights_used", []),
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
        "scores": {
            "technical": signal["technical_score"],
            "options":   signal["option_score"],
            "news":      signal["news_score"],
            "market":    signal["market_score"],
        },
        "technicals": {
            "rsi":        round(float(technical["latest"].get("rsi",    0) or 0), 2),
            "ema_9":      round(float(technical["latest"].get("ema_9",  0) or 0), 2),
            "ema_20":     round(float(technical["latest"].get("ema_20", 0) or 0), 2),
            "macd":       round(float(technical["latest"].get("macd",   0) or 0), 2),
            "atr":        round(float(technical["latest"].get("atr",    0) or 0), 2),
            "last_close": round(float(technical["latest"].get("close",  0) or 0), 2),
            "last_volume":int(float(technical["latest"].get("volume",   0) or 0)),
            "bb_upper":   round(float(technical["latest"].get("bb_upper", 0) or 0), 2),
            "bb_lower":   round(float(technical["latest"].get("bb_lower", 0) or 0), 2),
            "roc_5":      round(float(technical["latest"].get("roc_5",  0) or 0), 2),
        },
        "volatility": {
            "regime":         vol_data.get("regime",         "normal"),
            "atr_pct":        vol_data.get("atr_pct",        0),
            "hv20":           vol_data.get("hv20",           0),
            "percentile":     vol_data.get("percentile",     50),
            "recommendation": vol_data.get("recommendation", ""),
        },
        "patterns": {
            "found":            pat_data.get("patterns",          []),
            "strongest":        pat_data.get("strongest_pattern", ""),
            "direction":        pat_data.get("direction",         "neutral"),
            "score_bonus":      pat_data.get("score_bonus",       0),
            "strong_reversal":  pat_data.get("strong_reversal",   False),
        },
        "options": {
            "pcr":               option.get("pcr",                  "N/A"),
            "max_pain":          option.get("max_pain",              "N/A"),
            "put_oi_support":    option.get("highest_put_oi_strike", "N/A"),
            "call_oi_resistance":option.get("highest_call_oi_strike","N/A"),
            "directional_bias":  option.get("directional_bias",      "NEUTRAL"),
            "trade_permission":  option.get("trade_permission",       "ALLOW"),
            "strategy":          option.get("strategy",               "UNKNOWN"),
            "liquidity":         option.get("liquidity",              {}),
            "iv":                option.get("iv",                     {}),
            "risk_flags":        option.get("risk_flags",             []),
            "source":            option_source,
            "expiry":            option_expiry,
        },
        "related_options": related_options,
        "reasons":         signal["reasons"][:8],
        "news": {
            "sentiment": news.get("sentiment", "neutral"),
            "impact":    news.get("impact",    "low"),
            "items":     news.get("items",     [])[:4],
        },
        "commentary":  commentary,
        "ce_list":     ce_list,
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
            "rsi":        analysis_df["rsi"].tail(60).round(2).tolist(),
            "bb_upper":   analysis_df["bb_upper"].tail(60).round(2).tolist(),
            "bb_lower":   analysis_df["bb_lower"].tail(60).round(2).tolist(),
        },
    })


if __name__ == "__main__":
    app.run(debug=True, port=5000)
