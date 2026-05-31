"""
Portfolio Option Analyst — deep per-position analysis for CE/PE trades.
Checks: technicals, option chain OI, IV, DTE, premium decay, moneyness.
Returns signal: HOLD / ADD MORE / PARTIAL EXIT / EXIT NOW / ROLL
"""

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from analysis.greeks import calculate_greeks, theta_decay_forecast

NSE_TZ = ZoneInfo("Asia/Kolkata")

MONTH_MAP = {
    "JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,
    "JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12
}


def parse_option_symbol(sym: str, chain_expiry: str | None = None) -> dict:
    """
    Parse NSE option symbol like VEDL26JUN420CE.

    NSE monthly format: {UNDERLYING}{YY}{MMM}{STRIKE}{CE|PE}
      VEDL26JUN420CE  → VEDL, year=2026, month=JUN, strike=420, CE
      YY = 2-digit year, NOT day of month.

    Expiry date: use chain_expiry (from Upstox) if given, else last Thursday of month.
    """
    sym = sym.upper().strip()
    m = re.match(
        r"([A-Z&]+)(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d+)(CE|PE)", sym
    )
    if not m:
        return {"underlying": sym, "strike": 0, "option_type": "CE",
                "expiry_str": "", "expiry_date": None, "dte": None}

    underlying  = m.group(1)
    year        = 2000 + int(m.group(2))          # "26" → 2026
    month       = MONTH_MAP[m.group(3)]
    strike      = float(m.group(4))
    option_type = m.group(5)

    # Determine expiry date
    expiry_date = None
    if chain_expiry:
        # chain_expiry from Upstox: "2026-06-30"
        try:
            expiry_date = datetime.strptime(chain_expiry, "%Y-%m-%d").replace(tzinfo=NSE_TZ)
        except Exception:
            pass

    if expiry_date is None:
        # Fallback: last Thursday of the month
        import calendar
        last_day = calendar.monthrange(year, month)[1]
        thursdays = [d for d in range(1, last_day + 1)
                     if datetime(year, month, d).weekday() == 3]
        expiry_day  = thursdays[-1] if thursdays else last_day
        expiry_date = datetime(year, month, expiry_day, tzinfo=NSE_TZ)

    dte = max(0, (expiry_date.date() - datetime.now(NSE_TZ).date()).days)

    return {
        "underlying":  underlying,
        "strike":      strike,
        "option_type": option_type,
        "expiry_str":  expiry_date.strftime("%d-%b-%Y"),
        "expiry_date": expiry_date,
        "dte":         dte,
    }


def _chain_for_strike(option_df, strike: float) -> dict:
    """Extract chain data for a specific strike row."""
    if option_df is None or option_df.empty:
        return {}
    matches = option_df[abs(option_df["strike"].astype(float) - strike) < 0.5]
    if matches.empty:
        return {}
    row = matches.iloc[0]
    return {
        "call_oi":        int(row.get("call_oi", 0) or 0),
        "put_oi":         int(row.get("put_oi",  0) or 0),
        "call_change_oi": int(row.get("call_change_oi", 0) or 0),
        "put_change_oi":  int(row.get("put_change_oi",  0) or 0),
        "call_iv":        float(row.get("call_iv", 0) or 0),
        "put_iv":         float(row.get("put_iv",  0) or 0),
        "call_ltp":       float(row.get("call_ltp", 0) or 0),
        "put_ltp":        float(row.get("put_ltp",  0) or 0),
        "call_bid":       float(row.get("call_bid", 0) or 0),
        "call_ask":       float(row.get("call_ask", 0) or 0),
        "put_bid":        float(row.get("put_bid",  0) or 0),
        "put_ask":        float(row.get("put_ask",  0) or 0),
    }


