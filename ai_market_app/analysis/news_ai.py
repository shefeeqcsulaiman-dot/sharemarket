"""
News sentiment AI — upgraded with negation detection, recency weighting,
source credibility scoring, and event impact classification.
"""

from datetime import datetime, timezone
import re


# Positive / negative keyword sets with weights
_POS = {
    "profit": 2, "growth": 2, "revenue": 2, "beat": 3, "upgrade": 2,
    "expansion": 2, "order": 1, "approval": 2, "rally": 1, "gain": 1,
    "strong": 1, "record": 2, "dividend": 1, "buyback": 2, "outperform": 2,
    "deal": 1, "launch": 1, "partnership": 1, "win": 1, "increase": 1,
    "raise": 1, "boost": 1, "positive": 1, "bullish": 2, "breakout": 2,
}

_NEG = {
    "loss": 3, "penalty": 2, "debt": 2, "downgrade": 3, "fall": 1,
    "decline": 2, "fraud": 3, "investigation": 2, "weak": 2, "miss": 2,
    "default": 3, "lawsuit": 2, "fine": 2, "cut": 2, "reduce": 1,
    "bearish": 2, "concern": 1, "risk": 1, "delay": 1, "halt": 2,
    "suspend": 2, "resign": 2, "warning": 2, "negative": 1, "crash": 3,
}

# Words that flip sentiment when they appear before a keyword (negation)
_NEGATORS = {"no", "not", "never", "without", "cleared", "despite", "rejects",
             "denies", "avoids", "reverses", "no longer", "against"}

# High-credibility source domains
_CREDIBLE_SOURCES = {
    "nseindia.com": 1.4, "moneycontrol.com": 1.3, "economictimes.com": 1.2,
    "livemint.com": 1.2, "businessstandard.com": 1.2, "reuters.com": 1.4,
    "bloomberg.com": 1.4, "cnbctv18.com": 1.1, "zeebiz.com": 1.0,
}

# Event categories with impact multipliers
_HIGH_IMPACT_KEYWORDS = {"earnings", "quarterly", "results", "q1", "q2", "q3", "q4",
                          "annual", "ipo", "merger", "acquisition", "regulatory",
                          "sebi", "rbi", "government", "policy"}


def _parse_published(published_str: str) -> datetime | None:
    """Try to parse feedparser published string to datetime."""
    if not published_str:
        return None
    formats = [
        "%a, %d %b %Y %H:%M:%S %z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(published_str.strip(), fmt)
        except ValueError:
            pass
    return None


def _recency_weight(published_str: str) -> float:
    """Return 1.0–2.0 based on how recent the news is."""
    dt = _parse_published(published_str)
    if not dt:
        return 1.0
    now = datetime.now(timezone.utc)
    try:
        age_hours = (now - dt.astimezone(timezone.utc)).total_seconds() / 3600
    except Exception:
        return 1.0
    if age_hours < 1:    return 2.0
    if age_hours < 3:    return 1.6
    if age_hours < 6:    return 1.3
    if age_hours < 12:   return 1.1
    if age_hours < 24:   return 1.0
    return 0.8  # old news gets penalised


def _source_credibility(link: str) -> float:
    for domain, weight in _CREDIBLE_SOURCES.items():
        if domain in link:
            return weight
    return 1.0


def _is_negated(word: str, title_words: list[str]) -> bool:
    """Check if `word` is preceded by a negator in the word list."""
    try:
        idx = title_words.index(word)
    except ValueError:
        return False
    window = title_words[max(0, idx - 3): idx]
    return bool(set(window) & _NEGATORS)


def _score_title(title: str) -> tuple[float, float, str]:
    """
    Returns (pos_score, neg_score, sentiment_tag).
    Uses weighted keywords, negation detection.
    """
    words = re.findall(r'\b\w+\b', title.lower())
    pos_score = 0.0
    neg_score = 0.0

    for word in words:
        if word in _POS:
            if not _is_negated(word, words):
                pos_score += _POS[word]
        if word in _NEG:
            if not _is_negated(word, words):
                neg_score += _NEG[word]

    net = pos_score - neg_score
    if net > 3:
        tag = "positive"
    elif net < -2:
        tag = "negative"
    else:
        tag = "neutral"

    return pos_score, neg_score, tag


def _is_high_impact(title: str) -> bool:
    words = set(re.findall(r'\b\w+\b', title.lower()))
    return bool(words & _HIGH_IMPACT_KEYWORDS)


def simple_news_score(news_items: list[dict]) -> dict:
    """
    Upgraded news scoring:
    - Weighted keywords (not flat +5/-7)
    - Negation detection ("cleared from fraud" = positive)
    - Recency weighting (fresh news counts more)
    - Source credibility weighting
    - High-impact event detection
    """
    if not news_items:
        return {
            "score": 50, "sentiment": "neutral", "impact": "low",
            "reasons": ["No news available"], "items": [],
        }

    base = 50.0
    reasons = []
    enriched_items = []
    high_impact_count = 0
    net_weighted_score = 0.0
    total_weight = 0.0

    for item in news_items:
        title     = item.get("title",     "")
        link      = item.get("link",      "")
        published = item.get("published", "")

        pos, neg, tag = _score_title(title)
        recency   = _recency_weight(published)
        cred      = _source_credibility(link)
        hi        = _is_high_impact(title)

        # Per-item weighted net sentiment
        net_item  = (pos - neg) * recency * cred * (1.5 if hi else 1.0)
        weight    = recency * cred
        net_weighted_score += net_item
        total_weight       += weight

        if hi:
            high_impact_count += 1
            if tag == "positive":
                reasons.append(f"High-impact positive: {title[:60]}")
            elif tag == "negative":
                reasons.append(f"High-impact negative: {title[:60]}")
        else:
            if tag == "positive" and pos >= 2:
                reasons.append(f"Positive: {title[:60]}")
            elif tag == "negative" and neg >= 3:
                reasons.append(f"Negative: {title[:60]}")

        enriched_items.append({**item, "sentiment": tag, "is_high_impact": hi})

    # Normalise net score to 0-100
    if total_weight > 0:
        avg_net = net_weighted_score / total_weight
        # Scale: ±5 weighted net maps to ±30 points
        base += max(-35, min(35, avg_net * 6))
    score = min(max(int(round(base)), 0), 100)

    # Sentiment label
    if score >= 65:
        sentiment = "positive"
    elif score <= 38:
        sentiment = "negative"
    else:
        sentiment = "neutral"

    # Impact
    if high_impact_count >= 2 or abs(net_weighted_score) > 8:
        impact = "high"
    elif high_impact_count >= 1 or abs(net_weighted_score) > 4:
        impact = "medium"
    else:
        impact = "low"

    # Sentiment drift summary
    sentiments = [i["sentiment"] for i in enriched_items]
    pos_n = sentiments.count("positive")
    neg_n = sentiments.count("negative")
    if pos_n + neg_n > 0:
        if pos_n > neg_n * 2:
            reasons.insert(0, f"News flow predominantly positive ({pos_n} bullish vs {neg_n} bearish)")
        elif neg_n > pos_n * 2:
            reasons.insert(0, f"News flow predominantly negative ({neg_n} bearish vs {pos_n} bullish)")
        else:
            reasons.insert(0, f"Mixed news: {pos_n} positive, {neg_n} negative headlines")

    return {
        "score":     score,
        "sentiment": sentiment,
        "impact":    impact,
        "reasons":   reasons[:6],
        "items":     enriched_items,
    }
