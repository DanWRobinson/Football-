"""Bookmaker margin removal, edge and staking."""

from __future__ import annotations

import numpy as np
from scipy.optimize import brentq


def implied(prices) -> np.ndarray:
    return 1.0 / np.asarray(prices, dtype=float)


def devig(prices, method: str = "power") -> np.ndarray:
    """Fair probabilities from a complete market's prices.

    'power' (p_i = q_i^k, solve sum = 1) handles favourite-longshot bias better than
    simple normalisation ('multiplicative').
    """
    q = implied(prices)
    if np.any(~np.isfinite(q)) or np.any(q <= 0):
        return np.full_like(q, np.nan)
    if method == "multiplicative" or abs(q.sum() - 1) < 1e-9:
        return q / q.sum()
    try:
        k = brentq(lambda k: np.sum(q ** k) - 1, 0.5, 3.0)
        return q ** k
    except ValueError:
        return q / q.sum()


def overround(prices) -> float:
    return float(implied(prices).sum() - 1)


def edge(p_model: float, price: float) -> float:
    """Expected return per unit staked."""
    return p_model * price - 1


def kelly(p_model: float, price: float, fraction: float = 0.25, cap: float = 0.05) -> float:
    """Fractional Kelly stake as a share of bankroll (capped)."""
    if price <= 1 or not np.isfinite(price):
        return 0.0
    f = (p_model * price - 1) / (price - 1)
    return float(np.clip(f * fraction, 0, cap))


def blend(p_model: np.ndarray, p_market: np.ndarray, w_model: float = 0.5) -> np.ndarray:
    """Log-linear pool of model and market probabilities."""
    pm, pk = np.clip(p_model, 1e-6, 1), np.clip(p_market, 1e-6, 1)
    z = w_model * np.log(pm) + (1 - w_model) * np.log(pk)
    e = np.exp(z)
    return e / e.sum()
