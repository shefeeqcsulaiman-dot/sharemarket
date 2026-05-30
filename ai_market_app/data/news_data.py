import feedparser


def fetch_google_news(symbol_name: str) -> list[dict]:
    query = symbol_name.replace(" ", "+")
    url = f"https://news.google.com/rss/search?q={query}+stock+India"

    feed = feedparser.parse(url)
    news_items = []
    for entry in feed.entries[:10]:
        news_items.append({
            "title": entry.get("title", ""),
            "link": entry.get("link", ""),
            "published": entry.get("published", "")
        })

    if not news_items:
        news_items = [
            {
                "title": f"No live news available for {symbol_name}. Using sample headlines.",
                "link": "",
                "published": ""
            }
        ]

    return news_items
