"""Strumenti di analisi operativa: partite del giorno, punto di pareggio del cash out,
monitor delle strategie salvate, calcolatore di copertura."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import backtester as bt  # noqa: E402
from src import strategies as sv  # noqa: E402

OUTCOME_LABEL = {"home": "1", "draw": "X", "away": "2", "over": "Over 2.5", "under": "Under 2.5"}


# --------------------------------------------------------------------------- partite del giorno
class History:
    """Storico delle selezioni indicizzato per esito e ordinato per quota: trovare i precedenti
    'simili' a una partita costa una ricerca binaria invece di una scansione."""

    def __init__(self, *selection_frames: pd.DataFrame):
        self.by = {}
        for frame in selection_frames:
            f = frame[frame["odds"].notna()]
            for name, g in f.groupby("selection"):
                g = g.sort_values("odds", kind="stable")
                self.by[name] = (g["odds"].to_numpy(dtype=float), g["won"].to_numpy(dtype=float), g["league"].to_numpy())

    def similar(self, selection: str, odds: float, league: str | None = None, tol: float = 0.07, min_n: int = 150) -> dict:
        """Esito storico delle selezioni dello stesso tipo con quota entro +/- tol. Usa la stessa lega
        se ci sono almeno `min_n` precedenti, altrimenti tutte le leghe."""
        empty = {"n": 0, "hit_pct": np.nan, "roi_pct": np.nan, "scope": ""}
        if selection not in self.by or not np.isfinite(odds) or odds <= 1:
            return empty
        o, won, lg = self.by[selection]
        lo, hi = np.searchsorted(o, odds * (1 - tol), "left"), np.searchsorted(o, odds * (1 + tol), "right")
        if hi <= lo:
            return empty
        o, won, lg = o[lo:hi], won[lo:hi], lg[lo:hi]
        scope = "tutte le leghe"
        if league is not None:
            same = lg == league
            if same.sum() >= min_n:
                o, won, scope = o[same], won[same], str(league)
        return {"n": int(len(won)), "hit_pct": float(won.mean() * 100), "scope": scope,
                "roi_pct": float((won * (o - 1) - (1 - won)).mean() * 100)}   # rendimento storico puntando a quelle quote


def daily_board(fixtures: pd.DataFrame, con, history: History, vault: dict | None = None, min_n: int = 150) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Analisi delle partite in programma. Ritorna (tabellone, dettaglio):
    - dettaglio: una riga per partita ed esito con quota, probabilita' implicita senza margine, frequenza
      storica dei precedenti simili, scarto tra le due e numerosita';
    - tabellone: una riga per partita con variabili pre-partita, l'esito con lo scarto migliore e le
      strategie salvate che la partita attiva."""
    if fixtures is None or fixtures.empty:
        return pd.DataFrame(), pd.DataFrame()
    fixtures = fixtures.reset_index(drop=True)
    parts = [sv.football_upload_selections(fixtures, m, con) for m in ("1x2", "ou25")]
    rows = []
    for frame, names in zip(parts, (("home", "draw", "away"), ("over", "under"))):
        inv = pd.concat([1 / frame.loc[frame["selection"] == n, "odds"].reset_index(drop=True) for n in names], axis=1)
        fair = inv.div(inv.sum(axis=1, min_count=len(names)), axis=0)        # probabilita' implicite senza margine
        for k, n in enumerate(names):
            sub = frame[frame["selection"] == n].reset_index(drop=True)
            for i, r in sub.iterrows():
                s = history.similar(n, r["odds"], r["league"], min_n=min_n)
                implied = fair.iloc[i, k] * 100 if np.isfinite(fair.iloc[i, k]) else np.nan
                rows.append({"row": i, "esito": OUTCOME_LABEL[n], "selection": n, "quota": r["odds"], "implicita_pct": implied,
                             "storica_pct": s["hit_pct"], "scarto_pct": s["hit_pct"] - implied if s["n"] else np.nan,
                             "roi_storico_pct": s["roi_pct"], "precedenti": s["n"], "ambito": s["scope"]})
    detail = pd.DataFrame(rows)
    one = parts[0][parts[0]["selection"] == "home"].reset_index(drop=True)
    board = pd.DataFrame({
        "row": fixtures.index, "data": fixtures["date"], "ora": fixtures.get("time"), "lega": one["league"], "partita": one["event"],
        "1": one["odds_home"], "X": one["odds_draw"], "2": one["odds_away"], "over": fixtures.get("odds_over25"),
        "pos_casa": one["home_pos"], "pos_ospite": one["away_pos"], "form_casa": one["home_ppg5"], "form_ospite": one["away_ppg5"],
        "gol_recenti": one["goals5"],
    })
    ok = detail[(detail["precedenti"] >= min_n) & detail["scarto_pct"].notna()]
    best = ok.sort_values("scarto_pct", ascending=False).drop_duplicates("row").set_index("row")
    board["esito_migliore"] = board["row"].map(best["esito"])
    board["scarto_pct"] = board["row"].map(best["scarto_pct"])
    board["precedenti"] = board["row"].map(best["precedenti"])
    board["segnali"] = ""
    if vault:
        signals, _ = sv.scan(fixtures, vault, 1000.0, con)
        if len(signals):
            names = signals.groupby("evento")["strategia"].agg(lambda s: ", ".join(dict.fromkeys(s)))
            board["segnali"] = board["partita"].map(names).fillna("")
    return board, detail


