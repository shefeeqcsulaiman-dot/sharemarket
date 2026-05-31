"""
WhatsApp alerts via CallMeBot API (free, no subscription needed).

One-time setup for the user:
1. Save +34 644 65 53 60 in WhatsApp contacts as "CallMeBot"
2. Send this exact message to that number:  I allow callmebot to send me messages
3. You will receive your API key via WhatsApp
4. Add to .env:  CALLMEBOT_API_KEY=your_key_here
                 WHATSAPP_NUMBER=+919744636565
"""

import logging
import os
import urllib.parse
import urllib3
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

urllib3.disable_warnings()
log = logging.getLogger(__name__)
NSE_TZ = ZoneInfo("Asia/Kolkata")

_CALLMEBOT_URL = "https://api.callmebot.com/whatsapp.php"
_last_alerts: dict[str, str] = {}   # symbol → last signal sent


def _send(message: str) -> bool:
    load_dotenv(override=True)
    phone   = os.getenv("WHATSAPP_NUMBER", "").strip()
    api_key = os.getenv("CALLMEBOT_API_KEY", "").strip()

    if not phone or not api_key:
        log.warning("WhatsApp not configured. Set WHATSAPP_NUMBER and CALLMEBOT_API_KEY in .env")
        return False

    # CallMeBot needs phone without + but with country code
    phone_clean = phone.replace("+", "").replace(" ", "")

    try:
        resp = requests.get(
            _CALLMEBOT_URL,
            params={
                "phone":  phone_clean,
                "text":   urllib.parse.quote(message),
                "apikey": api_key,
            },
            timeout=10,
            verify=False,
        )
        if resp.status_code == 200:
            log.info("WhatsApp sent: %s", message[:60])
            return True
        else:
            log.warning("WhatsApp failed: %s %s", resp.status_code, resp.text[:100])
            return False
    except Exception as exc:
        log.warning("WhatsApp error: %s", exc)
        return False


def _fmt_pnl(pnl: float) -> str:
    return f"+Rs{pnl:.0f}" if pnl >= 0 else f"-Rs{abs(pnl):.0f}"


def _now() -> str:
    return datetime.now(NSE_TZ).strftime("%H:%M IST")


# ── Public alert functions ────────────────────────────────────────────────────

def alert_exit_now(symbol: str, ltp: float, pnl: float, reason: str) -> bool:
    msg = (
        f"🚨 YARA ALERT — EXIT NOW\n"
        f"Symbol: {symbol}\n"
        f"LTP: Rs{ltp} | P&L: {_fmt_pnl(pnl)}\n"
        f"Reason: {reason}\n"
        f"Time: {_now()}\n"
        f"⚡ Sell at market immediately"
    )
    return _send(msg)


def alert_exit(symbol: str, ltp: float, pnl: float, bid: float, reason: str) -> bool:
    msg = (
        f"⚠️ YARA ALERT — EXIT\n"
        f"Symbol: {symbol}\n"
        f"LTP: Rs{ltp} | Bid: Rs{bid} | P&L: {_fmt_pnl(pnl)}\n"
        f"Reason: {reason}\n"
        f"Time: {_now()}"
    )
    return _send(msg)


def alert_book_profit(symbol: str, ltp: float, pnl: float, decay_pct: float) -> bool:
    msg = (
        f"💰 YARA ALERT — BOOK PROFIT\n"
        f"Symbol: {symbol}\n"
        f"LTP: Rs{ltp} | P&L: {_fmt_pnl(pnl)}\n"
        f"Premium up {decay_pct:.0f}% from avg\n"
        f"Time: {_now()}\n"
        f"✅ Consider full or partial exit"
    )
    return _send(msg)


def alert_sl_breach(symbol: str, ltp: float, sl_level: float, pnl: float) -> bool:
    msg = (
        f"🔴 YARA ALERT — STOP LOSS HIT\n"
        f"Symbol: {symbol}\n"
        f"LTP Rs{ltp} crossed SL Rs{sl_level}\n"
        f"P&L: {_fmt_pnl(pnl)}\n"
        f"Time: {_now()}\n"
        f"❌ Exit position immediately"
    )
    return _send(msg)


def alert_target_hit(symbol: str, ltp: float, target: float, pnl: float) -> bool:
    msg = (
        f"🎯 YARA ALERT — TARGET HIT\n"
        f"Symbol: {symbol}\n"
        f"LTP Rs{ltp} reached target Rs{target}\n"
        f"P&L: {_fmt_pnl(pnl)}\n"
        f"Time: {_now()}\n"
        f"✅ Book profits now"
    )
    return _send(msg)


def alert_morning_summary(positions: list[dict]) -> bool:
    """Send portfolio summary at market open."""
    total_pnl = sum(p.get("pnl", 0) for p in positions)
    lines = [f"📊 YARA — Morning Summary {_now()}"]
    for p in positions:
        sig = p.get("signal", "")
        pnl = p.get("pnl", 0)
        lines.append(f"  {p['symbol']:20} {sig:12} {_fmt_pnl(pnl)}")
    lines.append(f"\nTotal P&L: {_fmt_pnl(total_pnl)}")
    return _send("\n".join(lines))


def maybe_alert(analysis: dict) -> bool:
    """
    Compare current signal to last sent alert.
    Only sends if signal changed to an urgent state.
    """
    sym    = analysis["symbol"]
    signal = analysis["signal"]
    prev   = _last_alerts.get(sym, "")

    # Don't re-alert same signal
    if signal == prev:
        return False

    sent = False
    ltp  = analysis.get("ltp", 0)
    pnl  = analysis.get("pnl", 0)

    if signal == "EXIT NOW":
        reason = analysis.get("reasons", [""])[0]
        sent = alert_exit_now(sym, ltp, pnl, reason)

    elif signal == "EXIT" and prev not in ("EXIT", "EXIT NOW"):
        chain = analysis.get("chain", {})
        bid   = chain.get("call_bid") or chain.get("put_bid") or ltp
        reason = analysis.get("reasons", [""])[0]
        sent = alert_exit(sym, ltp, pnl, bid, reason)

    elif signal == "BOOK PROFIT" and prev != "BOOK PROFIT":
        avg  = analysis.get("avg_price", 1) or 1
        dcy  = (ltp - avg) / avg * 100
        sent = alert_book_profit(sym, ltp, pnl, dcy)

    if sent:
        _last_alerts[sym] = signal

    return sent
