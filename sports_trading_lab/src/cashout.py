"""Simulazione delle chiusure anticipate (cash out) nel backtest.

Il database non contiene quote in corso di partita, quindi il cash out si simula in due modi:

MANUALE  - riproduci il tuo comportamento reale con quattro regole, ciascuna con una quota di giocate
           interessate e un importo:
             1. vincente incassata prima      -> incassi solo una parte del profitto pieno
             2. vincente chiusa in perdita    -> uscita sbagliata: perdi una parte della puntata
             3. perdente chiusa in profitto   -> uscita giusta: incassi una parte del profitto potenziale
             4. perdente tagliata             -> recuperi una parte della puntata
           Le giocate interessate sono scelte in modo casuale ma ripetibile (dipende solo dall'id del
           match e dal seme), quindi lo stesso backtest da' sempre lo stesso risultato.

INTELLIGENTE - usa lo stato reale della partita a un punto di controllo presente nei dati:
             calcio -> risultato all'intervallo;   tennis -> esito del primo set.
           La probabilita' della selezione a quel punto e' stimata con un modello (Poisson per i gol
           del secondo tempo, set indipendenti per il tennis) calibrato sulle quote pre-partita; da
           li' si ricava il valore del cash out e si chiude se supera la soglia di profitto
           ("incassa") o di perdita ("taglia"). E' una stima: le quote reali in corso di partita
           possono differire, e il margine del cash out e' un parametro.

Valore del cash out per unita' (p = probabilita' stimata della selezione al punto di controllo,
m = margine del cash out):
    back: profitto = puntata * (quota * p * (1 - m) - 1)
    lay : profitto = puntata_banco * (1 - quota * p * (1 + m))
Sui profitti si applica la commissione exchange della strategia.
"""
from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

SECOND_HALF_SHARE = 0.554   # quota dei gol segnati nel secondo tempo (misurata sul database)
SET_MOMENTUM = 0.2          # spostamento (logit) dopo il primo set: chi lo vince rende piu' di quanto dica il modello a set indipendenti
MAX_GOALS = 12
DEFAULTS = {
    "mode": "off",
    # manuale: quote e importi in percentuale
    "win_early_share": 0.0, "win_early_keep": 70.0,      # vincenti incassate prima: % giocate, % del profitto incassata
    "win_panic_share": 0.0, "win_panic_loss": 30.0,      # vincenti chiuse in perdita: % giocate, % della puntata persa
    "lose_saved_share": 0.0, "lose_saved_gain": 30.0,    # perdenti chiuse in profitto: % giocate, % del profitto potenziale
    "lose_cut_share": 0.0, "lose_cut_recover": 40.0,     # perdenti tagliate: % giocate, % della puntata recuperata
    "seed": 1,
    # intelligente
    "take_profit": True, "tp_pct": 30.0,                 # incassa se il cash out vale almeno +tp% del rischio
    "stop_loss": False, "sl_pct": 50.0,                  # taglia se il cash out vale al massimo -sl% del rischio
    "margin_pct": 5.0,                                   # margine trattenuto sul cash out
}
REASONS = {"": "Tenuta fino alla fine", "tp": "Incassata in profitto", "sl": "Tagliata in perdita",
           "win_early": "Vincente incassata prima", "win_panic": "Vincente chiusa in perdita",
           "lose_saved": "Perdente chiusa in profitto", "lose_cut": "Perdente tagliata"}


def settings(cfg: dict | None) -> dict:
    """Impostazioni complete del cash out di una strategia (i valori mancanti prendono il default)."""
    return {**DEFAULTS, **((cfg or {}).get("cashout") or {})}


def is_active(cfg: dict | None) -> bool:
    return settings(cfg)["mode"] in ("manual", "smart")


# --------------------------------------------------------------------------- modello calcio
def _poisson_pmf(lam: np.ndarray, kmax: int = MAX_GOALS) -> np.ndarray:
    """Matrice (n, kmax+1) delle probabilita' di Poisson; l'ultima colonna assorbe la coda."""
    lam = np.asarray(lam, dtype=float)[:, None]
    k = np.arange(kmax + 1)[None, :]
    logfact = np.cumsum(np.log(np.maximum(np.arange(kmax + 1), 1)))[None, :]
    pmf = np.exp(-lam + k * np.log(np.maximum(lam, 1e-12)) - logfact)
    pmf[:, -1] += np.maximum(1 - pmf.sum(axis=1), 0)
    return pmf