def team_recent(con, team: str, n: int = 6) -> pd.DataFrame:
    """Ultime partite di una squadra, dalla sua prospettiva."""
    df = con.execute("""SELECT match_date, league, home_team, away_team, fthg, ftag FROM football_matches
                        WHERE home_team = ? OR away_team = ? ORDER BY match_date DESC LIMIT ?""", [team, team, int(n)]).df()
    if df.empty:
        return pd.DataFrame(columns=["data", "sede", "avversario", "risultato", "esito"])
    home = df["home_team"] == team
    gf, ga = np.where(home, df["fthg"], df["ftag"]), np.where(home, df["ftag"], df["fthg"])
    return pd.DataFrame({"data": pd.to_datetime(df["match_date"]).dt.strftime("%d/%m"), "sede": np.where(home, "casa", "fuori"),
                         "avversario": np.where(home, df["away_team"], df["home_team"]),
                         "risultato": [f"{int(a)}-{int(b)}" for a, b in zip(gf, ga)],
                         "esito": np.select([gf > ga, gf == ga], ["V", "N"], "P")})


# --------------------------------------------------------------------------- punto di pareggio del cash out
def breakeven(trades: pd.DataFrame) -> dict:
    """Quanto bisogna spingersi con le chiusure manuali perche' la strategia vada in pari.

    Lavora sul risultato 'tenendo fino alla fine' e in valore atteso (senza sorte):
      - tagliare una quota `d` delle perdenti recuperando `rec` della puntata aggiunge  d * rec * R_perse
      - incassare prima una quota `a` delle vincenti tenendo `keep` del profitto toglie a * (1 - keep) * P_vinte
    Ritorna i totali, una griglia ROI (recupero x quota tagliata) e la quota di pareggio per ogni recupero."""
    hold = trades["pnl_hold"] if "pnl_hold" in trades.columns else trades["pnl"]
    hold = hold.to_numpy(dtype=float)
    risk = trades["risk"].to_numpy(dtype=float)
    won = hold > 0
    total_risk, base = float(risk.sum()), float(hold.sum())
    lost_risk, won_profit = float(risk[~won].sum()), float(hold[won].sum())
    shares, recovers = np.arange(0, 101, 10), np.arange(20, 81, 10)
    grid = pd.DataFrame({f"{s}%": [(base + s / 100 * r / 100 * lost_risk) / total_risk * 100 for r in recovers] for s in shares},
                        index=[f"recupero {r}%" for r in recovers]) if total_risk else pd.DataFrame()
    need = []
    for r in recovers:
        share = -base / (r / 100 * lost_risk) * 100 if lost_risk > 0 else np.nan
        need.append({"recupero_pct": int(r), "quota_perdenti_da_tagliare_pct": 0.0 if base >= 0 else (share if share <= 100 else np.nan)})
    return {"roi_hold_pct": base / total_risk * 100 if total_risk else np.nan, "profit_hold": base, "total_risk": total_risk,
            "lost_risk": lost_risk, "won_profit": won_profit, "n_won": int(won.sum()), "n_lost": int((~won).sum()),
            "grid": grid, "need": pd.DataFrame(need)}


def breakeven_custom(be: dict, cut_share: float, recover: float, early_share: float = 0.0, keep: float = 100.0) -> float:
    """ROI atteso (%) con una combinazione di 'perdenti tagliate' e 'vincenti incassate prima' (valori in %)."""
    if not be["total_risk"]:
        return float("nan")
    profit = be["profit_hold"] + cut_share / 100 * recover / 100 * be["lost_risk"] - early_share / 100 * (1 - keep / 100) * be["won_profit"]
    return profit / be["total_risk"] * 100


