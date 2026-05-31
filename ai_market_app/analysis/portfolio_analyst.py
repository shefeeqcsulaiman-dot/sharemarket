"""
Portfolio AI Analyst — generates BUY MORE / HOLD / EXIT signals
for each holding and open position based on live technicals.
"""

import re
from datetime import datetime
from zoneinfo import ZoneInfo

NSE_TZ = ZoneInfo("Asia/Kolkata")


def _days_to_expiry(trading_symbol: str) -> int | None:
    """
    Parse NSE option symbol like VEDL26JUN420CE → expiry date.
    Returns days remaining, or None if can't parse.
    """
    m = re.search(r"(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})", trading_symbol.upper())
    if not m:
        return None
    month_map = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,
                 "JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12}
    try:
        day   = int(m.group(1))
        month = month_map[m.group(2)]
        year  = 2000 + int(m.group(3))
        expiry_dt = datetime(year, month, day, tzinfo=NSE_TZ)
        now       = datetime.now(NSE_TZ)
        return max(0, (expiry_dt.date() - now.date()).days)
    except Exception:
        return None


def analyse_holding(holding: dict, candles_df, option_data: dict | None = None) -> dict:
    """
    Generate a HOLD/BUY MORE/EXIT signal for a long-term holding.
    Uses technicals from candle data + P&L context.
    """
    symbol    = holding["symbol"]
    avg_price = float(holding["avg_price"] or 0)
    ltp       = float(holding["ltp"] or 0)
    pnl_pct   = float(holding["pnl_pct"] or 0)
    pnl       = float(holding["pnl"] or 0)

    signal    = "HOLD"
    reasons   = []
    score     = 50   # 0-100, above 60 = BUY MORE, below 35 = EXIT

    # ── P&L context ────────────────────────────────────────────────────────
    if pnl_pct >= 20:
        score -= 15
        reasons.append(f"Up {pnl_pct}% — consider booking partial profits")
    elif pnl_pct >= 10:
        score -= 5
        reasons.append(f"Good gain of {pnl_pct}% — trail stop-loss")
    elif pnl_pct <= -15:
        score -= 20
        reasons.append(f"Down {pnl_pct}% — significant loss, review thesis")
    elif pnl_pct <= -8:
        score -= 10
        reasons.append(f"Loss of {pnl_pct}% — watch carefully")
    else:
        reasons.append(f"P&L: {pnl_pct:+.1f}% (Rs{pnl:+.0f})")

    # ── Technical analysis ──────────────────────────────────────────────────
    if candles_df is not None and not candles_df.empty and "rsi" in candles_df.columns:
        latest = candles_df.iloc[-1]
        rsi    = float(latest.get("rsi", 50) or 50)
        ema9   = float(latest.get("ema_9", ltp) or ltp)
        ema20  = float(latest.get("ema_20", ltp) or ltp)
        vwap   = float(latest.get("vwap", ltp) or ltp)

        if rsi > 72:
            score -= 12
            reasons.append(f"RSI {rsi:.0f} — overbought, risk of pullback")
        elif rsi < 30:
            score += 10
            reasons.append(f"RSI {rsi:.0f} — oversold, potential bounce")
        elif rsi > 55:
            score += 8
            reasons.append(f"RSI {rsi:.0f} — bullish momentum")

        if ema9 > ema20:
            score += 10
            reasons.append("EMA 9 above EMA 20 — uptrend intact")
        else:
            score -= 8
            reasons.append("EMA 9 below EMA 20 — short-term weakness")

        if ltp > vwap:
            score += 6
            reasons.append("Trading above VWAP — intraday bulls in control")
        else:
            score -= 4

    # ── Option bias overlay ──────────────────────────────────────────────
    if option_data:
        bias = option_data.get("directional_bias", "")
        if bias == "BULLISH":
            score += 8
            reasons.append("Option chain: bullish (PCR supportive)")
        elif bias == "BEARISH":
            score -= 10
            reasons.append("Option chain: bearish (PCR weak)")
        elif bias == "BULLISH_BREAKOUT":
            score += 12
            reasons.append("Option chain: breakout setup active")

    # ── Final signal ─────────────────────────────────────────────────────
    if score >= 62:
        signal = "BUY MORE"
    elif score >= 45:
        signal = "HOLD"
    else:
        signal = "EXIT"

    return {
        "symbol":     symbol,
        "type":       "holding",
        "signal":     signal,
        "score":      min(max(score, 0), 100),
        "pnl":        round(pnl, 2),
        "pnl_pct":    round(pnl_pct, 2),
        "avg_price":  avg_price,
        "ltp":        ltp,
        "qty":        holding["quantity"],
        "reasons":    reasons[:4],
        "updated_at": datetime.now(NSE_TZ).strftime("%H:%M:%S"),
    }