_GRID = None


def _grid() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Griglia (lambda casa, lambda ospite) -> (P casa, P ospite), calcolata una volta sola."""
    global _GRID
    if _GRID is None:
        vals = np.arange(0.15, 4.51, 0.05)
        lh, la = (x.ravel() for x in np.meshgrid(vals, vals, indexing="ij"))
        ph, pa = _poisson_pmf(lh), _poisson_pmf(la)
        joint = ph[:, :, None] * pa[:, None, :]
        i, j = np.indices((MAX_GOALS + 1, MAX_GOALS + 1))
        _GRID = (np.stack([lh, la], axis=1), (joint * (i > j)).sum(axis=(1, 2)), (joint * (i < j)).sum(axis=(1, 2)))
    return _GRID


def fit_goal_rates(p_home: np.ndarray, p_away: np.ndarray) -> np.ndarray:
    """Gol attesi (casa, ospite) sull'intera partita che riproducono le probabilita' 1 e 2 senza margine."""
    lam, gh, ga = _grid()
    key = np.round(np.stack([p_home, p_away], axis=1), 3)
    ok = np.isfinite(key).all(axis=1)
    out = np.full((len(key), 2), np.nan)
    if not ok.any():
        return out
    uniq, inv = np.unique(key[ok], axis=0, return_inverse=True)
    best = np.empty(len(uniq), dtype=int)
    for s in range(0, len(uniq), 1500):          # a blocchi: memoria contenuta
        u = uniq[s:s + 1500]
        d = (u[:, 0][:, None] - gh[None, :]) ** 2 + (u[:, 1][:, None] - ga[None, :]) ** 2
        best[s:s + 1500] = d.argmin(axis=1)
    out[ok] = lam[best][np.asarray(inv).ravel()]
    return out


def total_from_over(p_over: np.ndarray) -> np.ndarray:
    """Gol totali attesi che danno la probabilita' di Over 2.5 indicata (NaN se non disponibile)."""
    lam = np.arange(0.3, 7.01, 0.02)
    over = 1 - np.exp(-lam) * (1 + lam + lam ** 2 / 2)
    p = np.asarray(p_over, dtype=float)
    return np.where(np.isfinite(p), np.interp(np.clip(p, over[0], over[-1]), over, lam), np.nan)


# correzione delle probabilita' all'intervallo misurata sui risultati reali (taratura 2019-2022, verificata sul 2023+):
# il modello di Poisson sopravvaluta chi e' in vantaggio e il numero di gol del secondo tempo
HT_TEAM = (-0.02, 1.00, -0.22)        # logit' = a + b*logit + c*segno del vantaggio della squadra scelta
HT_DRAW = (0.06, 0.94)
HT_OVER = (-0.04, 1.07, 0.10)         # termine c se all'intervallo c'e' esattamente un gol


def _calibrate_ht(t: pd.DataFrame, p: np.ndarray) -> np.ndarray:
    sel = t["selection"].to_numpy()
    h, a = t["ht_home"].to_numpy(dtype=float), t["ht_away"].to_numpy(dtype=float)
    inner = np.isfinite(p) & (p > 1e-6) & (p < 1 - 1e-6)
    x = np.log(np.clip(p, 1e-6, 1 - 1e-6) / (1 - np.clip(p, 1e-6, 1 - 1e-6)))
    lead = np.sign(np.where(sel == "home", h - a, np.where(sel == "away", a - h, 0.0)))
    team = HT_TEAM[0] + HT_TEAM[1] * x + HT_TEAM[2] * lead
    draw = HT_DRAW[0] + HT_DRAW[1] * x
    one_goal = (h + a) == 1
    x_over = np.where(sel == "under", -x, x)                      # logit dell'over
    over = HT_OVER[0] + HT_OVER[1] * x_over + HT_OVER[2] * one_goal
    logit = np.where(sel == "draw", draw, np.where(sel == "over", over, np.where(sel == "under", -over, team)))
    return np.where(inner, 1 / (1 + np.exp(-logit)), p)


