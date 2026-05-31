"""
Black-Scholes Greeks calculator for NSE options.
Calculates Delta, Gamma, Theta, Vega, Rho from IV + market data.
"""

import math
from scipy.stats import norm


RISK_FREE_RATE = 0.068   # India 10-yr bond ~6.8%
TRADING_DAYS   = 252


def _d1_d2(S: float, K: float, T: float, r: float, sigma: float):
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return None, None
    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return d1, d2
    except Exception:
        return None, None


def calculate_greeks(
    spot: float,
    strike: float,
    dte: int,
    iv_pct: float,
    option_type: str = "CE",
    r: float = RISK_FREE_RATE,
) -> dict:
    """
    Calculate option Greeks using Black-Scholes model.

    Args:
        spot       : Underlying spot price
        strike     : Option strike price
        dte        : Days to expiry
        iv_pct     : Implied volatility in percent (e.g. 37.84)
        option_type: 'CE' or 'PE'
        r          : Risk-free interest rate (annual)

    Returns dict with: delta, gamma, theta, vega, rho, theoretical_price, intrinsic, time_value
    """
    if not all([spot, strike, dte is not None, iv_pct]):
        return _empty()

    T     = max(dte, 0.5) / 365.0    # time in years (min 0.5 day to avoid division issues)
    sigma = iv_pct / 100.0

    d1, d2 = _d1_d2(spot, strike, T, r, sigma)
    if d1 is None:
        return _empty()

    sqrt_T = math.sqrt(T)
    n_d1   = norm.pdf(d1)             # standard normal PDF at d1
    N_d1   = norm.cdf(d1)
    N_d2   = norm.cdf(d2)
    N_nd1  = norm.cdf(-d1)
    N_nd2  = norm.cdf(-d2)
    disc   = math.exp(-r * T)

    if option_type.upper() == "CE":
        delta = N_d1
        price = spot * N_d1 - strike * disc * N_d2
        rho   = strike * T * disc * N_d2 / 100
    else:
        delta = N_d1 - 1
        price = strike * disc * N_nd2 - spot * N_nd1
        rho   = -strike * T * disc * N_nd2 / 100

    gamma = n_d1 / (spot * sigma * sqrt_T)
    vega  = spot * n_d1 * sqrt_T / 100          # per 1% change in IV
    theta = (
        -(spot * n_d1 * sigma / (2 * sqrt_T)) - r * strike * disc * N_d2
        if option_type.upper() == "CE"
        else -(spot * n_d1 * sigma / (2 * sqrt_T)) + r * strike * disc * N_nd2
    ) / 365    # per calendar day

    # Intrinsic and time value
    if option_type.upper() == "CE":
        intrinsic = max(0.0, spot - strike)
    else:
        intrinsic = max(0.0, strike - spot)
    time_value = max(0.0, price - intrinsic)

    def f(v, n): return round(float(v), n)
    return {
        "delta":            f(delta,  4),
        "gamma":            f(gamma,  6),
        "theta":            f(theta,  4),
        "vega":             f(vega,   4),
        "rho":              f(rho,    4),
        "theoretical_price":f(max(price, 0), 2),
        "intrinsic":        f(intrinsic,  2),
        "time_value":       f(time_value, 2),
    }


def theta_decay_forecast(theta: float, dte: int, ltp: float) -> dict:
    """
    Project premium decay over remaining days.
    Theta accelerates near expiry (last 7 days = ~50% of total decay).
    """
    if not theta or not ltp or ltp <= 0:
        return {}

    # Simple projection (not accounting for acceleration)
    daily_decay = abs(theta)
    week_decay  = daily_decay * 7
    def f(v, n): return round(float(v), n)
    return {
        "daily_rs":    f(daily_decay, 3),
        "weekly_rs":   f(week_decay,  2),
        "days_to_zero":f(ltp / daily_decay, 1) if daily_decay > 0 else None,
        "pct_per_day": f(daily_decay / ltp * 100, 2) if ltp > 0 else None,
    }


def _empty() -> dict:
    return {
        "delta": None, "gamma": None, "theta": None,
        "vega": None,  "rho": None,   "theoretical_price": None,
        "intrinsic": None, "time_value": None,
    }