def analyse_position(position: dict, underlying_candles=None, option_data: dict | None = None) -> dict:
    """
    Generate EXIT / HOLD / AVERAGE signal for an open CE/PE option position.
    Considers days to expiry, premium decay, and underlying direction.
    """
    symbol    = position["symbol"]
    avg_price = float(position["avg_price"] or 0)
    ltp       = float(position["ltp"] or 0)
    qty       = int(position["quantity"] or 0)
    pnl       = float(position["pnl"] or 0)

    signal  = "HOLD"
    reasons = []
    score   = 50

    # ── Premium decay ──────────────────────────────────────────────────────
    if avg_price > 0:
        decay_pct = ((ltp - avg_price) / avg_price) * 100
        if decay_pct >= 40:
            score += 15
            reasons.append(f"Premium up {decay_pct:.0f}% — consider booking profits")
        elif decay_pct <= -40:
            score -= 20
            reasons.append(f"Premium decayed {abs(decay_pct):.0f}% — exit to cut losses")
        elif decay_pct <= -60:
            score -= 30
            reasons.append(f"Premium lost {abs(decay_pct):.0f}% — likely worthless")
        else:
            reasons.append(f"Premium: Rs{avg_price} -> Rs{ltp} ({decay_pct:+.0f}%)")

    # ── Days to expiry ────────────────────────────────────────────────────
    dte = _days_to_expiry(symbol)
    if dte is not None:
        if dte <= 3:
            score -= 25
            reasons.append(f"Only {dte} day(s) to expiry — theta decay critical, exit or roll")
        elif dte <= 7:
            score -= 10
            reasons.append(f"{dte} days to expiry — time decay accelerating")
        else:
            reasons.append(f"{dte} days to expiry — time value intact")

    # ── Underlying technicals ─────────────────────────────────────────────
    is_call = "CE" in symbol.upper()
    is_put  = "PE" in symbol.upper()

    if underlying_candles is not None and not underlying_candles.empty and "rsi" in underlying_candles.columns:
        latest = underlying_candles.iloc[-1]
        rsi    = float(latest.get("rsi", 50) or 50)
        ema9   = float(latest.get("ema_9", 0) or 0)
        ema20  = float(latest.get("ema_20", 0) or 0)

        if is_call:
            if ema9 > ema20 and rsi > 50:
                score += 12
                reasons.append("Underlying trending up — supports CE position")
            elif ema9 < ema20 or rsi < 40:
                score -= 12
                reasons.append("Underlying weakening — CE at risk")
        elif is_put:
            if ema9 < ema20 and rsi < 50:
                score += 12
                reasons.append("Underlying falling — supports PE position")
            elif ema9 > ema20 or rsi > 60:
                score -= 12
                reasons.append("Underlying rallying — PE at risk")

    # ── Option chain bias ─────────────────────────────────────────────────
    if option_data:
        bias = option_data.get("directional_bias", "")
        if is_call:
            if bias in ("BULLISH", "BULLISH_BREAKOUT"):
                score += 8
                reasons.append("Option chain bullish — supports CE")
            elif bias == "BEARISH":
                score -= 8
                reasons.append("Option chain bearish — CE headwind")
        elif is_put:
            if bias == "BEARISH":
                score += 8
            elif bias in ("BULLISH", "BULLISH_BREAKOUT"):
                score -= 8

    # ── P&L absolute ─────────────────────────────────────────────────────
    if pnl > 0:
        reasons.append(f"Current P&L: +Rs{pnl:.0f} (in profit)")
    else:
        reasons.append(f"Current P&L: Rs{pnl:.0f}")

    # ── Signal ──────────────────────────────────────────────────────────
    if score >= 62:
        signal = "HOLD"
    elif score >= 45:
        signal = "HOLD"
    elif score >= 30:
        signal = "REVIEW"
    else:
        signal = "EXIT"

    # Override: if big profit, suggest booking
    if avg_price > 0 and ltp > avg_price * 1.5:
        signal = "BOOK PROFIT"
        reasons.insert(0, f"Premium 50%+ above avg — book profits now")

    return {
        "symbol":     symbol,
        "type":       "option",
        "signal":     signal,
        "score":      min(max(score, 0), 100),
        "pnl":        round(pnl, 2),
        "avg_price":  avg_price,
        "ltp":        ltp,
        "qty":        qty,
        "dte":        dte,
        "is_call":    is_call,
        "reasons":    reasons[:4],
        "updated_at": datetime.now(NSE_TZ).strftime("%H:%M:%S"),
    }