def football_checkpoint_prob(t: pd.DataFrame, calibrated: bool = True) -> np.ndarray:
    """Probabilita' della selezione all'intervallo, dato il risultato del primo tempo.
    `calibrated=False` restituisce il modello di Poisson puro (le probabilita' 1-X-2 sommano a 1)."""
    need = ("odds_home", "odds_draw", "odds_away", "ht_home", "ht_away", "selection")
    if t.empty or any(c not in t.columns for c in need):
        return np.full(len(t), np.nan)
    inv = (1 / t[["odds_home", "odds_draw", "odds_away"]].to_numpy(dtype=float))
    probs = inv / inv.sum(axis=1, keepdims=True)                      # quote pre-partita senza margine
    lam = fit_goal_rates(probs[:, 0], probs[:, 2])
    if "odds_over" in t.columns and "odds_under" in t.columns:        # il totale gol e' piu' preciso dal mercato O/U
        o, u = 1 / t["odds_over"].to_numpy(dtype=float), 1 / t["odds_under"].to_numpy(dtype=float)
        tot = total_from_over(o / (o + u))
        scale = np.where(np.isfinite(tot), tot / lam.sum(axis=1), 1.0)
        lam = lam * scale[:, None]
    h, a = t["ht_home"].to_numpy(dtype=float), t["ht_away"].to_numpy(dtype=float)
    ok = np.isfinite(lam).all(axis=1) & np.isfinite(h) & np.isfinite(a)
    out = np.full(len(t), np.nan)
    if not ok.any():
        return out
    px, py = _poisson_pmf(lam[ok, 0] * SECOND_HALF_SHARE), _poisson_pmf(lam[ok, 1] * SECOND_HALF_SHARE)
    joint = px[:, :, None] * py[:, None, :]                           # gol del secondo tempo: casa x ospite
    i, j = np.indices((MAX_GOALS + 1, MAX_GOALS + 1))
    lead = (h[ok] - a[ok])[:, None, None]
    goals = (h[ok] + a[ok])[:, None, None]
    diff = (i - j)[None, :, :] + lead
    table = {
        "home": (joint * (diff > 0)).sum(axis=(1, 2)), "draw": (joint * (diff == 0)).sum(axis=(1, 2)),
        "away": (joint * (diff < 0)).sum(axis=(1, 2)), "over": (joint * ((i + j)[None, :, :] + goals > 2.5)).sum(axis=(1, 2)),
    }
    table["under"] = 1 - table["over"]
    sel = t["selection"].to_numpy()[ok]
    res = np.full(ok.sum(), np.nan)
    for name, p in table.items():
        res = np.where(sel == name, p, res)
    out[ok] = res
    return _calibrate_ht(t, out) if calibrated else out


# --------------------------------------------------------------------------- modello tennis
def set_prob_from_match(p_match: np.ndarray, best_of: np.ndarray) -> np.ndarray:
    """Probabilita' di vincere un set che, con set indipendenti, da' la probabilita' di match indicata."""
    s = np.linspace(0.001, 0.999, 999)
    bo3, bo5 = s ** 2 * (3 - 2 * s), s ** 3 * (10 - 15 * s + 6 * s ** 2)
    p = np.clip(np.asarray(p_match, dtype=float), 0.001, 0.999)
    return np.where(np.asarray(best_of, dtype=float) == 5, np.interp(p, bo5, s), np.interp(p, bo3, s))


