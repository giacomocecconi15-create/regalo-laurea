"""Simulazione Monte Carlo del bankroll: stake fisso vs Kelly frazionario.

Le operazioni future vengono estratte con reinserimento (bootstrap) dai trade del backtest:
ogni trade e' descritto dal rendimento per unita' di rischio `r` (vincita netta potenziale `b`
se vinto, -1 se perso).

Kelly: per una scommessa con vincita netta `b` per unita' rischiata e probabilita' `p`, la
frazione ottimale e' f* = (p*b - (1-p)) / b = edge / b. L'edge per unita' di rischio e' stimato
con il rendimento medio storico della strategia (ROI), quindi f_i = ROI / b_i, moltiplicato per
la frazione scelta (es. 1/4) e limitato da un tetto massimo. Con ROI <= 0 Kelly non punta.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def trade_returns(trades: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """(r, b): rendimento realizzato e vincita potenziale per unita' di rischio."""
    risk = trades["risk"].to_numpy(dtype=float)
    r = trades["pnl"].to_numpy(dtype=float) / risk
    b = trades["win_return"].to_numpy(dtype=float) if "win_return" in trades else np.where(r > 0, r, np.nan)
    return r, b


def kelly_fractions(b: np.ndarray, edge: float, fraction: float = 0.25, cap: float = 0.05) -> np.ndarray:
    """Frazione di bankroll da rischiare su ogni trade (0 se non c'e' edge)."""
    if not edge > 0:
        return np.zeros_like(b, dtype=float)
    f = fraction * edge / np.asarray(b, dtype=float)
    return np.clip(np.nan_to_num(f, nan=0.0, posinf=0.0), 0.0, cap)


def max_drawdown_pct(paths: np.ndarray) -> np.ndarray:
    """Max drawdown % (picco-valle) di ogni percorso; `paths` include il bankroll iniziale in colonna 0."""
    peak = np.maximum.accumulate(paths, axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        dd = np.where(peak > 0, (peak - paths) / peak, 0.0)
    return dd.max(axis=1) * 100


def simulate(r: np.ndarray, b: np.ndarray, mode: str = "flat", n_sims: int = 1000, n_trades: int = 500,
             bankroll: float = 1000.0, stake_pct: float = 1.0, kelly_fraction: float = 0.25,
             kelly_cap_pct: float = 5.0, ruin_pct: float = 10.0, seed: int | None = 42) -> dict:
    """Simula `n_sims` percorsi di `n_trades` operazioni.

    mode='flat'  : rischio costante = stake_pct % del bankroll iniziale.
    mode='kelly' : rischio = frazione di Kelly del bankroll corrente (composto).
    Rovina: il bankroll scende a/sotto `ruin_pct` % del valore iniziale (da li' si smette di puntare).
    """
    r = np.asarray(r, dtype=float)
    if len(r) == 0:
        raise ValueError("nessun trade da cui simulare")
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(r), size=(n_sims, n_trades))
    R = r[idx]
    if mode == "flat":
        stake = bankroll * stake_pct / 100
        paths = bankroll + np.cumsum(stake * R, axis=1)
        mean_stake_pct = stake_pct
    elif mode == "kelly":
        f = kelly_fractions(b, float(r.mean()), kelly_fraction, kelly_cap_pct / 100)
        paths = bankroll * np.cumprod(1 + f[idx] * R, axis=1)
        mean_stake_pct = float(f.mean() * 100)
    else:
        raise ValueError("mode deve essere 'flat' o 'kelly'")
    paths = np.concatenate([np.full((n_sims, 1), bankroll), paths], axis=1)
    level = bankroll * ruin_pct / 100
    hit = paths <= level
    ruined = hit.any(axis=1)
    first = hit.argmax(axis=1)
    cols = np.arange(paths.shape[1])[None, :]
    frozen = ruined[:, None] & (cols > first[:, None])  # dopo la rovina il bankroll resta fermo
    paths = np.where(frozen, paths[np.arange(n_sims), first][:, None], paths)
    final = paths[:, -1]
    dd = max_drawdown_pct(paths)
    q = np.percentile(paths, [5, 25, 50, 75, 95], axis=0)
    return {
        "mode": mode, "n_sims": n_sims, "n_trades": n_trades, "bankroll": bankroll,
        "final": final, "max_dd_pct": dd,
        "risk_of_ruin_pct": float(ruined.mean() * 100),
        "prob_profit_pct": float((final > bankroll).mean() * 100),
        "median_final": float(np.median(final)), "mean_final": float(final.mean()),
        "p5_final": float(np.percentile(final, 5)), "p95_final": float(np.percentile(final, 95)),
        "median_dd_pct": float(np.median(dd)), "p95_dd_pct": float(np.percentile(dd, 95)),
        "mean_stake_pct": mean_stake_pct,
        "bands": pd.DataFrame(q.T, columns=["p5", "p25", "p50", "p75", "p95"]).rename_axis("trade").reset_index(),
    }
