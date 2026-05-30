"""
Candlestick pattern AI detector.
Identifies bullish/bearish reversal and continuation patterns from OHLCV data.
Returns a score bonus, direction, and named patterns found.
"""

import pandas as pd


def _body(row) -> float:
    return abs(float(row["close"]) - float(row["open"]))


def _range(row) -> float:
    return float(row["high"]) - float(row["low"])


def _is_bullish(row) -> bool:
    return float(row["close"]) >= float(row["open"])


def _upper_wick(row) -> float:
    return float(row["high"]) - max(float(row["open"]), float(row["close"]))


def _lower_wick(row) -> float:
    return min(float(row["open"]), float(row["close"])) - float(row["low"])


def detect(df: pd.DataFrame) -> dict:
    """
    Detect candlestick patterns on the last 5 bars.
    Returns: score_bonus, direction, strongest_pattern, patterns (list), reasons
    """
    if len(df) < 5:
        return _empty()

    c  = df.iloc[-1]
    p1 = df.iloc[-2]
    p2 = df.iloc[-3]

    found     = []
    direction = "neutral"
    bonus     = 0

    body_c  = _body(c)
    rng_c   = _range(c)
    avg_rng = _range(c)  # will be replaced below
    if rng_c == 0:
        return _empty()

    # Average range of last 10 bars (for relative size comparison)
    look = df.tail(10)
    avg_rng = look.apply(_range, axis=1).mean() or rng_c

    # ── SINGLE-BAR PATTERNS ────────────────────────────────────────────────

    # Doji (body < 10% of range)
    if body_c < rng_c * 0.10 and rng_c > avg_rng * 0.5:
        found.append(("Doji", "neutral", 1))

    # Hammer (bullish reversal): small body at top, long lower wick
    lw = _lower_wick(c)
    uw = _upper_wick(c)
    if (lw > body_c * 2 and uw < body_c * 0.5 and
            _is_bullish(c) and rng_c > avg_rng * 0.6):
        found.append(("Hammer", "bullish", 5))

    # Shooting Star (bearish reversal): small body at bottom, long upper wick
    if (uw > body_c * 2 and lw < body_c * 0.5 and
            not _is_bullish(c) and rng_c > avg_rng * 0.6):
        found.append(("Shooting Star", "bearish", 5))

    # Marubozu (strong momentum): body > 85% of range
    if body_c > rng_c * 0.85 and rng_c > avg_rng * 0.8:
        if _is_bullish(c):
            found.append(("Bullish Marubozu", "bullish", 6))
        else:
            found.append(("Bearish Marubozu", "bearish", 6))

    # ── TWO-BAR PATTERNS ──────────────────────────────────────────────────

    body_p1 = _body(p1)

    # Bullish Engulfing
    if (not _is_bullish(p1) and _is_bullish(c) and
            float(c["close"]) > float(p1["open"]) and
            float(c["open"])  < float(p1["close"]) and
            body_c > body_p1 * 1.1):
        found.append(("Bullish Engulfing", "bullish", 8))

    # Bearish Engulfing
    if (_is_bullish(p1) and not _is_bullish(c) and
            float(c["close"]) < float(p1["open"]) and
            float(c["open"])  > float(p1["close"]) and
            body_c > body_p1 * 1.1):
        found.append(("Bearish Engulfing", "bearish", 8))

    # Piercing Line (bullish reversal after downtrend)
    if (not _is_bullish(p1) and _is_bullish(c) and
            float(c["open"]) < float(p1["close"]) and
            float(c["close"]) > (float(p1["open"]) + float(p1["close"])) / 2):
        found.append(("Piercing Line", "bullish", 6))

    # Dark Cloud Cover (bearish reversal after uptrend)
    if (_is_bullish(p1) and not _is_bullish(c) and
            float(c["open"]) > float(p1["close"]) and
            float(c["close"]) < (float(p1["open"]) + float(p1["close"])) / 2):
        found.append(("Dark Cloud Cover", "bearish", 6))

    # ── THREE-BAR PATTERNS ────────────────────────────────────────────────

    body_p2 = _body(p2)

    # Morning Star (bullish reversal)
    if (not _is_bullish(p2) and body_p2 > avg_rng * 0.5 and
            _body(p1) < avg_rng * 0.3 and
            _is_bullish(c) and body_c > avg_rng * 0.5 and
            float(c["close"]) > (float(p2["open"]) + float(p2["close"])) / 2):
        found.append(("Morning Star", "bullish", 9))

    # Evening Star (bearish reversal)
    if (_is_bullish(p2) and body_p2 > avg_rng * 0.5 and
            _body(p1) < avg_rng * 0.3 and
            not _is_bullish(c) and body_c > avg_rng * 0.5 and
            float(c["close"]) < (float(p2["open"]) + float(p2["close"])) / 2):
        found.append(("Evening Star", "bearish", 9))

    # Three White Soldiers (strong bullish)
    if (all(_is_bullish(df.iloc[-i]) for i in range(1, 4)) and
            all(_body(df.iloc[-i]) > avg_rng * 0.5 for i in range(1, 4))):
        found.append(("Three White Soldiers", "bullish", 10))

    # Three Black Crows (strong bearish)
    if (all(not _is_bullish(df.iloc[-i]) for i in range(1, 4)) and
            all(_body(df.iloc[-i]) > avg_rng * 0.5 for i in range(1, 4))):
        found.append(("Three Black Crows", "bearish", 10))

    # ── Summarise ─────────────────────────────────────────────────────────
    if not found:
        return _empty()

    # Pick strongest pattern
    found.sort(key=lambda x: x[2], reverse=True)
    strongest      = found[0]
    direction      = strongest[1]
    bonus          = sum(p[2] for p in found[:3])  # top 3 bonuses
    strong_rev     = strongest[2] >= 8

    # Compute dominant direction
    bull_score = sum(p[2] for p in found if p[1] == "bullish")
    bear_score = sum(p[2] for p in found if p[1] == "bearish")
    if bull_score > bear_score:
        direction = "bullish"
        if bear_score > 0:
            bonus = bonus * 0.6  # conflicting signals → reduce bonus
    elif bear_score > bull_score:
        direction = "bearish"
        if bull_score > 0:
            bonus = bonus * 0.6

    pattern_names = [p[0] for p in found]
    reasons = []
    for name, dir_, sc in found[:3]:
        tag = "[BULL]" if dir_ == "bullish" else "[BEAR]" if dir_ == "bearish" else "[NEUT]"
        reasons.append(f"{tag} Pattern: {name} — {dir_} (strength {sc}/10)")

    return {
        "patterns":          pattern_names,
        "strongest_pattern": strongest[0],
        "direction":         direction,
        "score_bonus":       round(min(bonus, 15), 1),
        "strong_reversal":   strong_rev,
        "reasons":           reasons,
    }


def _empty() -> dict:
    return {
        "patterns": [], "strongest_pattern": "",
        "direction": "neutral", "score_bonus": 0,
        "strong_reversal": False, "reasons": [],
    }