def match_prob_after_first_set(s: np.ndarray, best_of: np.ndarray, won_first: np.ndarray) -> np.ndarray:
    """Probabilita' di vincere il match dopo il primo set (vinto o perso)."""
    s = np.asarray(s, dtype=float)
    q = 1 - s
    bo5 = np.asarray(best_of, dtype=float) == 5
    ahead = np.where(bo5, s ** 2 * (1 + 2 * q + 3 * q ** 2), s * (2 - s))     # servono 2 set su 4 / 1 su 2
    behind = np.where(bo5, s ** 3 * (1 + 3 * q), s ** 2)                      # servono 3 set su 4 / 2 su 2
    won_first = np.asarray(won_first, dtype=bool)
    p = np.clip(np.where(won_first, ahead, behind), 1e-6, 1 - 1e-6)
    logit = np.log(p / (1 - p)) + np.where(won_first, SET_MOMENTUM, -SET_MOMENTUM)   # correzione misurata sul database
    return 1 / (1 + np.exp(-logit))


def tennis_checkpoint_prob(t: pd.DataFrame) -> np.ndarray:
    need = ("odds", "opp_odds", "set1_won", "best_of")
    if t.empty or any(c not in t.columns for c in need):
        return np.full(len(t), np.nan)
    o, oo = 1 / t["odds"].to_numpy(dtype=float), 1 / t["opp_odds"].to_numpy(dtype=float)
    p0 = o / (o + oo)
    first = t["set1_won"].to_numpy(dtype=float)
    ok = np.isfinite(p0) & np.isfinite(first)
    out = np.full(len(t), np.nan)
    if ok.any():
        bo = t["best_of"].to_numpy(dtype=float)[ok]
        out[ok] = match_prob_after_first_set(set_prob_from_match(p0[ok], bo), bo, first[ok] > 0.5)
    return out


def checkpoint_prob(trades: pd.DataFrame) -> np.ndarray:
    """Probabilita' della selezione al punto di controllo (intervallo / fine primo set)."""
    if "set1_won" in trades.columns:
        return tennis_checkpoint_prob(trades)
    return football_checkpoint_prob(trades)


# --------------------------------------------------------------------------- applicazione
def _uniform(match_ids: pd.Series, selections: pd.Series, seed: int) -> np.ndarray:
    """Numero in [0, 1) stabile per ogni giocata: non cambia aggiungendo o togliendo filtri."""
    def one(key: str) -> float:
        return int(hashlib.md5(key.encode()).hexdigest()[:8], 16) / 0x100000000
    return np.array([one(f"{seed}|{m}|{s}") for m, s in zip(match_ids, selections)])


def cashout_value(trades: pd.DataFrame, p_now: np.ndarray, margin: float, commission: float) -> np.ndarray:
    """Profitto del cash out al punto di controllo, nelle stesse unita' di `pnl` (NaN se non stimabile)."""
    o = trades["odds"].to_numpy(dtype=float)
    risk = trades["risk"].to_numpy(dtype=float)
    lay = (trades["side"].to_numpy() == "lay") if "side" in trades.columns else np.zeros(len(trades), dtype=bool)
    back_pnl = risk * (o * p_now * (1 - margin) - 1)
    backer = risk / (o - 1)
    lay_pnl = backer * (1 - o * np.minimum(p_now * (1 + margin), 1.0))
    pnl = np.where(lay, lay_pnl, back_pnl)
    pnl = np.where(pnl > 0, pnl * (1 - commission), pnl)
    return np.maximum(pnl, -risk)


