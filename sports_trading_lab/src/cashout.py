"""Uscita dalla posizione: Take Profit e Stop Loss come due regole separate, ognuna con il proprio evento.

    Take Profit (esci in positivo) -> al primo evento favorevole scelto: fa il primo break, fa il controbreak, fa il
                                      prossimo break, vince il primo o il secondo set (calcio: un esito all'intervallo)
    Stop Loss   (esci in negativo) -> al primo evento sfavorevole scelto: subisce il primo break / il controbreak /
                                      il prossimo break, perde il primo o il secondo set
Per chi banca (lay) favorevole e sfavorevole si invertono. Vale la prima regola che scatta dopo l'ingresso; se nessuna
scatta l'operazione resta aperta fino alla fine. Esempio: punti chi fa il primo break, Take Profit a fine primo set
se lo vince, Stop Loss al controbreak se lo subisce.

Tre metodi:
  "odds"     -> per ciascuna regola la quota a cui chiudi. Il ROI si calcola dalla quota di ingresso di ogni
                operazione (pre-partita, live stimata o inserita a mano):
                    back = ingresso/uscita - 1;  lay = (1 - ingresso/uscita) / (ingresso - 1)
                Quota lasciata vuota: ogni operazione esce alla quota stimata per lei in quel momento (modello tarato,
                meno il margine), cosi' l'uscita resta realistica anche con quote di ingresso molto diverse.
  "pct"      -> Take Profit e Stop Loss come ROI sull'importo rischiato (SL 25% = perdi il 25% dell'unita'), controllati
                nel momento dell'evento con la quota stimata (modelli tarati sui risultati reali): si esce al valore di
                quel momento, che puo' andare oltre la soglia. "In ogni momento" = primo break, controbreak, fine primo
                e secondo set (o intervallo), in ordine.
  "scalping" -> ogni game di servizio e' un'operazione a se', controllata punto per punto (src/scalping.py)

Tempi degli eventi: set*100 + game (fine set = set*100 + 99, intervallo = 45, pre-partita = 0). Un evento conta solo se
avviene dopo l'ingresso. Primo break, controbreak e prossimo break esistono solo per i match registrati punto per punto.

Valore del cash out per unita' di rischio (p = probabilita' stimata della selezione, m = margine):
    back: profitto = quota_ingresso * p * (1 - m) - 1
    lay : profitto = puntata_banco * (1 - quota_ingresso * p * (1 + m))
Sui profitti si applica la commissione exchange della strategia.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

SECOND_HALF_SHARE = 0.554   # quota dei gol segnati nel secondo tempo (misurata sul database)
SET_MOMENTUM = 0.2          # spostamento (logit) dopo il primo set: chi lo vince rende piu' di quanto dica il modello a set indipendenti
SET2_MOMENTUM = 0.08        # idem dopo il secondo set: sull'1-1 chi ha vinto il secondo set vince di piu' (tarato 2019-2022, verificato 2023+)
# come e' stato vinto il primo set (solo match punto per punto): logit' = logit + a*pulito + b*rimonta + c*tie-break
#   pulito  = primo break fatto e mai ripreso (+1 per chi vince il set, -1 per chi lo perde)
#   rimonta = set vinto dopo aver subito il primo break (+1), perso dopo averlo fatto (-1)
#   tie-break = nessun break nel set (+1 chi vince, -1 chi perde). Forma scelta sul 2019-2022 (log-loss 2023+:
#   0.4087 -> 0.4055), coefficienti stimati su tutti gli anni.
SET1_HISTORY = (0.315, -0.185, 0.139)
MAX_GOALS = 12

METHODS = {"odds": "A quote che inserisci tu", "pct": "Take Profit / Stop Loss in % di ROI", "scalping": "Scalping sul servizio"}
EVENT_NAME = {"break1": "primo break", "cbreak": "controbreak", "nbreak": "prossimo break", "set1": "fine primo set",
              "set2": "fine secondo set", "ht": "intervallo", "all": "in ogni momento"}
TENNIS_EVENTS = ["break1", "cbreak", "nbreak", "set1", "set2"]
GOOD = {"break1": "made", "cbreak": "made", "nbreak": "made", "set1": "won", "set2": "won"}   # favorevole a chi punta
BAD = {"break1": "suffered", "cbreak": "suffered", "nbreak": "suffered", "set1": "lost", "set2": "lost"}
POINT_DATA = {"break1", "cbreak", "nbreak"}           # eventi noti solo nei match registrati punto per punto
ALL_POINTS = {"tennis": ["break1", "cbreak", "set1", "set2"], "football": ["ht"]}
HT_GROUPS = {"team": ["lead", "draw", "trail"], "draw": ["level", "notlevel"], "goals": ["g0", "g1", "g2"]}
OUTCOME_LABEL = {
    ("break1", "made"): "fa il primo break", ("break1", "suffered"): "subisce il primo break",
    ("cbreak", "made"): "fa il controbreak", ("cbreak", "suffered"): "subisce il controbreak",
    ("nbreak", "made"): "fa il prossimo break", ("nbreak", "suffered"): "subisce il prossimo break",
    ("set1", "won"): "vince il primo set", ("set1", "lost"): "perde il primo set",
    ("set2", "won"): "vince il secondo set", ("set2", "lost"): "perde il secondo set",
    ("ht", "lead"): "in vantaggio all'intervallo", ("ht", "draw"): "in parità all'intervallo",
    ("ht", "trail"): "in svantaggio all'intervallo", ("ht", "level"): "pari all'intervallo",
    ("ht", "notlevel"): "non pari all'intervallo", ("ht", "g0"): "0 gol nel primo tempo", ("ht", "g1"): "1 gol nel primo tempo",
    ("ht", "g2"): "2 gol nel primo tempo",
}
# vecchie impostazioni ("quando" unico per Take Profit e Stop Loss)
WHEN = {
    "tennis": {"off": "Nessuna uscita anticipata", "break1": "Al primo break", "set1": "A fine primo set", "set2": "A fine secondo set",
               "all": "In ogni momento disponibile", "scalping": "Scalping sul servizio"},
    "football": {"off": "Nessuna uscita anticipata", "ht": "All'intervallo"},
}
CHECKPOINT_LABEL = EVENT_NAME
DEFAULTS = {"mode": "off", "method": "odds", "tp_event": "off", "sl_event": "off", "tp_quote": None, "sl_quote": None,
            "tp": 30.0, "sl": 50.0, "margin_pct": 5.0,
            # solo scalping
            "who": "both", "skip_games": 2, "decided_pct": 5.0, "stop_at_bp": False, "spread_pct": 0.5}
REASONS = {"": "Tenuta fino alla fine", "tp": "Take Profit", "sl": "Stop Loss"}
_RANK = {"team": ["lead", "draw", "trail"], "draw": ["level", "notlevel"], "over": ["g2", "g1", "g0"], "under": ["g0", "g1", "g2"]}


def _quote(v) -> float | None:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if np.isfinite(v) and v > 1.0 else None


def _legacy(raw: dict, cfg: dict, sport: str) -> dict:
    """Converte le impostazioni delle versioni precedenti (un solo "quando", quote per esito, modalita' smart/manual)."""
    if raw.get("mode") == "smart":
        return {"mode": "tpsl", "method": "pct", "tp_event": "ht" if sport == "football" else "set1",
                "sl_event": "ht" if sport == "football" else "set1",
                "tp": raw.get("tp_pct", 30.0) if raw.get("take_profit", True) else 0.0,
                "sl": raw.get("sl_pct", 50.0) if raw.get("stop_loss", False) else 0.0, "margin_pct": raw.get("margin_pct", 5.0)}
    if raw.get("mode") == "manual":
        return {"mode": "off", "legacy_manual": True}
    if "when" not in raw or "tp_event" in raw or "sl_event" in raw:
        return raw
    out = {k: v for k, v in raw.items() if k not in ("when", "quotes", "exec")}
    when = raw.get("when")
    if when == "scalping":
        out["method"] = "scalping"
        return out
    if raw.get("method", "pct") != "odds":                    # percentuale: stesso momento per le due soglie
        out.update(method="pct", tp_event=when, sl_event=when)
        return out
    side = (cfg or {}).get("side", "back")
    quotes = {p: {k: _quote(v) for k, v in (row or {}).items()} for p, row in (raw.get("quotes") or {}).items()}
    out.update(method="odds", tp_event="off", sl_event="off", tp_quote=None, sl_quote=None)
    if sport == "football":
        q = {k: v for k, v in (quotes.get("ht") or {}).items() if v}
        sel = set((cfg or {}).get("selection") or [])
        group = "draw" if sel == {"draw"} else "over" if sel == {"over"} else "under" if sel == {"under"} else "team"
        ranked = [k for k in _RANK[group] if k in q]
        if side == "lay":
            ranked = ranked[::-1]
        if ranked:
            out.update(tp_event=f"ht_{ranked[0]}", tp_quote=q[ranked[0]])
        if len(ranked) > 1:
            out.update(sl_event=f"ht_{ranked[-1]}", sl_quote=q[ranked[-1]])
        return out
    points = [p for p in TENNIS_EVENTS if p in quotes] if when == "all" else [when]
    for p in points:
        row = quotes.get(p) or {}
        good, bad = (GOOD[p], BAD[p]) if side == "back" else (BAD[p], GOOD[p])
        if row.get(good) and out["tp_event"] == "off":
            out.update(tp_event=p, tp_quote=row[good])
        if row.get(bad) and out["sl_event"] == "off":
            out.update(sl_event=p, sl_quote=row[bad])
    return out


def settings(cfg: dict | None) -> dict:
    """Impostazioni complete dell'uscita di una strategia (con conversione delle versioni precedenti)."""
    raw = dict((cfg or {}).get("cashout") or {})
    sport = (cfg or {}).get("sport", "tennis")
    out = {**DEFAULTS, **_legacy(raw, cfg or {}, sport)}
    if out["method"] not in METHODS or (out["method"] == "scalping" and sport != "tennis"):
        out["method"] = "pct" if out["method"] != "scalping" else "odds"
    valid = valid_events(sport, out["method"])
    for which in ("tp", "sl"):
        if out[f"{which}_event"] not in valid:
            out[f"{which}_event"] = "off"
        out[f"{which}_quote"] = _quote(out.get(f"{which}_quote"))
    for k in ("tp", "sl", "margin_pct", "spread_pct", "decided_pct"):
        out[k] = float(out[k] or 0.0)
    if out["mode"] == "tpsl" and out["method"] != "scalping" and out["tp_event"] == "off" and out["sl_event"] == "off":
        out["mode"] = "off"
    return out


def valid_events(sport: str, method: str) -> list[str]:
    if method == "scalping":
        return ["off"]
    if sport == "football":
        evs = ["ht_" + k for g in HT_GROUPS.values() for k in g]
        return ["off"] + (["ht"] + evs if method == "pct" else evs)
    return ["off"] + TENNIS_EVENTS + (["all"] if method == "pct" else [])


def rule_active(c: dict, which: str) -> bool:
    if c["method"] == "scalping" or c[f"{which}_event"] == "off":
        return False
    return True if c["method"] == "odds" else c[which] > 0


def is_active(cfg: dict | None) -> bool:
    c = settings(cfg)
    if c["mode"] != "tpsl":
        return False
    return c["method"] == "scalping" or rule_active(c, "tp") or rule_active(c, "sl")


def rule_outcome(event: str, which: str, side: str) -> str | None:
    """Esito richiesto dalla regola (None = qualsiasi, per "in ogni momento" e "intervallo" a percentuale)."""
    if event in ("all", "ht"):
        return None
    if event.startswith("ht_"):
        return event[3:]
    good = (which == "tp") == (side == "back")
    return GOOD[event] if good else BAD[event]


def rule_label(event: str, which: str, side: str) -> str:
    if event == "off":
        return "nessuno"
    if event in ("all", "ht"):
        return EVENT_NAME[event]
    point = "ht" if event.startswith("ht_") else event
    return OUTCOME_LABEL[(point, rule_outcome(event, which, side))]


def needs_point_data(cfg: dict) -> set[str]:
    """Eventi di uscita che richiedono i match registrati punto per punto."""
    c = settings(cfg)
    if c["mode"] != "tpsl" or c["method"] == "scalping":
        return set()
    return {c[f"{w}_event"] for w in ("tp", "sl") if rule_active(c, w)} & POINT_DATA


def entry_valid(cfg: dict, event: str) -> bool:
    """L'evento puo' arrivare dopo l'ingresso? (falso per esempio con ingresso a fine primo set e uscita al primo break)."""
    from src import backtester as bt
    entry = bt.entry_point(cfg)
    if event in ("off", "all"):
        return True
    point = "ht" if event.startswith("ht") else event
    if entry is None:
        return True
    if entry == "ht":
        return point != "ht"
    if point in ("set2", "nbreak"):
        return True
    if entry == "set1":
        return False
    if entry == "press":
        return True                  # dipende dal momento della pressione: gli eventi gia' passati vengono ignorati
    if entry == "cbreak":
        return point == "set1"
    return point in ("cbreak", "set1")          # ingresso al primo break


def ht_group(selection) -> str:
    return "draw" if selection == "draw" else "goals" if selection in ("over", "under") else "team"


def checkpoint_outcome(trades: pd.DataFrame, point: str) -> np.ndarray:
    """Esito reale di ogni operazione nel momento indicato (None = momento non disponibile: niente break nel
    primo set, dato punto per punto mancante, partita gia' finita, 3+ gol per l'Over/Under...)."""
    n = len(trades)
    none = np.full(n, None, dtype=object)
    if n == 0:
        return none
    if point in ("break1", "cbreak"):
        col = "ip_break1" if point == "break1" else "ip_cbreak"
        if col not in trades.columns:
            return none
        b = trades[col].to_numpy(dtype=object)
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


def exit_return(entry_odds, exit_odds, side, commission: float = 0.0) -> np.ndarray:
    """Profitto per unita' di rischio chiudendo alla quota `exit_odds` una posizione aperta a `entry_odds`.
    back (rischio = puntata):        ingresso/uscita - 1
    lay  (rischio = responsabilita'): (1 - ingresso/uscita) / (ingresso - 1)
    La commissione si applica solo ai profitti. `side` puo' essere una stringa o un vettore."""
    o, x = np.asarray(entry_odds, dtype=float), np.asarray(exit_odds, dtype=float)
    lay = np.asarray(side) == "lay"
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(lay, (1 - o / x) / (o - 1), o / x - 1)
    r = np.where(r > 0, r * (1 - commission), r)
    return np.maximum(r, -1.0)


def _book(trades: pd.DataFrame) -> pd.DataFrame:
    """Le probabilita' si stimano sempre dalle quote pre-partita del bookmaker (non dalla quota live o di banca)."""
    return trades.assign(odds=trades["book_odds"]) if "book_odds" in trades.columns else trades


def _next_break(t: pd.DataFrame, t0: np.ndarray, made: bool) -> tuple[np.ndarray, np.ndarray]:
    """Primo break dopo l'ingresso fatto (made=True) o subito dal giocatore della riga: (tempo, probabilita' tarata)."""
    from src import backtester as bt
    n = len(t)
    time, prob = np.full(n, np.nan), np.full(n, np.nan)
    if "_breaks" not in t.columns or n == 0:
        return time, prob
    raw = t["_breaks"].to_numpy(dtype=object)
    players = t["player"].to_numpy(dtype=object) if "player" in t.columns else np.full(n, "")
    bo = t["best_of"].to_numpy(dtype=float) if "best_of" in t.columns else np.full(n, 3.0)
    fb = t["_fb_game"].to_numpy(dtype=float) if "_fb_game" in t.columns else np.full(n, np.nan)
    cb = t["_cb_game"].to_numpy(dtype=float) if "_cb_game" in t.columns else np.full(n, np.nan)
    b1 = t["ip_break1"].to_numpy(dtype=object) if "ip_break1" in t.columns else np.full(n, None)
    hist = np.where(b1 == "Fatto", 1.0, np.where(b1 == "Subito", -1.0, 0.0))
    for i in np.flatnonzero(pd.notna(raw)):
        me = bt._norm_name(players[i])
        for s_, g_, name, p in json.loads(raw[i]):
            tm = s_ * 100 + g_
            mine = bt._norm_name(name) == me
            if tm > t0[i] and mine == made:
                # ogni tipo di break ha la sua taratura: primo break, controbreak del primo set, break successivi
                kind = "break1" if (s_ == 1 and g_ == fb[i]) else "cbreak" if (s_ == 1 and g_ == cb[i]) else "nbreak"
                time[i] = tm
                prob[i] = float(bt.event_prob(p if mine else 1 - p, 1.0 if mine else -1.0, bo[i], kind, hist[i]))
                break
    return time, prob


_EVENT_MEMO: dict = {}


def _fingerprint(trades: pd.DataFrame) -> tuple:
    cols = [c for c in ("match_id", "player", "odds", "book_odds", "risk", "entry_t", "selection") if c in trades.columns]
    h = pd.util.hash_pandas_object(trades[cols], index=True).to_numpy() if cols and len(trades) else np.zeros(1, dtype=np.uint64)
    return len(trades), int(h.sum()), int(h[::max(1, len(h) // 89)].sum())


def event_data(trades: pd.DataFrame, event: str, t0: np.ndarray, outcome: str | None) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Per ogni operazione: (tempo dell'evento, evento valido dopo l'ingresso con l'esito richiesto, probabilita' della
    selezione subito dopo l'evento, esito reale). I risultati vengono ricordati: la stessa strategia li richiede piu' volte."""
    key = (_fingerprint(trades), event, outcome, hash(np.asarray(t0, dtype=float).tobytes()))
    hit = _EVENT_MEMO.get(key)
    if hit is not None:
        return hit
    res = _event_data(trades, event, t0, outcome)
    if len(_EVENT_MEMO) > 48:
        _EVENT_MEMO.clear()
    _EVENT_MEMO[key] = res
    return res


def _event_data(trades: pd.DataFrame, event: str, t0: np.ndarray, outcome: str | None) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    from src import backtester as bt
    n = len(trades)
    t = _book(trades)
    point = "ht" if event.startswith("ht") else event
    if point == "nbreak":
        made = outcome != "suffered"
        time, prob = _next_break(t, t0, made)
        out = np.where(np.isfinite(time), "made" if made else "suffered", None).astype(object)
        return time, np.isfinite(time), prob, out
    out = checkpoint_outcome(t, point)
    if point == "break1":
        g = t["_fb_game"].to_numpy(dtype=float) if "_fb_game" in t.columns else np.full(n, np.nan)
        time, prob = 100 + g, bt.live_prob(t, "break1")
    elif point == "cbreak":
        g = t["_cb_game"].to_numpy(dtype=float) if "_cb_game" in t.columns else np.full(n, np.nan)
        time, prob = 100 + g, bt.live_prob(t, "cbreak")
    elif point == "set1":
        time, prob = np.full(n, 199.0), checkpoint_prob(t, "set1")
    elif point == "set2":
        time, prob = np.full(n, 299.0), checkpoint_prob(t, "set2")
    else:
        time, prob = np.full(n, 45.0), checkpoint_prob(t, "ht")
    has = pd.notna(out) & np.isfinite(time)
    ok = has & (time > t0) & (out == outcome if outcome is not None else True)
    return time, ok.astype(bool), prob, out


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
    if "ip_break1" in t.columns and "ip_cbreak" in t.columns:     # storia dei break del set, se registrata
        sg = np.where(first == 1, 1.0, np.where(first == 0, -1.0, 0.0))
        b1, cb = t["ip_break1"].to_numpy(dtype=object), t["ip_cbreak"].to_numpy(dtype=object)
        clean = np.where((sg > 0) & (b1 == "Fatto") & (cb == "Nessuno"), 1.0,
                         np.where((sg < 0) & (b1 == "Subito") & (cb == "Nessuno"), -1.0, 0.0))
        comeback = np.where((sg > 0) & (b1 == "Subito"), 1.0, np.where((sg < 0) & (b1 == "Fatto"), -1.0, 0.0))
        tb = np.where(b1 == "Nessun break", sg, 0.0)
        a, b, c = SET1_HISTORY
        shift = a * clean + b * comeback + c * tb
        pc = np.clip(out, 1e-6, 1 - 1e-6)
        adj = 1 / (1 + np.exp(-(np.log(pc / (1 - pc)) + shift)))
        out = np.where(np.isfinite(out) & (shift != 0), adj, out)
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
    if point in ("break1", "cbreak"):
        from src import backtester as bt
        col = "ip_break1" if point == "break1" else "ip_cbreak"
        p = bt.live_prob(t, point)
        has = t[col].isin(["Fatto", "Subito"]).to_numpy() if col in t.columns else np.zeros(len(t), bool)
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


# --------------------------------------------------------------------------- regole di uscita
def _side(trades: pd.DataFrame, cfg: dict) -> str:
    if "side" in trades.columns and len(trades):
        return str(trades["side"].iloc[0])
    return cfg.get("side", "back")


def model_exit_odds(trades: pd.DataFrame, prob: np.ndarray, margin: float, side: str) -> np.ndarray:
    """Quota a cui si esce con il cash out stimato (coerente con `cashout_value`): chi punta chiude bancando a
    1/(p*(1-m)), chi banca chiude puntando a 1/(p*(1+m))."""
    with np.errstate(divide="ignore", invalid="ignore"):
        return 1 / (np.asarray(prob, dtype=float) * ((1 + margin) if side == "lay" else (1 - margin)))


def rule_columns(trades: pd.DataFrame, cfg: dict, which: str, quote: float | None = None, model: bool = False) -> list[tuple]:
    """Momenti candidati di una regola: lista di (tempo, valido, rendimento per unita' di rischio, etichetta, quota di
    uscita) per ogni evento della regola (uno solo, oppure tutti per "in ogni momento"). `quote` sostituisce la quota
    impostata; `model=True` esce sempre alla quota stimata dal modello."""
    c = settings(cfg)
    event = c[f"{which}_event"]
    n = len(trades)
    if event == "off" or n == 0:
        return []
    side = _side(trades, cfg)
    t0 = trades["entry_t"].to_numpy(dtype=float) if "entry_t" in trades.columns else np.zeros(n)
    t0 = np.where(np.isfinite(t0), t0, 0.0)
    commission = float(cfg.get("commission") or 0.0)
    events = ALL_POINTS[cfg.get("sport", "tennis")] if event == "all" else [event]
    outcome = None if event == "all" else rule_outcome(event, which, side)
    risk = trades["risk"].to_numpy(dtype=float)
    cols = []
    m = c["margin_pct"] / 100
    for ev in events:
        time, ok, prob, out = event_data(trades, ev, t0, outcome)
        q = quote if quote is not None else c[f"{which}_quote"]
        if c["method"] == "odds" and q and not model:
            value = exit_return(trades["odds"].to_numpy(dtype=float), q, side, commission) * np.ones(n)
            xo = np.full(n, float(q))
        else:
            value = cashout_value(trades, prob, m, commission) / risk
            xo = model_exit_odds(trades, prob, m, side)
        point = "ht" if ev.startswith("ht") else ev
        labels = np.array([f"{EVENT_NAME[point]}: {OUTCOME_LABEL.get((point, o), o or '')}" for o in out], dtype=object)
        cols.append((time, ok, value, labels, xo))
    return cols


def first_trigger(cols: list[tuple], which: str, threshold: float | None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Primo momento in cui la regola scatta: (tempo, rendimento, etichetta). threshold None = scatta sempre (quote);
    altrimenti Take Profit se il rendimento e' >= soglia, Stop Loss se e' <= -soglia."""
    if not cols:
        return np.zeros(0), np.zeros(0), np.zeros(0, dtype=object), np.zeros(0)
    n = len(cols[0][0])
    best_t, best_v, best_x = np.full(n, np.inf), np.full(n, np.nan), np.full(n, np.nan)
    best_l = np.full(n, "", dtype=object)
    for time, ok, value, labels, xo in cols:
        trig = ok & np.isfinite(value)
        if threshold is not None:
            with np.errstate(invalid="ignore"):
                trig &= (value >= threshold) if which == "tp" else (value <= -threshold)
        better = trig & (time < best_t)
        best_t, best_v = np.where(better, time, best_t), np.where(better, value, best_v)
        best_l, best_x = np.where(better, labels, best_l), np.where(better, xo, best_x)
    return best_t, best_v, best_l, best_x


def _thresholds(c: dict) -> tuple[float | None, float | None]:
    if c["method"] == "odds":
        return None, None
    return c["tp"] / 100, c["sl"] / 100


def resolve(trades: pd.DataFrame, cfg: dict, model: bool = False) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Applica le due regole: vale quella che scatta per prima. Ritorna (profitto, motivo, momento, quota di uscita).
    `model=True`: stesse regole ma uscita alla quota stimata di ogni operazione (confronto realistico)."""
    c = settings(cfg)
    n = len(trades)
    hold = trades["pnl"].to_numpy(dtype=float)
    risk = trades["risk"].to_numpy(dtype=float)
    thr_tp, thr_sl = _thresholds(c)
    inf = (np.full(n, np.inf), np.full(n, np.nan), np.full(n, "", dtype=object), np.full(n, np.nan))
    tp = first_trigger(rule_columns(trades, cfg, "tp", model=model), "tp", thr_tp) if rule_active(c, "tp") else inf
    sl = first_trigger(rule_columns(trades, cfg, "sl", model=model), "sl", thr_sl) if rule_active(c, "sl") else inf
    use_tp = np.isfinite(tp[0]) & (tp[0] < sl[0])
    use_sl = np.isfinite(sl[0]) & (sl[0] <= tp[0])
    pnl = np.where(use_tp, tp[1] * risk, np.where(use_sl, sl[1] * risk, hold))
    reason = np.where(use_tp, "tp", np.where(use_sl, "sl", "")).astype(object)
    label = np.where(use_tp, tp[2], np.where(use_sl, sl[2], "fine partita")).astype(object)
    xo = np.where(use_tp, tp[3], np.where(use_sl, sl[3], np.nan))
    return pnl, reason, label, xo


def model_benchmark(trades: pd.DataFrame, cfg: dict) -> dict:
    """Stesse regole ma uscendo alla quota stimata di ogni operazione: ROI (%), scarto rispetto a tenere fino alla fine
    (punti di ROI) e suo errore standard. Con margine zero, se la stima e' giusta per questo piano, lo scarto vale zero
    piu' il rumore: |scarto| entro circa 2 errori standard e' compatibile con una stima corretta."""
    nan = {"roi": float("nan"), "gap": float("nan"), "se": float("nan")}
    if trades.empty or "pnl_hold" not in trades.columns:
        return nan
    t = trades.assign(pnl=trades["pnl_hold"])
    pnl, *_ = resolve(t, cfg, model=True)
    risk = t["risk"].to_numpy(dtype=float)
    diff = pnl - t["pnl"].to_numpy(dtype=float)
    n = len(diff)
    se = float(np.std(diff, ddof=1) * np.sqrt(n) / risk.sum() * 100) if n > 1 else float("nan")
    return {"roi": float(pnl.sum() / risk.sum() * 100), "gap": float(diff.sum() / risk.sum() * 100), "se": se}


def apply(trades: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Uscita sulle operazioni a livello di match. Aggiunge `pnl_hold` (tenendo fino alla fine), `cashout`
    ('', 'tp', 'sl'), `exit_point` (momento ed esito), `exit_odds` e ricalcola `pnl`, `trade_won`, `equity`."""
    t = trades.copy()
    t["pnl_hold"] = t["pnl"].to_numpy(dtype=float)
    if t.empty or not is_active(cfg):
        t["cashout"], t["exit_point"], t["exit_odds"] = "", "", np.nan
        return t
    pnl, reason, label, xo = resolve(t, cfg)
    t["pnl"], t["cashout"], t["exit_point"], t["exit_odds"] = pnl, reason, label, xo
    t["trade_won"] = t["pnl"] > 0
    t["equity"] = np.cumsum(t["pnl"].to_numpy())
    return t


def rule_estimate(trades: pd.DataFrame, cfg: dict, which: str) -> dict:
    """Per la regola: quante operazioni vedono l'evento dopo l'ingresso, quota equa mediana stimata dal modello in quel
    momento e quota di ingresso mediana di quelle operazioni."""
    c = settings(cfg)
    cols = rule_columns(trades, cfg, which) if c[f"{which}_event"] not in ("off", "all") else []
    if not cols:
        return {"n": 0, "est": np.nan, "entry": np.nan}
    t0 = trades["entry_t"].to_numpy(dtype=float) if "entry_t" in trades.columns else np.zeros(len(trades))
    ev = c[f"{which}_event"]
    side = _side(trades, cfg)
    _, ok, prob, _ = event_data(trades, ev, np.where(np.isfinite(t0), t0, 0.0), rule_outcome(ev, which, side))
    m = ok & np.isfinite(prob) & (prob > 0)
    out = {"n": int(ok.sum()), "est": float(np.median(1 / prob[m])) if m.any() else np.nan,
           "entry": float(np.median(trades["odds"].to_numpy(dtype=float)[ok])) if ok.any() else np.nan, "unreal_pct": np.nan}
    q = c[f"{which}_quote"]
    if q and m.any():
        # partite in cui la quota inserita rende oltre 10 punti di ROI piu' della quota stimata per quella partita
        o = trades["odds"].to_numpy(dtype=float)[m]
        comm = float(cfg.get("commission") or 0.0)
        gap = exit_return(o, q, side, comm) - exit_return(o, 1 / prob[m], side, comm)
        out["unreal_pct"] = float((gap > 0.10).mean() * 100)
    return out


def quotes_grid(trades: pd.DataFrame, cfg: dict, tp_quotes, sl_quotes) -> pd.DataFrame:
    """ROI (%) per ogni coppia di quote di uscita (righe = quota Stop Loss, colonne = quota Take Profit, 0 = regola
    spenta). Quali operazioni escono e quando non dipende dalla quota, solo il rendimento."""
    c = settings(cfg)
    if trades.empty:
        return pd.DataFrame()
    n = len(trades)
    hold = trades["pnl_hold"].to_numpy(dtype=float) if "pnl_hold" in trades.columns else trades["pnl"].to_numpy(dtype=float)
    risk = trades["risk"].to_numpy(dtype=float)
    entry = trades["odds"].to_numpy(dtype=float)
    side, comm = _side(trades, cfg), float(cfg.get("commission") or 0.0)
    on = {**cfg, "cashout": {**c, "tp_quote": 2.0, "sl_quote": 2.0}}      # quote fittizie: servono solo i tempi
    t_tp = first_trigger(rule_columns(trades, on, "tp"), "tp", None)[0] if c["tp_event"] != "off" else np.full(n, np.inf)
    t_sl = first_trigger(rule_columns(trades, on, "sl"), "sl", None)[0] if c["sl_event"] != "off" else np.full(n, np.inf)
    gain = lambda m, q: float((exit_return(entry[m], q, side, comm) * risk[m]).sum())
    out = np.full((len(sl_quotes), len(tp_quotes)), np.nan)
    for i, qs in enumerate(sl_quotes):
        for j, qt in enumerate(tp_quotes):
            a = np.where(qt, t_tp, np.inf)
            b = np.where(qs, t_sl, np.inf)
            m_tp, m_sl = np.isfinite(a) & (a < b), np.isfinite(b) & (b <= a)
            total = hold[~(m_tp | m_sl)].sum() + (gain(m_tp, qt) if qt else 0.0) + (gain(m_sl, qs) if qs else 0.0)
            out[i, j] = total / risk.sum() * 100
    return pd.DataFrame(out, index=list(sl_quotes), columns=list(tp_quotes))


def pct_grid(trades: pd.DataFrame, cfg: dict, tps, sls) -> pd.DataFrame:
    """ROI (%) per ogni coppia di soglie percentuali (righe = Stop Loss, colonne = Take Profit, 0 = spenta)."""
    c = settings(cfg)
    if trades.empty:
        return pd.DataFrame()
    n = len(trades)
    hold = trades["pnl_hold"].to_numpy(dtype=float) if "pnl_hold" in trades.columns else trades["pnl"].to_numpy(dtype=float)
    risk = trades["risk"].to_numpy(dtype=float)
    cols_tp = rule_columns(trades, cfg, "tp") if c["tp_event"] != "off" else []
    cols_sl = rule_columns(trades, cfg, "sl") if c["sl_event"] != "off" else []
    none = (np.full(n, np.inf), np.full(n, np.nan))
    tp_hits = {v: first_trigger(cols_tp, "tp", v / 100)[:2] if v and cols_tp else none for v in tps}
    sl_hits = {v: first_trigger(cols_sl, "sl", v / 100)[:2] if v and cols_sl else none for v in sls}
    out = np.full((len(sls), len(tps)), np.nan)
    for i, s_ in enumerate(sls):
        for j, t_ in enumerate(tps):
            (a, va), (b, vb) = tp_hits[t_], sl_hits[s_]
            m_tp, m_sl = np.isfinite(a) & (a < b), np.isfinite(b) & (b <= a)
            pnl = np.where(m_tp, va * risk, np.where(m_sl, vb * risk, hold))
            out[i, j] = pnl.sum() / risk.sum() * 100
    return pd.DataFrame(out, index=list(sls), columns=list(tps))


def tpsl_grid(values: np.ndarray, hold: np.ndarray, risk: np.ndarray, tps, sls) -> pd.DataFrame:
    """ROI (%) per ogni coppia di soglie su una matrice di valori (scalping punto per punto)."""
    out = np.full((len(sls), len(tps)), np.nan)
    for i, sl in enumerate(sls):
        for j, tp in enumerate(tps):
            v, _, _ = apply_tpsl(values, hold, tp, sl)
            out[i, j] = (v * risk).sum() / risk.sum() * 100 if risk.sum() else np.nan
    return pd.DataFrame(out, index=list(sls), columns=list(tps))


def summary(trades: pd.DataFrame) -> pd.DataFrame:
    """Effetto dell'uscita per motivo e momento: operazioni, quote, ROI all'uscita, profitto ottenuto e tenendo fino alla fine."""
    cols = ["esito", "momento", "operazioni", "quota_pct", "quota_ingresso", "quota_uscita", "roi_uscita_pct", "profitto",
            "profitto_tenendo", "differenza"]
    if "cashout" not in trades.columns or trades.empty:
        return pd.DataFrame(columns=cols)
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
    order = {v: i for i, v in enumerate(["Take Profit", "Stop Loss", "Tenuta fino alla fine"])}
    return out.sort_values(["esito", "momento"], key=lambda c: c.map(order) if c.name == "esito" else c).reset_index(drop=True)[cols]