# --------------------------------------------------------------------------- monitor delle strategie
def strategy_monitor(vault: dict, load_selections, bankroll: float = 100.0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Riesegue ogni strategia salvata sui dati correnti e confronta il periodo recente con lo storico.
    `load_selections(sport, market, price, timing)` fornisce le selezioni (cosi' l'app puo' usare la cache).
    Ritorna (tabella per strategia, operazioni di tutte le strategie per il portafoglio)."""
    rows, pooled = [], []
    for name, entry in vault.items():
        cfg = dict(entry.get("config") or {})
        sport = cfg.get("sport")
        if sport not in bt.MARKETS:
            continue
        market = cfg.get("market") or bt.MARKETS[sport][0]
        cfg.pop("date_from", None)
        cfg.pop("date_to", None)                       # il monitor guarda sempre fino all'ultimo dato
        res = bt.backtest_selections(load_selections(sport, market, cfg.get("price", "avg"), cfg.get("price_timing", "open")), cfg, bankroll)
        t, m = res.trades, res.metrics
        if t.empty:
            rows.append({"strategia": name, "operazioni": 0, "stato": "nessuna operazione"})
            continue
        last = t["date"].max()
        recent = lambda days: t[t["date"] > last - pd.Timedelta(days=days)]
        roi = lambda x: float(x["pnl"].sum() / x["risk"].sum() * 100) if len(x) else np.nan
        r365, r90 = recent(365), recent(90)
        eq = t["equity"].to_numpy()
        peak = np.maximum.accumulate(np.maximum(eq, 0))
        dd_now = float(peak[-1] - eq[-1])
        if len(r365) < 30:
            state = "poche operazioni recenti"
        elif roi(r365) > 0 and (not np.isfinite(r365["clv_pct"].mean()) or r365["clv_pct"].mean() > 0):
            state = "in linea"
        elif roi(r365) > 0:
            state = "utile ma CLV negativo"
        elif dd_now >= 0.9 * m["max_dd_units"] and m["max_dd_units"] > 0:
            state = "al massimo drawdown"
        else:
            state = "in calo"
        rows.append({"strategia": name, "operazioni": m["trades"], "roi_pct": m["roi_pct"], "roi_365_pct": roi(r365), "n_365": len(r365),
                     "roi_90_pct": roi(r90), "n_90": len(r90), "clv_365_pct": float(r365["clv_pct"].mean()) if len(r365) else np.nan,
                     "dd_attuale": dd_now, "dd_massimo": m["max_dd_units"], "ultima": last.date(), "stato": state})
        pooled.append(t[["date", "match_id", "pnl", "risk", "odds", "clv_pct"]].assign(strategia=name))
    table = pd.DataFrame(rows)
    pool = pd.concat(pooled, ignore_index=True).sort_values(["date", "match_id"], kind="stable").reset_index(drop=True) if pooled else pd.DataFrame()
    if len(pool):
        pool["equity"] = pool["pnl"].cumsum()
    return table, pool


def monthly_correlation(pool: pd.DataFrame) -> pd.DataFrame:
    """Correlazione dei profitti mensili tra strategie (vicino a 1 = salgono e scendono insieme)."""
    if pool.empty or pool["strategia"].nunique() < 2:
        return pd.DataFrame()
    m = pool.groupby([pool["date"].dt.to_period("M"), "strategia"])["pnl"].sum().unstack("strategia").fillna(0.0)
    return m.corr().round(2) if len(m) >= 6 else pd.DataFrame()


# --------------------------------------------------------------------------- calcolatore di copertura
def hedge(side: str, odds: float, stake: float, live_odds: float, commission: float = 0.0) -> dict:
    """Chiusura manuale di una posizione alla quota attuale.

    side='back': hai puntato `stake` a `odds`; chiudi bancando a `live_odds`.
    side='lay' : hai bancato con puntata del banco `stake` a `odds`; chiudi puntando a `live_odds`.
    Ritorna la puntata di chiusura che rende uguale il risultato nei due esiti e quel risultato
    (profitto positivo o perdita), al netto della commissione exchange sui profitti."""
    if not (odds > 1 and live_odds > 1 and stake > 0):
        raise ValueError("quote maggiori di 1 e puntata positiva")
    if side not in ("back", "lay"):
        raise ValueError("lato non valido")
    c = float(commission)
    if side == "back":
        if_win = lambda h: stake * (odds - 1) - h * (live_odds - 1)        # la selezione vince
        if_lose = lambda h: h - stake                                      # la selezione perde
    else:
        if_win = lambda h: h * (live_odds - 1) - stake * (odds - 1)
        if_lose = lambda h: stake - h
    net = lambda x: x * (1 - c) if x > 0 else x
    h = stake * odds / live_odds                                           # copertura che pareggia i due esiti (senza commissione)
    for _ in range(40):                                                    # correzione per la commissione sui soli profitti
        a, b = net(if_win(h)), net(if_lose(h))
        if abs(a - b) < 1e-9:
            break
        h += (a - b) / (live_odds if side == "back" else -live_odds)
    profit = min(net(if_win(h)), net(if_lose(h)))
    risk = stake if side == "back" else stake * (odds - 1)
    return {"hedge_stake": float(h), "profit": float(profit), "profit_pct": float(profit / risk * 100),
            "hedge_side": "banca" if side == "back" else "punta", "risk": float(risk)}
