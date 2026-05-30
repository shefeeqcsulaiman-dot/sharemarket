"""
NSE India public API session manager.
No API key required — uses the same session-cookie approach as the browser.
Session is initialised once and refreshed every 5 minutes.
"""

import logging
import threading
import time

import requests

log = logging.getLogger(__name__)

_BASE = "https://www.nseindia.com"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
    "X-Requested-With": "XMLHttpRequest",
}
_SESSION_TTL = 240  # seconds before re-initialising session


class _NSESession:
    def __init__(self):
        self._session: requests.Session | None = None
        self._lock = threading.Lock()
        self._born_at: float = 0.0

    def _build(self) -> requests.Session:
        s = requests.Session()
        s.headers.update(_HEADERS)
        try:
            s.get(_BASE, timeout=12)
            time.sleep(0.4)
            s.get(f"{_BASE}/market-data/live-equity-market", timeout=12)
            time.sleep(0.3)
        except Exception as exc:
            log.debug("NSE session warm-up failed (non-fatal): %s", exc)
        return s

    def _ensure(self):
        now = time.monotonic()
        if self._session is None or (now - self._born_at) > _SESSION_TTL:
            self._session = self._build()
            self._born_at = now

    def get(self, path: str, params: dict | None = None) -> dict | None:
        with self._lock:
            self._ensure()
        for attempt in range(2):
            try:
                resp = self._session.get(
                    f"{_BASE}{path}", params=params, timeout=10
                )
                if resp.status_code == 401 and attempt == 0:
                    with self._lock:
                        self._session = self._build()
                        self._born_at = time.monotonic()
                    continue
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:
                log.debug("NSE GET %s failed (attempt %d): %s", path, attempt, exc)
                if attempt == 0:
                    with self._lock:
                        self._session = self._build()
                        self._born_at = time.monotonic()
        return None


_session = _NSESession()


def nse_get(path: str, params: dict | None = None) -> dict | None:
    return _session.get(path, params=params)


# ── Public helpers ───────────────────────────────────────────────────────────

def fetch_quote(symbol: str) -> dict | None:
    """Return live NSE equity quote dict, or None on failure."""
    data = nse_get("/api/quote-equity", params={"symbol": symbol.upper()})
    if not data:
        return None
    try:
        pi = data["priceInfo"]
        ti = data.get("tradeInfo", {})
        hl = pi.get("intraDayHighLow", {})
        meta = data.get("metadata", {})
        change = pi.get("change", 0.0)
        pchange = pi.get("pChange", 0.0)
        ltp = pi.get("lastPrice") or pi.get("close", 0.0)
        return {
            "symbol": symbol.upper(),
            "ltp": round(float(ltp), 2),
            "open": round(float(pi.get("open", ltp)), 2),
            "high": round(float(hl.get("max", ltp)), 2),
            "low": round(float(hl.get("min", ltp)), 2),
            "previous_close": round(float(pi.get("previousClose", ltp)), 2),
            "change": round(float(change), 2),
            "change_pct": round(float(pchange), 2),
            "change_text": f"{pchange:+.2f}%",
            "volume": int(ti.get("totalTradedVolume", 0) or 0),
            "vwap": round(float(pi.get("vwap", ltp)), 2),
            "timestamp_str": meta.get("lastUpdateTime", ""),
            "source": "nse",
        }
    except Exception as exc:
        log.debug("NSE quote parse error for %s: %s", symbol, exc)
        return None


def fetch_option_chain(symbol: str) -> dict | None:
    """
    Return raw NSE option-chain JSON, or None on failure.
    Keys: records.data (list), records.underlyingValue, records.expiryDates
    """
    data = nse_get(
        "/api/option-chain-equities", params={"symbol": symbol.upper()}
    )
    if data and data.get("records", {}).get("data"):
        return data
    # Some large-cap stocks use the indices endpoint too — try that
    data = nse_get(
        "/api/option-chain-indices", params={"symbol": symbol.upper()}
    )
    if data and data.get("records", {}).get("data"):
        return data
    return None
