def format_signal_label(signal: str) -> str:
    mapping = {
        "BUY": "BUY",
        "WATCH": "WATCH",
        "NEUTRAL": "NEUTRAL",
        "AVOID": "AVOID"
    }
    return mapping.get(signal, signal)