def apply(trades: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Applica il cash out alle operazioni gia' regolate. Aggiunge `pnl_hold` (risultato tenendo fino
    alla fine), `cashout` (motivo della chiusura, '' se tenuta) e ricalcola `pnl`, `trade_won`, `equity`."""
    co = settings(cfg)
    t = trades.copy()
    hold = t["pnl"].to_numpy(dtype=float)
    t["pnl_hold"] = hold
    reason = np.full(len(t), "", dtype=object)
    pnl = hold.copy()
    if co["mode"] == "manual" and len(t):
        risk = t["risk"].to_numpy(dtype=float)
        full_win = t["win_return"].to_numpy(dtype=float) * risk
        u = _uniform(t["match_id"], t["selection"], int(co["seed"]))
        won = hold > 0
        a, b = co["win_early_share"] / 100, co["win_panic_share"] / 100
        c, d = co["lose_saved_share"] / 100, co["lose_cut_share"] / 100
        early, panic = won & (u < a), won & (u >= a) & (u < a + b)
        saved, cut = ~won & (u < c), ~won & (u >= c) & (u < c + d)
        pnl = np.where(early, hold * co["win_early_keep"] / 100, pnl)
        pnl = np.where(panic, -risk * co["win_panic_loss"] / 100, pnl)
        pnl = np.where(saved, full_win * co["lose_saved_gain"] / 100, pnl)
        pnl = np.where(cut, -risk * (1 - co["lose_cut_recover"] / 100), pnl)
        for mask, name in ((early, "win_early"), (panic, "win_panic"), (saved, "lose_saved"), (cut, "lose_cut")):
            reason[mask] = name
    elif co["mode"] == "smart" and len(t):
        p_now = checkpoint_prob(t)
        value = cashout_value(t, p_now, co["margin_pct"] / 100, float((cfg or {}).get("commission") or 0.0))
        ratio = value / t["risk"].to_numpy(dtype=float)
        tp = bool(co["take_profit"]) & np.isfinite(ratio) & (ratio >= co["tp_pct"] / 100)
        sl = bool(co["stop_loss"]) & np.isfinite(ratio) & (ratio <= -co["sl_pct"] / 100)
        pnl = np.where(tp | sl, value, pnl)
        reason[tp], reason[sl] = "tp", "sl"
        t["cashout_value"] = value
    t["pnl"] = pnl
    t["cashout"] = reason
    t["trade_won"] = pnl > 0
    t["equity"] = np.cumsum(pnl)
    return t


def summary(trades: pd.DataFrame) -> pd.DataFrame:
    """Effetto del cash out per motivo: giocate, profitto ottenuto e profitto che avrebbero dato tenendole."""
    if "cashout" not in trades.columns or trades.empty:
        return pd.DataFrame(columns=["esito", "giocate", "profitto", "profitto_tenendo", "differenza"])
    g = trades.groupby("cashout")
    out = pd.DataFrame({"giocate": g.size(), "profitto": g["pnl"].sum(), "profitto_tenendo": g["pnl_hold"].sum()})
    out["differenza"] = out["profitto"] - out["profitto_tenendo"]
    out.insert(0, "esito", [REASONS.get(k, k) for k in out.index])
    return out.reset_index(drop=True)


def threshold_grid(trades_hold: pd.DataFrame, cfg: dict, tps=(None, 10, 20, 30, 50, 75), sls=(None, 30, 50, 70)) -> pd.DataFrame:
    """ROI per ogni combinazione di soglie 'incassa' / 'taglia' (modalita' intelligente), con la tenuta
    nelle due meta' del periodo. `trades_hold` sono le operazioni senza cash out."""
    co = settings(cfg)
    if trades_hold.empty:
        return pd.DataFrame()
    p_now = checkpoint_prob(trades_hold)
    value = cashout_value(trades_hold, p_now, co["margin_pct"] / 100, float((cfg or {}).get("commission") or 0.0))
    hold = trades_hold["pnl"].to_numpy(dtype=float)
    risk = trades_hold["risk"].to_numpy(dtype=float)
    ratio = value / risk
    ok = np.isfinite(ratio)
    half = np.arange(len(hold)) < len(hold) / 2
    rows = []
    for tp in tps:
        for sl in sls:
            close = (ok & (ratio >= tp / 100) if tp is not None else False) | (ok & (ratio <= -sl / 100) if sl is not None else False)
            pnl = np.where(close, value, hold)
            eq = np.cumsum(pnl)
            rows.append({
                "incassa_da": "mai" if tp is None else f"+{tp}%", "taglia_da": "mai" if sl is None else f"-{sl}%",
                "chiuse_pct": float(np.mean(close) * 100) if np.ndim(close) else 0.0,
                "roi_pct": pnl.sum() / risk.sum() * 100, "profit": pnl.sum(),
                "max_dd": float((np.maximum.accumulate(np.maximum(eq, 0)) - eq).max()),
                "roi_1": pnl[half].sum() / risk[half].sum() * 100 if half.any() else np.nan,
                "roi_2": pnl[~half].sum() / risk[~half].sum() * 100 if (~half).any() else np.nan,
            })
    return pd.DataFrame(rows)
