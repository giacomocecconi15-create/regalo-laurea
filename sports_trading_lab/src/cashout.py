"""Cash out con Take Profit e Stop Loss.

Due sole soglie, in percentuale dell'importo a rischio:
    Take Profit  -> chiudi in utile quando il cash out vale almeno +TP%
    Stop Loss    -> chiudi in perdita quando il cash out vale -SL% o meno
e un "quando": i momenti della partita in cui il cash out viene controllato.

  tennis:  al primo break del primo set, a fine primo set, a fine secondo set, oppure in ognuno di questi momenti
  calcio:  all'intervallo
  scalping sul servizio (tennis): ogni game di servizio e' un'operazione a se', controllata punto per punto
                                   (vedi src/scalping.py)

Il database non contiene quote in corso di partita: nei momenti di controllo la quota viene stimata con
modelli tarati sui risultati reali (primo set, secondo set, primo break, intervallo). Se nessuna soglia
viene raggiunta l'operazione resta aperta fino alla fine.

Valore del cash out per unita' di rischio (p = probabilita' stimata della selezione nel momento di controllo,
m = margine del cash out):
    back: profitto = quota_ingresso * p * (1 - m) - 1
    lay : profitto = puntata_banco * (1 - quota_ingresso * p * (1 + m))
Sui profitti si applica la commissione exchange della strategia.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

SECOND_HALF_SHARE = 0.554   # quota dei gol segnati nel secondo tempo (misurata sul database)
SET_MOMENTUM = 0.2          # spostamento (logit) dopo il primo set: chi lo vince rende piu' di quanto dica il modello a set indipendenti
SET2_MOMENTUM = 0.08        # idem dopo il secondo set: sull'1-1 chi ha vinto il secondo set vince di piu' (tarato 2019-2022, verificato 2023+)
MAX_GOALS = 12
WHEN = {
    "tennis": {"off": "Nessun cash out", "break1": "Al primo break", "set1": "A fine primo set", "set2": "A fine secondo set",
               "all": "In ogni momento disponibile", "scalping": "Scalping sul servizio"},
    "football": {"off": "Nessun cash out", "ht": "All'intervallo"},
}
CHECKPOINT_LABEL = {"break1": "primo break", "set1": "fine primo set", "set2": "fine secondo set", "ht": "intervallo"}
ORDER = ["break1", "set1", "set2"]
DEFAULTS = {"mode": "off", "when": "set1", "tp": 30.0, "sl": 50.0, "margin_pct": 5.0,
            # solo scalping
            "who": "both", "skip_games": 2, "decided_pct": 5.0, "stop_at_bp": False, "spread_pct": 0.5}
REASONS = {"": "Tenuta fino alla fine", "tp": "Take Profit", "sl": "Stop Loss"}


def settings(cfg: dict | None) -> dict:
    """Impostazioni complete del cash out di una strategia. Le strategie salvate con le versioni precedenti
    (regole 'manuali' a frequenza o modalita' 'intelligente') vengono convertite: l'intelligente diventa
    Take Profit / Stop Loss a fine primo set (intervallo nel calcio), il manuale viene disattivato."""
    raw = dict((cfg or {}).get("cashout") or {})
    sport = (cfg or {}).get("sport", "tennis")
    if raw.get("mode") == "smart":
        raw = {"mode": "tpsl", "when": "ht" if sport == "football" else "set1",
               "tp": raw.get("tp_pct", 30.0) if raw.get("take_profit", True) else 0.0,
               "sl": raw.get("sl_pct", 50.0) if raw.get("stop_loss", False) else 0.0,
               "margin_pct": raw.get("margin_pct", 5.0)}
    elif raw.get("mode") == "manual":
        raw = {"mode": "off", "legacy_manual": True}
    out = {**DEFAULTS, **raw}
    if out["when"] not in WHEN.get(sport, {}) or out["when"] == "off":
        out["mode"] = "off"
    return out


def is_active(cfg: dict | None) -> bool:
    c = settings(cfg)
    return c["mode"] == "tpsl" and (c["tp"] > 0 or c["sl"] > 0 or c["when"] == "scalping")


def checkpoints(cfg: dict) -> list[str]:
    """Momenti di controllo validi, in ordine cronologico, tenendo conto del momento di ingresso:
    un controllo precedente o coincidente con l'ingresso viene escluso."""
    c = settings(cfg)
    if c["mode"] != "tpsl" or c["when"] == "scalping":
        return []
    from src import backtester as bt
    entry = bt.entry_point(cfg)
    if cfg.get("sport") == "football":
        return [] if entry == "ht" else ["ht"]
    wanted = ORDER if c["when"] == "all" else [c["when"]]
    start = {None: -1, "break1": 0, "set1": 1}[entry]
    return [w for w in wanted if ORDER.index(w) > start]


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


def tennis_set2_prob(t: pd.DataFrame) -> np.ndarray:
    """Probabilita' di vincere il match dopo due set, dai set vinti nei primi due (colonna `set2_sets`)."""
    need = ("odds", "opp_odds", "set2_sets", "best_of")
    if t.empty or any(c not in t.columns for c in need):
        return np.full(len(t), np.nan)
    o, oo = 1 / t["odds"].to_numpy(dtype=float), 1 / t["opp_odds"].to_numpy(dtype=float)
    p0 = o / (o + oo)
    k = t["set2_sets"].to_numpy(dtype=float)
    bo = t["best_of"].to_numpy(dtype=float)
    ok = np.isfinite(p0) & np.isfinite(k)
    out = np.full(len(t), np.nan)
    if not ok.any():
        return out
    s = set_prob_from_match(p0[ok], bo[ok])
    q = 1 - s
    five = bo[ok] == 5
    kk = k[ok]
    p = np.where(five, np.select([kk == 2, kk == 1], [1 - q ** 3, s ** 2 * (3 - 2 * s)], s ** 3),
                 np.select([kk == 2, kk == 1], [1.0, s], 0.0))
    inner = (p > 1e-6) & (p < 1 - 1e-6)
    pc = np.clip(p, 1e-6, 1 - 1e-6)
    won2 = (t["set1_won"].to_numpy(dtype=float)[ok] == 0) if "set1_won" in t.columns else np.zeros(len(kk), bool)
    sign = np.where(kk == 1, np.where(won2, 1.0, -1.0), np.sign(kk - 1))     # sull'1-1 conta chi ha vinto il secondo set
    adj = 1 / (1 + np.exp(-(np.log(pc / (1 - pc)) + SET2_MOMENTUM * sign)))
    out[ok] = np.where(inner, adj, p)
    return out


def checkpoint_prob(trades: pd.DataFrame, point: str | None = None) -> np.ndarray:
    """Probabilita' stimata della selezione nel momento di controllo indicato (calcolata sulle quote
    pre-partita, colonna `book_odds` se presente)."""
    t = trades.assign(odds=trades["book_odds"]) if "book_odds" in trades.columns else trades
    if point is None:
        point = "set1" if "set1_won" in t.columns else "ht"
    if point == "ht":
        return football_checkpoint_prob(t)
    if point == "set1":
        return tennis_checkpoint_prob(t)
    if point == "set2":
        p = tennis_set2_prob(t)
        return np.where((p <= 0) | (p >= 1), np.nan, p)       # partita gia' finita in due set: niente cash out
    if point == "break1":
        from src import backtester as bt
        p = bt.live_prob(t, "break1")
        has = t["ip_break1"].isin(["Fatto", "Subito"]).to_numpy() if "ip_break1" in t.columns else np.zeros(len(t), bool)
        return np.where(has, p, np.nan)
    raise ValueError(point)


def cashout_value(trades: pd.DataFrame, p_now: np.ndarray, margin: float, commission: float) -> np.ndarray:
    """Profitto del cash out nel momento di controllo, per unita' di rischio (NaN se non stimabile)."""
    o = trades["odds"].to_numpy(dtype=float)
    risk = trades["risk"].to_numpy(dtype=float)
    lay = (trades["side"].to_numpy() == "lay") if "side" in trades.columns else np.zeros(len(trades), dtype=bool)
    back_pnl = risk * (o * p_now * (1 - margin) - 1)
    backer = risk / (o - 1)
    lay_pnl = backer * (1 - o * np.minimum(p_now * (1 + margin), 1.0))
    pnl = np.where(lay, lay_pnl, back_pnl)
    pnl = np.where(pnl > 0, pnl * (1 - commission), pnl)
    return np.maximum(pnl, -risk)


def first_hit(values: np.ndarray, tp: float, sl: float) -> np.ndarray:
    """Indice del primo momento in cui il valore raggiunge il Take Profit o lo Stop Loss (-1 = mai).
    `values` = matrice (operazioni x momenti) di rendimenti per unita' di rischio, NaN = momento non disponibile."""
    hit = np.zeros(values.shape, dtype=bool)
    with np.errstate(invalid="ignore"):
        if tp > 0:
            hit |= values >= tp / 100
        if sl > 0:
            hit |= values <= -sl / 100
    return np.where(hit.any(axis=1), hit.argmax(axis=1), -1)


def apply_tpsl(values: np.ndarray, hold: np.ndarray, tp: float, sl: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Applica le soglie. Ritorna (rendimento, motivo '', 'tp', 'sl', indice del momento di uscita)."""
    k = first_hit(values, tp, sl)
    rows = np.arange(len(hold))
    v = np.where(k >= 0, values[rows, np.maximum(k, 0)], hold)
    reason = np.where(k < 0, "", np.where(v >= 0, "tp", "sl"))
    return v, reason, k


def checkpoint_values(trades: pd.DataFrame, points: list[str], margin_pct: float, commission: float) -> np.ndarray:
    """Matrice (operazioni x momenti di controllo) del valore del cash out per unita' di rischio."""
    if trades.empty or not points:
        return np.zeros((len(trades), 0))
    risk = trades["risk"].to_numpy(dtype=float)
    cols = [cashout_value(trades, checkpoint_prob(trades, p), margin_pct / 100, commission) / risk for p in points]
    return np.stack(cols, axis=1)


def apply(trades: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Cash out sulle operazioni a livello di match. Aggiunge `pnl_hold` (tenendo fino alla fine), `cashout`
    ('', 'tp', 'sl'), `exit_point` (momento di uscita) e ricalcola `pnl`, `trade_won`, `equity`."""
    c = settings(cfg)
    points = checkpoints(cfg)
    t = trades.copy()
    hold = t["pnl"].to_numpy(dtype=float)
    t["pnl_hold"] = hold
    if not points or t.empty:
        t["cashout"], t["exit_point"] = "", ""
        return t
    vals = checkpoint_values(t, points, c["margin_pct"], float(cfg.get("commission") or 0.0))
    risk = t["risk"].to_numpy(dtype=float)
    v, reason, k = apply_tpsl(vals, hold / risk, c["tp"], c["sl"])
    t["pnl"] = v * risk
    t["cashout"] = reason
    t["exit_point"] = [CHECKPOINT_LABEL[points[i]] if i >= 0 else "fine partita" for i in k]
    t["trade_won"] = t["pnl"] > 0
    t["equity"] = np.cumsum(t["pnl"].to_numpy())
    return t


def tpsl_grid(values: np.ndarray, hold: np.ndarray, risk: np.ndarray, tps, sls) -> pd.DataFrame:
    """ROI (%) per ogni coppia di soglie. Righe = Stop Loss, colonne = Take Profit (0 = disattivo)."""
    out = np.full((len(sls), len(tps)), np.nan)
    for i, sl in enumerate(sls):
        for j, tp in enumerate(tps):
            v, _, _ = apply_tpsl(values, hold, tp, sl)
            out[i, j] = (v * risk).sum() / risk.sum() * 100 if risk.sum() else np.nan
    return pd.DataFrame(out, index=list(sls), columns=list(tps))


def summary(trades: pd.DataFrame) -> pd.DataFrame:
    """Effetto del cash out per motivo e momento: operazioni, profitto ottenuto e profitto tenendo fino alla fine."""
    if "cashout" not in trades.columns or trades.empty:
        return pd.DataFrame(columns=["esito", "momento", "operazioni", "profitto", "profitto_tenendo", "differenza"])
    point = trades["exit_point"].astype(object) if "exit_point" in trades.columns else pd.Series("", index=trades.index, dtype=object)
    n = pd.to_numeric(point.astype(str).str.extract(r"^dopo (\d+) punt", expand=False), errors="coerce")
    point = point.where(n.isna(), np.select([n <= 3, n <= 6], ["durante il game: punti 1-3", "durante il game: punti 4-6"],
                                            "durante il game: punti 7+"))
    key = [trades["cashout"].map(REASONS).rename("esito"), point.rename("momento")]
    g = trades.groupby(key, sort=False)
    out = pd.DataFrame({"operazioni": g.size(), "profitto": g["pnl"].sum(), "profitto_tenendo": g["pnl_hold"].sum()})
    out["differenza"] = out["profitto"] - out["profitto_tenendo"]
    out = out.rename_axis(["esito", "momento"]).reset_index()
    order = {v: i for i, v in enumerate(REASONS.values())}
    return out.sort_values(["esito", "momento"], key=lambda c: c.map(order) if c.name == "esito" else c).reset_index(drop=True)
