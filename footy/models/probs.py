"""Scoreline probabilities (Dixon-Coles adjusted Poisson) and market prices."""

from __future__ import annotations

import numpy as np
from scipy.stats import poisson


def dc_tau(x, y, lh, la, rho):
    """Dixon-Coles low-score correction."""
    if x == 0 and y == 0:
        return 1 - lh * la * rho
    if x == 0 and y == 1:
        return 1 + lh * rho
    if x == 1 and y == 0:
        return 1 + la * rho
    if x == 1 and y == 1:
        return 1 - rho
    return 1.0


def score_matrix(lh: float, la: float, rho: float = 0.0, max_goals: int = 10) -> np.ndarray:
    g = np.arange(max_goals + 1)
    M = np.outer(poisson.pmf(g, lh), poisson.pmf(g, la))
    if rho:
        for x in (0, 1):
            for y in (0, 1):
                M[x, y] *= dc_tau(x, y, lh, la, rho)
    M = np.clip(M, 0, None)
    return M / M.sum()


def markets(M: np.ndarray) -> dict:
    n = M.shape[0]
    hg, ag = np.indices(M.shape)
    tot = hg + ag
    out = {
        "H": M[hg > ag].sum(), "D": M[hg == ag].sum(), "A": M[hg < ag].sum(),
        "BTTS": M[(hg > 0) & (ag > 0)].sum(),
        "H_CS": M[:, 0].sum(), "A_CS": M[0, :].sum(),
        "H_exp": (M.sum(1) * np.arange(n)).sum(), "A_exp": (M.sum(0) * np.arange(n)).sum(),
    }
    for line in (0.5, 1.5, 2.5, 3.5, 4.5):
        out[f"O{line}"] = M[tot > line].sum()
        out[f"U{line}"] = 1 - out[f"O{line}"]
    for line in (-1.5, -0.5, 0.5, 1.5):  # home handicap, half-lines only (no push)
        out[f"AH_H{line:+}"] = M[(hg - ag + line) > 0].sum()
    flat = [((i, j), M[i, j]) for i in range(min(n, 6)) for j in range(min(n, 6))]
    flat.sort(key=lambda t: -t[1])
    out["top_scores"] = [(f"{i}-{j}", float(p)) for (i, j), p in flat[:5]]
    return out


def xpts_from_xg(hxg: float, axg: float) -> tuple[float, float]:
    """Expected league points given each side's xG (independent Poisson)."""
    M = score_matrix(max(hxg, 0.01), max(axg, 0.01), 0.0, 10)
    mk = markets(M)
    return 3 * mk["H"] + mk["D"], 3 * mk["A"] + mk["D"]
