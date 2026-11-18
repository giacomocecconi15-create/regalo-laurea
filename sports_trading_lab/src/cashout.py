"""Uscita dalla posizione (cash out): a quote scelte per ogni esito, oppure con Take Profit e Stop Loss in %.

Due metodi:
  quote   ("odds") -> per ogni momento (primo break, fine primo set, fine secondo set, intervallo) e per ogni esito
                      reale (break fatto/subito, set vinto/perso, in vantaggio/pari/in svantaggio...) si indica la quota
                      a cui si chiude. Il ROI si calcola dalla quota di ingresso di ogni operazione (pre-partita, live
                      stimata o inserita a mano):  back = ingresso/uscita - 1;  lay = (1 - ingresso/uscita) / (ingresso - 1)
  percentuale ("pct") -> Take Profit e Stop Loss come ROI sull'importo rischiato (SL 25% = perdi il 25% dell'unita').
                      Si controlla il valore nel momento scelto e si esce a quel valore: a fine set il prezzo si e' gia'
                      mosso molto, quindi l'uscita e' spesso ben oltre la soglia (la vista Uscita mostra il ROI medio
                      effettivo). Per un ROI esatto si usa il metodo a quote.
e un "quando": i momenti della partita in cui l'uscita e' possibile.

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
    "tennis": {"off": "Nessuna uscita anticipata", "break1": "Al primo break", "set1": "A fine primo set", "set2": "A fine secondo set",
               "all": "In ogni momento disponibile", "scalping": "Scalping sul servizio"},
    "football": {"off": "Nessuna uscita anticipata", "ht": "All'intervallo"},
}
CHECKPOINT_LABEL = {"break1": "primo break", "set1": "fine primo set", "set2": "fine secondo set", "ht": "intervallo"}
ORDER = ["break1", "set1", "set2"]
METHODS = {"odds": "A quote che inserisci tu", "pct": "Take Profit / Stop Loss in % di ROI"}
# esiti reali di ogni momento di controllo, dal punto di vista della selezione
OUTCOMES = {
    "break1": {"made": "fa il primo break", "suffered": "subisce il primo break"},
    "set1": {"won": "vince il primo set", "lost": "perde il primo set"},
    "set2": {"won": "vince il secondo set", "lost": "perde il secondo set"},
    "ht": {"lead": "in vantaggio", "draw": "in parità", "trail": "in svantaggio",
           "level": "pari all'intervallo", "notlevel": "non pari all'intervallo",
           "g0": "0 gol nel primo tempo", "g1": "1 gol nel primo tempo", "g2": "2 gol nel primo tempo"},
}
HT_GROUPS = {"team": ["lead", "draw", "trail"], "draw": ["level", "notlevel"], "goals": ["g0", "g1", "g2"]}
GOOD = {"break1": "made", "set1": "won", "set2": "won"}       # esito favorevole a chi punta (back) la selezione
DEFAULTS = {"mode": "off", "method": "pct", "quotes": {}, "when": "set1", "tp": 30.0, "sl": 50.0, "margin_pct": 5.0,
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
    if out["method"] not in METHODS or out["when"] == "scalping":
        out["method"] = "pct"
    out["quotes"] = clean_quotes(out.get("quotes"))
    return out


def clean_quotes(q) -> dict:
    """{momento: {esito: quota}} tenendo solo quote valide (> 1)."""
    out: dict = {}
    for point, row in (q or {}).items():
        if point not in OUTCOMES or not isinstance(row, dict):
            continue
        vals = {}
        for k, v in row.items():
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            if k in OUTCOMES[point] and np.isfinite(v) and v > 1.0:
                vals[k] = v
        if vals:
            out[point] = vals
    return out


def is_active(cfg: dict | None) -> bool:
    c = settings(cfg)
    if c["mode"] != "tpsl":
        return False
    if c["method"] == "odds":
        return bool(c["quotes"])
    return c["tp"] > 0 or c["sl"] > 0 or c["when"] == "scalping"


def side_outcomes(point: str, side: str) -> tuple[str, str] | None:
    """(esito Take Profit, esito Stop Loss) di un momento a due esiti, per chi punta o banca la selezione."""
    if point not in GOOD:
        return None
    good, bad = [k for k in OUTCOMES[point] if k == GOOD[point]][0], [k for k in OUTCOMES[point] if k != GOOD[point]][0]
    return (good, bad) if side == "back" else (bad, good)


def ht_group(selection) -> str:
    return "draw" if selection == "draw" else "goals" if selection in ("over", "under") else "team"


def checkpoint_outcome(trades: pd.DataFrame, point: str) -> np.ndarray:
    """Esito reale di ogni operazione nel momento indicato (None = momento non disponibile: niente break nel
    primo set, dato punto per punto mancante, partita gia' finita, 3+ gol per l'Over/Under...)."""
    n = len(trades)
    none = np.full(n, None, dtype=object)
    if n == 0:
        return none
    if point == "break1":
        if "ip_break1" not in trades.columns:
            return none
        b = trades["ip_break1"].to_numpy(dtype=object)
        return np.where(b == "Fatto", "made", np.where(b == "Subito", "suffered", None)).astype(object)
    if point == "set1":
        if "set1_won" not in trades.columns:
            return none
        w = trades["set1_won"].to_numpy(dtype=float)
        return np.where(w == 1, "won", np.where(w == 0, "lost", None)).astype(object)
    if point == "set2":
        if "set2_sets" not in trades.columns or "set1_won" not in trades.columns:
            return none
        k, w = trades["set2_sets"].to_numpy(dtype=float), trades["set1_won"].to_numpy(dtype=float)
        bo = trades["best_of"].to_numpy(dtype=float) if "best_of" in trades.columns else np.full(n, 3.0)
        won2 = k - w
        over = (bo != 5) & ((k == 0) | (k == 2))                  # 2-0 al meglio dei tre: partita finita
        ok = np.isfinite(won2) & ~over
        return np.where(ok & (won2 == 1), "won", np.where(ok & (won2 == 0), "lost", None)).astype(object)
    if point == "ht":
        sel = trades["selection"].to_numpy(dtype=object) if "selection" in trades.columns else np.full(n, "home", dtype=object)
        team = trades["ip_ht_sel"].to_numpy(dtype=object) if "ip_ht_sel" in trades.columns else none
        res = trades["ip_ht"].to_numpy(dtype=object) if "ip_ht" in trades.columns else none
        goals = trades["ip_ht_goals"].to_numpy(dtype=object) if "ip_ht_goals" in trades.columns else none
        t_out = np.where(team == "In vantaggio", "lead", np.where(team == "Pari", "draw", np.where(team == "In svantaggio", "trail", None)))
        d_out = np.where(res == "Pari", "level", np.where(pd.isna(res), None, "notlevel"))
        g_out = np.where(goals == "0", "g0", np.where(goals == "1", "g1", np.where(goals == "2", "g2", None)))
        grp = np.array([ht_group(x) for x in sel], dtype=object)
        return np.where(grp == "draw", d_out, np.where(grp == "goals", g_out, t_out)).astype(object)
    raise ValueError(point)


def exit_return(entry_odds, exit_odds, side: str, commission: float = 0.0) -> np.ndarray:
    """Profitto per unita' di rischio chiudendo alla quota `exit_odds` una posizione aperta a `entry_odds`.
    back (rischio = puntata):        ingresso/uscita - 1
    lay  (rischio = responsabilita'): (1 - ingresso/uscita) / (ingresso - 1)
    La commissione si applica solo ai profitti."""
    o, x = np.asarray(entry_odds, dtype=float), np.asarray(exit_odds, dtype=float)
    r = o / x - 1 if side == "back" else (1 - o / x) / (o - 1)
    r = np.where(r > 0, r * (1 - commission), r)
    return np.maximum(r, -1.0)


def apply_quotes(trades: pd.DataFrame, points: list[str], quotes: dict, commission: float
                 ) -> tuple[np.ndarray, np.ndarray, list[str], np.ndarray]:
    """Uscita alle quote indicate per esito. Ritorna (profitto, motivo, momento ed esito, quota di uscita)."""
    n = len(trades)
    pnl = trades["pnl"].to_numpy(dtype=float).copy()
    risk = trades["risk"].to_numpy(dtype=float)
    side = trades["side"].to_numpy(dtype=object) if "side" in trades.columns else np.full(n, "back", dtype=object)
    entry = trades["odds"].to_numpy(dtype=float)
    reason = np.full(n, "", dtype=object)
    label = np.full(n, "fine partita", dtype=object)
    exit_odds = np.full(n, np.nan)
    still = np.ones(n, dtype=bool)
    for point in points:
        q = quotes.get(point) or {}
        if not q:
            continue
        out = checkpoint_outcome(trades, point)
        x = np.array([q.get(o, np.nan) if o is not None else np.nan for o in out], dtype=float)
        go = still & np.isfinite(x) & np.isfinite(entry)
        if not go.any():
            continue
        r = np.where(side == "lay", exit_return(entry, x, "lay", commission), exit_return(entry, x, "back", commission))
        pnl = np.where(go, r * risk, pnl)
        exit_odds = np.where(go, x, exit_odds)
        pair = side_outcomes(point, str(side[0])) if n else None
        role = np.where(r >= 0, "tp", "sl") if pair is None else np.where(out == pair[0], "tp", "sl")
        reason = np.where(go, role, reason)
        label = np.where(go, np.array([f"{CHECKPOINT_LABEL[point]}: {OUTCOMES[point].get(o, '')}" for o in out], dtype=object), label)
        still &= ~go
    return pnl, reason, list(label), exit_odds


def estimated_quotes(trades: pd.DataFrame, point: str) -> dict:
    """Quota equa mediana stimata dal modello per ogni esito del momento (dalle quote pre-partita)."""
    if trades.empty:
        return {}
    p = checkpoint_prob(trades, point)
    out = checkpoint_outcome(trades, point)
    res = {}
    for k in OUTCOMES[point]:
        m = (out == k) & np.isfinite(p) & (p > 0)
        if m.any():
            res[k] = float(np.median(1 / p[m]))
    return res


def outcome_counts(trades: pd.DataFrame, point: str) -> dict:
    """Quante operazioni hanno ciascun esito reale nel momento (None = momento non disponibile)."""
    out = pd.Series(checkpoint_outcome(trades, point), dtype=object)
    return {k: int((out == k).sum()) for k in OUTCOMES[point]} | {None: int(out.isna().sum())}


def quotes_grid(trades: pd.DataFrame, point: str, tp_quotes, sl_quotes, commission: float) -> pd.DataFrame:
    """ROI (%) per ogni coppia di quote di uscita in un momento a due esiti (righe = quota Stop Loss, colonne =
    quota Take Profit; 0 = non esci). Le operazioni senza quell'esito restano tenute fino alla fine."""
    side = str(trades["side"].iloc[0]) if "side" in trades.columns and len(trades) else "back"
    pair = side_outcomes(point, side)
    if pair is None or trades.empty:
        return pd.DataFrame()
    out = checkpoint_outcome(trades, point)
    risk = trades["risk"].to_numpy(dtype=float)
    hold = trades["pnl_hold"].to_numpy(dtype=float) if "pnl_hold" in trades.columns else trades["pnl"].to_numpy(dtype=float)
    entry = trades["odds"].to_numpy(dtype=float)
    good, bad = out == pair[0], out == pair[1]
    other = hold[~(good | bad)].sum()

    def part(mask, quotes):
        return np.array([hold[mask].sum() if not x else (exit_return(entry[mask], x, side, commission) * risk[mask]).sum()
                         for x in quotes])
    g, b = part(good, tp_quotes), part(bad, sl_quotes)
    total = b[:, None] + g[None, :] + other
    return pd.DataFrame(total / risk.sum() * 100, index=list(sl_quotes), columns=list(tp_quotes))


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
    """Applica le soglie. Ritorna (rendimento, motivo '', 'tp', 'sl', indice del momento di uscita).
    Si esce al valore del momento in cui la soglia risulta superata (puo' essere oltre la soglia)."""
    k = first_hit(values, tp, sl)
    rows = np.arange(len(hold))
    v = np.where(k >= 0, values[rows, np.maximum(k, 0)] if values.shape[1] else hold, hold)
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
    commission = float(cfg.get("commission") or 0.0)
    if c["method"] == "odds":
        pnl, reason, label, xo = apply_quotes(t, points, c["quotes"], commission)
        t["pnl"], t["cashout"], t["exit_point"], t["exit_odds"] = pnl, reason, label, xo
        t["trade_won"] = t["pnl"] > 0
        t["equity"] = np.cumsum(t["pnl"].to_numpy())
        return t
    vals = checkpoint_values(t, points, c["margin_pct"], commission)
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
        return pd.DataFrame(columns=["esito", "momento", "operazioni", "quota_pct", "quota_ingresso", "quota_uscita",
                                     "roi_uscita_pct", "profitto", "profitto_tenendo", "differenza"])
    point = trades["exit_point"].astype(object) if "exit_point" in trades.columns else pd.Series("", index=trades.index, dtype=object)
    n = pd.to_numeric(point.astype(str).str.extract(r"^dopo (\d+) punt", expand=False), errors="coerce")
    point = point.where(n.isna(), np.select([n <= 3, n <= 6], ["durante il game: punti 1-3", "durante il game: punti 4-6"],
                                            "durante il game: punti 7+"))
    key = [trades["cashout"].map(REASONS).rename("esito"), point.rename("momento")]
    t = trades.assign(_xo=trades["exit_odds"] if "exit_odds" in trades.columns else np.nan)
    g = t.groupby(key, sort=False)
    out = pd.DataFrame({"operazioni": g.size(), "quota_ingresso": g["odds"].mean(), "quota_uscita": g["_xo"].mean(),
                        "profitto": g["pnl"].sum(), "rischio": g["risk"].sum(), "profitto_tenendo": g["pnl_hold"].sum()})
    out.insert(1, "quota_pct", out["operazioni"] / len(trades) * 100)
    out.insert(4, "roi_uscita_pct", out["profitto"] / out.pop("rischio") * 100)
    out["differenza"] = out["profitto"] - out["profitto_tenendo"]
    out = out.rename_axis(["esito", "momento"]).reset_index()
    order = {v: i for i, v in enumerate(REASONS.values())}
    return out.sort_values(["esito", "momento"], key=lambda c: c.map(order) if c.name == "esito" else c).reset_index(drop=True)
