#!/usr/bin/env python3
"""
options_pricing.py — Black-Scholes-Merton pricing engine with full Greeks.

Theory sources:
  - Hull Ch 14: BSM formula, risk-neutral valuation, historical vol estimation
  - Natenberg Ch 6: Delta/Gamma/Theta/Vega/Rho interpretation and behavior
  - Hull Ch 13: Geometric Brownian motion, lognormal stock price distribution

Volatility convention: annualized, continuous compounding. Time in years.
252 trading days per year (Hull §14.4).
"""

import math
from typing import Optional


# ── Normal distribution helpers ───────────────────────────────────────────────

def _norm_cdf(x: float) -> float:
    """Cumulative standard normal distribution N(x)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    """Standard normal probability density function N'(x)."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


# ── BSM core ──────────────────────────────────────────────────────────────────

def bsm_d1_d2(S: float, K: float, T: float, r: float, sigma: float,
              q: float = 0.0) -> tuple[float, float]:
    """
    Compute d1 and d2 for BSM with continuous dividend yield q.

    Hull eq 14.20 extended for dividend-paying stocks (Ch 15 / Merton 1973):
      d1 = [ln(S/K) + (r - q + σ²/2)T] / (σ√T)
      d2 = d1 − σ√T
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        raise ValueError(f"Invalid BSM inputs: S={S} K={K} T={T} σ={sigma}")
    ln_SK = math.log(S / K)
    d1 = (ln_SK + (r - q + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return d1, d2


def bsm_price(S: float, K: float, T: float, r: float, sigma: float,
              option_type: str = "call", q: float = 0.0) -> float:
    """
    Black-Scholes-Merton option price.

    Args:
        S: Underlying price
        K: Strike price
        T: Time to expiration in years (use trading days / 252)
        r: Risk-free rate (annualized, continuous)
        sigma: Volatility (annualized)
        option_type: "call" or "put"
        q: Continuous dividend yield (default 0)

    Returns:
        Theoretical option price (per share; multiply by 100 for contract value)

    Hull eq 14.20-14.21:
      c = S·e^{−qT}·N(d1) − K·e^{−rT}·N(d2)
      p = K·e^{−rT}·N(−d2) − S·e^{−qT}·N(−d1)
    """
    d1, d2 = bsm_d1_d2(S, K, T, r, sigma, q)
    disc = math.exp(-r * T)
    div  = math.exp(-q * T)

    if option_type.lower() == "call":
        return S * div * _norm_cdf(d1) - K * disc * _norm_cdf(d2)
    else:
        return K * disc * _norm_cdf(-d2) - S * div * _norm_cdf(-d1)


# ── Greeks ────────────────────────────────────────────────────────────────────

def bsm_greeks(S: float, K: float, T: float, r: float, sigma: float,
               option_type: str = "call", q: float = 0.0) -> dict:
    """
    Compute all first-order Greeks (and gamma/vega which are second-order).

    Natenberg Ch 6 summary:
      Delta:  rate of change of option value per $1 move in underlying
              Calls: 0 to +1; Puts: −1 to 0 (stored as 0 to −100 in Natenberg's
              convention, but here we use the standard 0–1 / −1–0 scale)
      Gamma:  rate of change of delta per $1 move; highest ATM
      Theta:  daily time decay (negative — options lose value with time)
      Vega:   sensitivity to 1% change in volatility (positive for long options)
      Rho:    sensitivity to 1% change in risk-free rate

    Returns dict with keys: price, delta, gamma, theta, vega, rho, d1, d2
    """
    d1, d2 = bsm_d1_d2(S, K, T, r, sigma, q)
    sqrt_T = math.sqrt(T)
    disc   = math.exp(-r * T)
    div    = math.exp(-q * T)
    nd1    = _norm_pdf(d1)

    price = bsm_price(S, K, T, r, sigma, option_type, q)

    # Delta — Hull §18.1; Natenberg Ch 6
    if option_type.lower() == "call":
        delta = div * _norm_cdf(d1)
    else:
        delta = div * (_norm_cdf(d1) - 1.0)

    # Gamma — same for calls and puts (Hull §18.2; Natenberg Fig 6-8)
    gamma = div * nd1 / (S * sigma * sqrt_T)

    # Theta — per calendar day (Hull §18.3; Natenberg p.111)
    if option_type.lower() == "call":
        theta = (-(S * div * nd1 * sigma / (2.0 * sqrt_T))
                 - r * K * disc * _norm_cdf(d2)
                 + q * S * div * _norm_cdf(d1)) / 365.0
    else:
        theta = (-(S * div * nd1 * sigma / (2.0 * sqrt_T))
                 + r * K * disc * _norm_cdf(-d2)
                 - q * S * div * _norm_cdf(-d1)) / 365.0

    # Vega — per 1% change in vol (Hull §18.4; Natenberg p.113-116)
    vega = S * div * nd1 * sqrt_T / 100.0

    # Rho — per 1% change in rate (Natenberg p.116)
    if option_type.lower() == "call":
        rho = K * T * disc * _norm_cdf(d2) / 100.0
    else:
        rho = -K * T * disc * _norm_cdf(-d2) / 100.0

    return {
        "price": price,
        "delta": delta,
        "gamma": gamma,
        "theta": theta,
        "vega":  vega,
        "rho":   rho,
        "d1":    d1,
        "d2":    d2,
        "prob_itm": _norm_cdf(d2) if option_type == "call" else _norm_cdf(-d2),
    }


# ── Implied Volatility ────────────────────────────────────────────────────────

def implied_vol(market_price: float, S: float, K: float, T: float, r: float,
                option_type: str = "call", q: float = 0.0,
                tol: float = 1e-6, max_iter: int = 100) -> Optional[float]:
    """
    Compute implied volatility via Newton-Raphson + bisection fallback.

    Strategy:
      1. Newton-Raphson using vega as the derivative (fast convergence near ATM)
      2. Bisection if N-R diverges (slower but guaranteed for extreme strikes)

    Returns None if no solution found (e.g. price below intrinsic value).

    Natenberg Ch 3: IV is the volatility that makes BSM theoretical value equal
    to market price. It's the market's implied forecast of future volatility.
    """
    if T <= 0:
        return None

    # Check intrinsic bounds
    disc = math.exp(-r * T)
    if option_type == "call":
        intrinsic = max(S - K * disc, 0.0)
    else:
        intrinsic = max(K * disc - S, 0.0)

    if market_price < intrinsic - 0.01:
        return None

    # Newton-Raphson
    sigma = 0.3  # starting guess
    for _ in range(max_iter):
        try:
            g = bsm_greeks(S, K, T, r, sigma, option_type, q)
            diff = g["price"] - market_price
            if abs(diff) < tol:
                return sigma
            vega_raw = g["vega"] * 100.0  # vega per unit, not per 1%
            if abs(vega_raw) < 1e-10:
                break
            sigma -= diff / vega_raw
            if sigma <= 0:
                sigma = 0.001
            if sigma > 5.0:
                sigma = 5.0
        except (ValueError, ZeroDivisionError):
            break

    # Bisection fallback
    lo, hi = 0.001, 5.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        try:
            price_mid = bsm_price(S, K, T, r, mid, option_type, q)
        except ValueError:
            return None
        if abs(price_mid - market_price) < tol:
            return mid
        if price_mid < market_price:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            return (lo + hi) / 2.0

    return None


# ── Historical Volatility ─────────────────────────────────────────────────────

def historical_vol(closes: list[float], annualize: bool = True) -> float:
    """
    Compute historical volatility from closing prices.

    Hull §14.4: σ̂ = s / √τ where s = std dev of log returns and τ = 1/252.
    Use n-1 denominator (unbiased estimator).

    Args:
        closes: List of closing prices (at least 2)
        annualize: If True, annualize using 252 trading days

    Returns:
        Volatility estimate (annualized if annualize=True)
    """
    if len(closes) < 2:
        raise ValueError("Need at least 2 closing prices")

    log_returns = [math.log(closes[i] / closes[i-1]) for i in range(1, len(closes))]
    n = len(log_returns)
    mean_r = sum(log_returns) / n
    variance = sum((r - mean_r) ** 2 for r in log_returns) / (n - 1)
    daily_vol = math.sqrt(variance)

    if annualize:
        return daily_vol * math.sqrt(252)
    return daily_vol


def historical_vol_ewma(closes: list[float], lam: float = 0.94) -> float:
    """
    EWMA volatility (RiskMetrics model). More responsive to recent moves.
    λ=0.94 is the standard RiskMetrics daily parameter.
    """
    if len(closes) < 2:
        raise ValueError("Need at least 2 closing prices")

    log_returns = [math.log(closes[i] / closes[i-1]) for i in range(1, len(closes))]
    var = log_returns[0] ** 2
    for r in log_returns[1:]:
        var = lam * var + (1 - lam) * r ** 2

    return math.sqrt(var * 252)


# ── Spread Greeks ─────────────────────────────────────────────────────────────

def spread_greeks(legs: list[dict]) -> dict:
    """
    Compute aggregate Greeks for a multi-leg spread.

    Each leg: {"S": float, "K": float, "T": float, "r": float, "sigma": float,
               "type": "call"|"put", "qty": int (+ for long, - for short),
               "q": float (optional)}

    Natenberg Ch 8 / Fig 8-19: greeks are additive across legs.
    Returns net delta, gamma, theta, vega, rho and net theoretical value.
    """
    totals = {"price": 0.0, "delta": 0.0, "gamma": 0.0,
              "theta": 0.0, "vega": 0.0, "rho": 0.0}

    for leg in legs:
        q = leg.get("q", 0.0)
        g = bsm_greeks(leg["S"], leg["K"], leg["T"], leg["r"],
                       leg["sigma"], leg["type"], q)
        qty = leg["qty"]
        for k in totals:
            totals[k] += g[k] * qty

    return totals


# ── Utility ───────────────────────────────────────────────────────────────────

def iv_rank(current_iv: float, iv_52w_low: float, iv_52w_high: float) -> float:
    """
    IV Rank = (current IV − 52w low) / (52w high − 52w low)

    Range 0–100. >50 = elevated IV (sell premium); <30 = low IV (buy premium).
    This is the primary strategy selector signal (Natenberg Ch 8).
    """
    if iv_52w_high <= iv_52w_low:
        return 50.0
    return 100.0 * (current_iv - iv_52w_low) / (iv_52w_high - iv_52w_low)


def iv_percentile(current_iv: float, historical_ivs: list[float]) -> float:
    """
    IV Percentile = % of days over past year where IV was below current IV.
    More robust than IV Rank when there are volatility spikes.
    """
    if not historical_ivs:
        return 50.0
    below = sum(1 for iv in historical_ivs if iv < current_iv)
    return 100.0 * below / len(historical_ivs)


def days_to_expiry(expiry_date: str, reference_date: Optional[str] = None) -> int:
    """
    Calendar days from reference (today by default) to expiry_date (YYYY-MM-DD).
    """
    from datetime import date as _date
    exp = _date.fromisoformat(expiry_date)
    ref = _date.today() if reference_date is None else _date.fromisoformat(reference_date)
    return max(0, (exp - ref).days)


def trading_days_to_expiry(calendar_days: int) -> float:
    """
    Approximate trading days from calendar days (252/365 ratio).
    Use as T in years: trading_days_to_expiry(cal_days) / 252.
    Hull §14.4: options life measured in trading days.
    """
    return calendar_days * (252 / 365)


if __name__ == "__main__":
    # Quick sanity check — Hull Example 14.6
    # S=42, K=40, r=0.10, σ=0.20, T=0.5 → c=4.76, p=0.81
    S, K, r, sigma, T = 42, 40, 0.10, 0.20, 0.5
    call = bsm_greeks(S, K, T, r, sigma, "call")
    put  = bsm_greeks(S, K, T, r, sigma, "put")
    print(f"Hull Example 14.6")
    print(f"  Call: {call['price']:.4f} (expect ~4.76)  delta={call['delta']:.4f}")
    print(f"  Put:  {put['price']:.4f}  (expect ~0.81)  delta={put['delta']:.4f}")

    # IV round-trip test
    iv = implied_vol(4.76, S, K, T, r, "call")
    print(f"  IV from call price 4.76: {iv:.4f} (expect ~0.20)")

    # Historical vol
    closes = [20.00, 20.10, 19.90, 20.00, 20.50, 20.25, 20.90, 20.90, 20.90,
              20.75, 20.75, 21.00, 21.10, 20.90, 20.90, 21.25, 21.40, 21.40,
              21.25, 21.75, 22.00]
    hv = historical_vol(closes)
    print(f"  Historical vol (Hull Table 14.1): {hv:.4f} (expect ~0.193)")
