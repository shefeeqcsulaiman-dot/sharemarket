"""
Multi-leg option strategy recognizer.
Detects: Bull Call Spread, Bear Put Spread, Long Straddle/Strangle,
         Call Ladder, Covered Call, and single legs.
Shows combined P&L, max profit, max loss, break-even.
"""


def _parse_strike_type(symbol: str) -> tuple[float, str] | None:
    import re
    m = re.match(r"[A-Z&]+\d{2}[A-Z]{3}(\d+)(CE|PE)", symbol.upper())
    if not m:
        return None
    return float(m.group(1)), m.group(2)


def detect_strategy(positions: list[dict]) -> dict:
    """
    Analyse a list of option positions on the SAME underlying.

    Each position dict needs: symbol, avg_price, ltp, quantity, pnl
    Returns: strategy name, combined metrics, break-evens, max P&L
    """
    if not positions:
        return {"name": "No positions", "legs": 0}

    legs = []
    for p in positions:
        parsed = _parse_strike_type(p["symbol"])
        if not parsed:
            continue
        strike, opt_type = parsed
        legs.append({
            "symbol":    p["symbol"],
            "strike":    strike,
            "type":      opt_type,
            "qty":       int(p.get("quantity", 0) or 0),
            "avg_price": float(p.get("avg_price", 0) or 0),
            "ltp":       float(p.get("ltp", 0) or 0),
            "pnl":       float(p.get("pnl", 0) or 0),
        })

    if not legs:
        return {"name": "Unknown", "legs": 0}

    # Sort by strike
    legs_sorted = sorted(legs, key=lambda x: x["strike"])
    calls = [l for l in legs_sorted if l["type"] == "CE"]
    puts  = [l for l in legs_sorted if l["type"] == "PE"]
    n_calls = len(calls)
    n_puts  = len(puts)

    # Combined P&L
    total_pnl       = sum(l["pnl"] for l in legs)
    total_premium    = sum(l["avg_price"] * l["qty"] for l in legs)
    total_current    = sum(l["ltp"]       * l["qty"] for l in legs)
    net_premium_paid = total_premium   # for all-buy positions

    strategy = "Custom Multi-leg"

    # ── Single leg ────────────────────────────────────────────────────────
    if len(legs) == 1:
        l = legs[0]
        if l["type"] == "CE":
            strategy = "Long Call"
            max_loss   = -l["avg_price"] * l["qty"]
            max_profit = float("inf")
            breakevens = [l["strike"] + l["avg_price"]]
        else:
            strategy = "Long Put"
            max_loss   = -l["avg_price"] * l["qty"]
            max_profit = (l["strike"] - l["avg_price"]) * l["qty"]
            breakevens = [l["strike"] - l["avg_price"]]

    # ── Bull Call Spread: buy lower CE, sell higher CE ────────────────────
    elif n_calls == 2 and n_puts == 0:
        low, high = calls[0], calls[1]
        if low["avg_price"] > 0 and high["avg_price"] > 0:
            net_debit  = low["avg_price"] - high["avg_price"]
            spread     = high["strike"] - low["strike"]
            max_profit = (spread - net_debit) * low["qty"]
            max_loss   = -net_debit * low["qty"]
            breakevens = [low["strike"] + net_debit]
            strategy   = "Bull Call Spread"
        else:
            strategy = "Multi-leg Call"
            max_profit = max_loss = None
            breakevens = []

    # ── Call Ladder: 3 calls at different strikes ─────────────────────────
    elif n_calls == 3 and n_puts == 0:
        strategy   = "Call Ladder"
        max_profit = None
        max_loss   = -net_premium_paid
        breakevens = [c["strike"] + c["avg_price"] for c in calls]

    # ── Bear Put Spread: buy higher PE, sell lower PE ─────────────────────
    elif n_puts == 2 and n_calls == 0:
        low, high = puts[0], puts[1]
        net_debit  = high["avg_price"] - low["avg_price"]
        spread     = high["strike"]    - low["strike"]
        max_profit = (spread - net_debit) * high["qty"]
        max_loss   = -net_debit * high["qty"]
        breakevens = [high["strike"] - net_debit]
        strategy   = "Bear Put Spread"

    # ── Long Straddle: same strike CE + PE ────────────────────────────────
    elif n_calls == 1 and n_puts == 1 and abs(calls[0]["strike"] - puts[0]["strike"]) < 5:
        net_debit  = calls[0]["avg_price"] + puts[0]["avg_price"]
        strike     = calls[0]["strike"]
        max_loss   = -net_debit * calls[0]["qty"]
        max_profit = float("inf")
        breakevens = [strike - net_debit, strike + net_debit]
        strategy   = "Long Straddle"

    # ── Long Strangle: different strike CE + PE ───────────────────────────
    elif n_calls == 1 and n_puts == 1:
        net_debit  = calls[0]["avg_price"] + puts[0]["avg_price"]
        max_loss   = -net_debit * calls[0]["qty"]
        max_profit = float("inf")
        breakevens = [
            puts[0]["strike"]  - net_debit,
            calls[0]["strike"] + net_debit,
        ]
        strategy = "Long Strangle"

    else:
        max_profit = None
        max_loss   = None
        breakevens = []

    return {
        "name":           strategy,
        "legs":           len(legs),
        "total_pnl":      round(total_pnl, 2),
        "total_premium":  round(total_premium, 2),
        "total_current":  round(total_current, 2),
        "max_profit":     round(max_profit, 2) if max_profit not in (None, float("inf")) else "Unlimited",
        "max_loss":       round(max_loss, 2) if max_loss is not None else None,
        "breakevens":     [round(b, 2) for b in breakevens],
        "calls":          n_calls,
        "puts":           n_puts,
        "leg_details":    legs,
    }


def group_by_underlying(positions: list[dict]) -> dict[str, list[dict]]:
    """Group positions by underlying symbol."""
    import re
    groups: dict[str, list] = {}
    for p in positions:
        m = re.match(r"([A-Z&]+)\d{2}[A-Z]{3}", p.get("symbol", "").upper())
        und = m.group(1) if m else "OTHER"
        groups.setdefault(und, []).append(p)
    return groups