def analyse_option_position(position: dict, spot: float,
                             candles_df, option_df,
                             option_chain_summary: dict,
                             chain_expiry: str | None = None) -> dict:
    """
    Full deep analysis for one option position.
    """
    sym        = position["symbol"]
    avg_price  = float(position["avg_price"] or 0)
    ltp        = float(position["ltp"] or 0)
    qty        = int(position["quantity"] or 0)
    pnl        = float(position["pnl"] or 0)
    prev_close = float(position.get("close_price", ltp) or ltp)

    parsed     = parse_option_symbol(sym, chain_expiry=chain_expiry)
    underlying = parsed["underlying"]
    strike     = parsed["strike"]
    opt_type   = parsed["option_type"]   # CE or PE
    dte        = parsed["dte"]
    is_call    = opt_type == "CE"

    score      = 50
    reasons    = []
    signals    = []

    # ── 1. MONEYNESS ──────────────────────────────────────────────────────
    moneyness_pct = ((strike - spot) / spot * 100) if spot > 0 else 0
    if is_call:
        dist_from_spot = strike - spot   # positive = OTM, negative = ITM
    else:
        dist_from_spot = spot - strike   # positive = OTM, negative = ITM

    if abs(dist_from_spot) <= spot * 0.01:
        moneyness_label = "ATM"
        score += 8
    elif dist_from_spot < 0:
        pct_itm = abs(dist_from_spot) / spot * 100
        moneyness_label = f"ITM {pct_itm:.1f}%"
        score += 5
    else:
        pct_otm = dist_from_spot / spot * 100
        moneyness_label = f"OTM {pct_otm:.1f}%"
        if pct_otm > 15:
            score -= 20
            reasons.append(f"Deep OTM ({pct_otm:.1f}% away) — low probability of profit")
        elif pct_otm > 8:
            score -= 10
            reasons.append(f"OTM {pct_otm:.1f}% from spot {spot} — needs strong move")
        else:
            reasons.append(f"Near OTM {pct_otm:.1f}% — within range")

    # Break-even price for calls: strike + avg_premium
    if is_call:
        breakeven = strike + avg_price
        be_gap_pct = (breakeven - spot) / spot * 100
        reasons.append(f"Break-even: Rs{breakeven:.1f} (spot needs +{be_gap_pct:.1f}%)")
    else:
        breakeven = strike - avg_price
        be_gap_pct = (spot - breakeven) / spot * 100
        reasons.append(f"Break-even: Rs{breakeven:.1f} (spot needs -{be_gap_pct:.1f}%)")

    # ── 2. PREMIUM DECAY ──────────────────────────────────────────────────
    if avg_price > 0:
        decay_pct = (ltp - avg_price) / avg_price * 100
        if decay_pct >= 50:
            score += 18
            signals.append(f"BOOK PROFIT: premium up {decay_pct:.0f}%")
        elif decay_pct >= 25:
            score += 10
            signals.append(f"Partial exit: +{decay_pct:.0f}% profit")
        elif decay_pct <= -60:
            score -= 25
            signals.append(f"Exit: premium lost {abs(decay_pct):.0f}%")
        elif decay_pct <= -35:
            score -= 15
            signals.append(f"Review: premium down {abs(decay_pct):.0f}%")
        else:
            reasons.append(f"Premium: Rs{avg_price} → Rs{ltp} ({decay_pct:+.0f}%)")

    # Daily change
    if prev_close > 0 and ltp != prev_close:
        day_chg = (ltp - prev_close) / prev_close * 100
        reasons.append(f"Today: {day_chg:+.1f}% (prev close Rs{prev_close})")

    # ── 3. DAYS TO EXPIRY ─────────────────────────────────────────────────
    if dte is not None:
        if dte == 0:
            score -= 40
            signals.append("EXIT IMMEDIATELY — expires today!")
        elif dte <= 3:
            score -= 25
            signals.append(f"URGENT: only {dte} days to expiry — theta killing premium")
        elif dte <= 7:
            score -= 12
            reasons.append(f"{dte} days to expiry — theta decay accelerating fast")
        elif dte <= 15:
            score -= 5
            reasons.append(f"{dte} days to expiry — monitor theta")
        else:
            score += 5
            reasons.append(f"{dte} days to expiry — time value intact")

    # ── 4. UNDERLYING TECHNICALS ──────────────────────────────────────────
    tech_summary = {}
    if candles_df is not None and not candles_df.empty and "rsi" in candles_df.columns:
        latest   = candles_df.iloc[-1]
        rsi      = float(latest.get("rsi",    50) or 50)
        ema9     = float(latest.get("ema_9",   spot) or spot)
        ema20    = float(latest.get("ema_20",  spot) or spot)
        vwap     = float(latest.get("vwap",    spot) or spot)
        atr      = float(latest.get("atr",     0)    or 0)
        macd     = float(latest.get("macd",    0)    or 0)
        macd_sig = float(latest.get("macd_signal", 0) or 0)

        tech_summary = {
            "rsi": round(rsi, 1), "ema9": round(ema9, 2),
            "ema20": round(ema20, 2), "vwap": round(vwap, 2),
            "atr": round(atr, 2), "macd": round(macd, 2),
        }

        # For CE (call): bullish technicals = good
        if is_call:
            if ema9 > ema20:
                score += 12
                reasons.append(f"Underlying: EMA 9 above EMA 20 — uptrend ({underlying})")
            else:
                score -= 10
                reasons.append(f"Underlying: EMA 9 below EMA 20 — downtrend, CE at risk")

            if spot > vwap:
                score += 8
                reasons.append(f"Underlying above VWAP Rs{vwap:.1f} — bullish intraday")
            else:
                score -= 6
                reasons.append(f"Underlying below VWAP Rs{vwap:.1f} — bearish intraday")

            if rsi > 60:
                score += 6
                reasons.append(f"RSI {rsi} — bullish momentum supports CE")
            elif rsi > 70:
                score -= 8
                reasons.append(f"RSI {rsi} — overbought, rally may stall")
            elif rsi < 35:
                score -= 12
                reasons.append(f"RSI {rsi} — weak, CE under pressure")

            if macd > macd_sig:
                score += 6
                reasons.append("MACD bullish crossover on underlying")
        else:  # PE
            if ema9 < ema20:
                score += 12
                reasons.append(f"Underlying: EMA9 below EMA20 — downtrend supports PE")
            else:
                score -= 10
                reasons.append(f"Underlying: EMA9 above EMA20 — uptrend, PE at risk")
            if rsi < 40:
                score += 8
                reasons.append(f"RSI {rsi} — weak momentum supports PE")
            elif rsi > 65:
                score -= 12
                reasons.append(f"RSI {rsi} — strong, PE under pressure")

        # ATR-based target for call
        if atr > 0 and is_call:
            tgt_spot  = spot + 2 * atr
            tgt_prem  = max(ltp * 1.3, ltp + 0.5)
            reasons.append(f"If {underlying} reaches Rs{tgt_spot:.1f} (+2xATR), target premium ~Rs{tgt_prem:.1f}")

    # ── 5. OPTION CHAIN DATA FOR THIS STRIKE ─────────────────────────────
    chain = _chain_for_strike(option_df, strike)
    chain_reasons = []
    if chain:
        oi_key  = "call_oi"  if is_call else "put_oi"
        coi_key = "call_change_oi" if is_call else "put_change_oi"
        iv_key  = "call_iv"  if is_call else "put_iv"
        bid_key = "call_bid" if is_call else "put_bid"
        ask_key = "call_ask" if is_call else "put_ask"

        oi      = chain.get(oi_key, 0)
        chg_oi  = chain.get(coi_key, 0)
        iv      = chain.get(iv_key, 0)
        bid     = chain.get(bid_key, 0)
        ask     = chain.get(ask_key, 0)
        spread  = round((ask - bid) / ((ask + bid) / 2) * 100, 1) if (ask + bid) > 0 else 99

        if chg_oi > 0:
            score += 8
            chain_reasons.append(f"OI building at {strike} CE (+{chg_oi:,}) — fresh buying interest")
        elif chg_oi < 0:
            score -= 8
            chain_reasons.append(f"OI unwinding at {strike} CE ({chg_oi:,}) — exits happening")

        if iv > 0:
            if iv > 45:
                chain_reasons.append(f"IV {iv}% — elevated, premium may compress on reversal")
            elif iv > 30:
                chain_reasons.append(f"IV {iv}% — normal range")
            else:
                chain_reasons.append(f"IV {iv}% — low, options cheap")

        if spread > 5:
            score -= 5
            chain_reasons.append(f"Wide bid-ask spread {spread}% — exit may be costly")
        elif spread < 2:
            chain_reasons.append(f"Tight spread {spread}% — liquid, easy exit")

        if bid > 0:
            chain_reasons.append(f"Live: Bid Rs{bid} / Ask Rs{ask} (OI: {oi:,})")

    # ── 6. OPTION CHAIN SUMMARY (PCR, MAX PAIN) ───────────────────────────
    pcr       = option_chain_summary.get("pcr", 1.0) or 1.0
    max_pain  = option_chain_summary.get("max_pain", 0) or 0
    put_wall  = option_chain_summary.get("highest_put_oi_strike", 0) or 0
    call_wall = option_chain_summary.get("highest_call_oi_strike", 0) or 0
    bias      = option_chain_summary.get("directional_bias", "NEUTRAL")

    if is_call:
        if float(pcr) >= 1.0:
            score += 8
            reasons.append(f"PCR {pcr} — put-heavy, supports upside (good for CE)")
        else:
            score -= 8
            reasons.append(f"PCR {pcr} — call-heavy, bears in control (CE headwind)")

        if call_wall > 0 and strike >= call_wall:
            score -= 12
            reasons.append(f"Your strike {strike} >= Call OI wall {call_wall} — massive resistance")
        elif call_wall > 0:
            reasons.append(f"Call OI wall at {call_wall} — resistance above your strike")

        if max_pain > 0 and spot < max_pain:
            score += 5
            reasons.append(f"Spot below max pain Rs{max_pain} — pin risk upward")
        elif max_pain > 0:
            reasons.append(f"Max pain Rs{max_pain} — spot above, CE favoured")

    if bias == "BULLISH" and is_call:
        score += 10
        reasons.append("Option chain: BULLISH bias — supports CE")
    elif bias == "BEARISH" and is_call:
        score -= 12
        reasons.append("Option chain: BEARISH — CE under pressure")
    elif bias == "BULLISH_BREAKOUT" and is_call:
        score += 15
        reasons.append("Option chain: BREAKOUT signal — CE momentum play")

    # ── 7. FINAL SIGNAL ───────────────────────────────────────────────────
    final_reasons = signals + reasons + chain_reasons

    if score >= 68:
        signal   = "ADD MORE"
        action   = f"Strong setup — consider adding at Rs{ltp:.2f} if premium holds"
        color    = "emerald"
    elif score >= 52:
        signal   = "HOLD"
        action   = "Position intact — hold and monitor"
        color    = "indigo"
    elif score >= 38:
        signal   = "PARTIAL EXIT"
        action   = f"Reduce 50% at Rs{ltp:.2f}, keep rest for potential recovery"
        color    = "amber"
    elif score >= 25:
        signal   = "EXIT"
        action   = f"Exit at market (bid Rs{chain.get('call_bid' if is_call else 'put_bid', ltp):.2f}) — risk outweighs reward"
        color    = "orange"
    else:
        signal   = "EXIT NOW"
        action   = f"Exit immediately — position deteriorating fast"
        color    = "red"

    # Hard overrides from urgent signals
    if any("EXIT IMMEDIATELY" in s for s in signals):
        signal = "EXIT NOW"; color = "red"
        action = "Expires today — sell at any price"
    elif any("BOOK PROFIT" in s for s in signals):
        signal = "BOOK PROFIT"; color = "emerald"
        action = f"Take full profits at current premium Rs{ltp:.2f}"

    # ── Greeks ───────────────────────────────────────────────────────────
    iv_val = chain.get("call_iv" if is_call else "put_iv", 0) or 0
    greeks = calculate_greeks(spot, strike, dte or 30, iv_val, opt_type)
    theta_forecast = theta_decay_forecast(greeks.get("theta"), dte or 30, ltp)

    # SL / Target levels for this position
    sl_level     = round(avg_price * 0.50, 2) if avg_price > 0 else None   # 50% loss on premium
    target_level = round(avg_price * 1.75, 2) if avg_price > 0 else None   # 75% gain on premium

    return {
        "symbol":         sym,
        "underlying":     underlying,
        "strike":         strike,
        "option_type":    opt_type,
        "dte":            dte,
        "expiry_str":     parsed["expiry_str"],
        "moneyness":      moneyness_label,
        "signal":         signal,
        "color":          color,
        "score":          min(max(score, 0), 100),
        "action":         action,
        "pnl":            round(pnl, 2),
        "avg_price":      avg_price,
        "ltp":            ltp,
        "prev_close":     prev_close,
        "qty":            qty,
        "spot":           spot,
        "breakeven":      round(breakeven, 2) if avg_price > 0 else None,
        "sl_level":       sl_level,
        "target_level":   target_level,
        "reasons":        final_reasons[:6],
        "chain":          chain,
        "technicals":     tech_summary,
        "greeks":         greeks,
        "theta_forecast": theta_forecast,
        "pcr":            round(float(pcr), 2),
        "max_pain":       round(float(max_pain), 2) if max_pain else None,
        "call_wall":      call_wall,
        "put_wall":       put_wall,
        "chain_bias":     bias,
        "updated_at":     datetime.now(NSE_TZ).strftime("%H:%M:%S"),
    }
