def simple_news_score(news_items: list[dict]) -> dict:
    positive_words = [
        "profit", "growth", "order", "approval", "upgrade",
        "rally", "gain", "expansion", "strong", "revenue", "beat"
    ]
    negative_words = [
        "loss", "penalty", "debt", "downgrade", "fall",
        "decline", "fraud", "investigation", "weak", "miss"
    ]

    score = 50
    reasons = []
    positive_count = 0
    negative_count = 0

    for item in news_items:
        title = item.get("title", "").lower()
        if any(word in title for word in positive_words):
            score += 5
            reasons.append(f"Positive news: {item.get('title', '')}")
            positive_count += 1
        if any(word in title for word in negative_words):
            score -= 7
            reasons.append(f"Negative news: {item.get('title', '')}")
            negative_count += 1

    score = min(max(int(score), 0), 100)
    if score >= 65:
        sentiment = "positive"
    elif score <= 35:
        sentiment = "negative"
    else:
        sentiment = "neutral"

    impact = "low"
    if positive_count + negative_count >= 3:
        impact = "medium"
    if positive_count >= 5 or negative_count >= 5:
        impact = "high"

    return {
        "score": score,
        "sentiment": sentiment,
        "impact": impact,
        "reasons": reasons[:6],
        "items": news_items
    }
