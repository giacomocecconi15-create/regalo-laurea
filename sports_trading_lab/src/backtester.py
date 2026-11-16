"""Motore di backtesting vettorizzato per tennis (testa a testa) e calcio (1X2, Over/Under 2.5).

Flusso:  load_selections() -> apply_filters() -> settle() -> compute_metrics()
oppure direttamente  run_backtest(cfg).

Ogni match viene esploso in "selezioni" (una riga per esito puntabile) che contengono solo
informazioni note prima del match (quote, ranking, form) piu' l'esito `won`. In questo modo i
filtri non possono introdurre look-ahead.

Configurazione di una strategia (dict serializzabile in JSON):
    {
      "sport": "tennis" | "football",
      "market": "h2h" | "1x2" | "ou25",
      "selection": ["favourite"],          # tennis: favourite/underdog; calcio: home/draw/away/over/under/favourite/underdog
      "side": "back" | "lay",
      "price": "avg",                      # bookmaker del prezzo di ingresso: ps, b365, max, avg, bfe
      "price_timing": "open" | "close",    # solo calcio
      "commission": 0.0,                   # commissione exchange sulle vincite nette (0.05 = 5%)
      "lay_mode": "liability" | "stake",   # banca: responsabilita' fissa 1u oppure puntata del backer 1u
      "odds_min": 1.5, "odds_max": 2.5,
      "date_from": "2021-01-01", "date_to": null,
      "exclude_retirements": false,        # solo tennis
      "filters": {"surface": {"in": ["Clay"]}, "rank": {"min": 1, "max": 50}},
      "cashout": {"mode": "off" | "manual" | "smart", ...}   # vedi src/cashout.py
    }
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import cashout  # noqa: E402
from src import config as C  # noqa: E402

MARKETS = {"tennis": ["h2h"], "football": ["1x2", "ou25"]}
SELECTIONS = {
    "h2h": ["favourite", "underdog"],
    "1x2": ["home", "draw", "away", "favourite", "underdog"],
    "ou25": ["over", "under"],
}
PRICE_BOOKS = {"tennis": C.TENNIS_BOOKS, "football": C.FOOT_BOOKS}
BOOK_LABELS = {"ps": "Pinnacle", "b365": "Bet365", "max": "Massimo mercato", "avg": "Media mercato", "bfe": "Betfair Exchange"}
CLOSE_PRIORITY = ["ps", "bfe", "avg"]  # riferimento di chiusura per il CLV

# Filtri disponibili: (colonna, etichetta, gruppo, tipo, min, max, passo)
#   cat    -> selezione multipla;  num -> min/max liberi;  slider -> intervallo su scala fissa
MONTHS = ["01 Gen", "02 Feb", "03 Mar", "04 Apr", "05 Mag", "06 Giu", "07 Lug", "08 Ago", "09 Set", "10 Ott", "11 Nov", "12 Dic"]
WEEKDAYS = ["1 Lun", "2 Mar", "3 Mer", "4 Gio", "5 Ven", "6 Sab", "7 Dom"]
FILTERS = {
    "tennis": [
        ("level", "Livello torneo", "Torneo", "cat"), ("series", "Serie", "Torneo", "cat"),
        ("competition", "Torneo", "Torneo", "cat"), ("surface", "Superficie", "Torneo", "cat"),
        ("court", "Indoor / Outdoor", "Torneo", "cat"), ("round", "Turno", "Torneo", "cat"),
        ("best_of", "Al meglio di (set)", "Torneo", "cat"),
        ("year", "Anno", "Calendario", "cat"), ("month", "Mese", "Calendario", "cat"),
        ("weekday", "Giorno della settimana", "Calendario", "cat"),
        ("rank", "Ranking", "Giocatore puntato", "num"), ("rank_points", "Punti ATP", "Giocatore puntato", "num"),
        ("form10", "Form: % vittorie ultime 10", "Giocatore puntato", "slider", 0, 100, 5),
        ("form_surf", "Form sulla superficie: % ultime 20", "Giocatore puntato", "slider", 0, 100, 5),
        ("rest_days", "Giorni dall'ultimo match", "Giocatore puntato", "slider", 1, 60, 1),
        ("matches14", "Match giocati negli ultimi 14 giorni", "Giocatore puntato", "slider", 0, 10, 1),
        ("srv_won10", "% punti vinti al servizio (ult. 10)", "Giocatore puntato", "slider", 50, 80, 1),
        ("ret_won10", "% punti vinti in risposta (ult. 10)", "Giocatore puntato", "slider", 25, 50, 1),
        ("ace10", "% ace per punto di servizio", "Giocatore puntato", "slider", 0, 25, 1),
        ("df10", "% doppi falli per punto di servizio", "Giocatore puntato", "slider", 0, 10, 0.5),
        ("age", "Età", "Giocatore puntato", "slider", 15, 45, 1), ("height", "Altezza (cm)", "Giocatore puntato", "slider", 160, 215, 1),
        ("hand", "Mano", "Giocatore puntato", "cat"), ("country", "Nazione", "Giocatore puntato", "cat"),
        ("opp_rank", "Ranking", "Avversario", "num"), ("opp_odds", "Quota", "Avversario", "num"),
        ("opp_form10", "Form: % vittorie ultime 10", "Avversario", "slider", 0, 100, 5),
        ("opp_form_surf", "Form sulla superficie: % ultime 20", "Avversario", "slider", 0, 100, 5),
        ("opp_rest_days", "Giorni dall'ultimo match", "Avversario", "slider", 1, 60, 1),
        ("opp_srv_won10", "% punti vinti al servizio (ult. 10)", "Avversario", "slider", 50, 80, 1),
        ("opp_ret_won10", "% punti vinti in risposta (ult. 10)", "Avversario", "slider", 25, 50, 1),
        ("opp_hand", "Mano", "Avversario", "cat"),
        ("rank_adv", "Vantaggio di ranking (avversario - giocatore)", "Confronto diretto", "num"),
        ("form_adv", "Vantaggio di form (punti %)", "Confronto diretto", "slider", -100, 100, 5),
        ("h2h_wins", "Precedenti vinti (dal 2019)", "Confronto diretto", "slider", 0, 15, 1),
        ("h2h_losses", "Precedenti persi (dal 2019)", "Confronto diretto", "slider", 0, 15, 1),
        ("h2h_diff", "Saldo dei precedenti", "Confronto diretto", "slider", -10, 10, 1),
    ],
    "football": [
        ("country", "Paese", "Competizione", "cat"), ("league", "Lega", "Competizione", "cat"),
        ("season", "Stagione", "Competizione", "cat"),
        ("month", "Mese", "Calendario", "cat"), ("weekday", "Giorno della settimana", "Calendario", "cat"),
        ("odds_home", "Quota 1", "Quote del match", "num"), ("odds_draw", "Quota X", "Quote del match", "num"),
        ("odds_away", "Quota 2", "Quote del match", "num"), ("odds_over", "Quota Over 2.5", "Quote del match", "num"),
        ("ah_line", "Handicap asiatico (linea casa)", "Quote del match", "slider", -3.0, 3.0, 0.25),
        ("home_pos", "Posizione in classifica", "Squadra di casa", "slider", 1, 24, 1),
        ("home_sppg", "Punti a partita in stagione", "Squadra di casa", "slider", 0.0, 3.0, 0.1),
        ("home_ppg5", "Punti a partita (ultime 5)", "Squadra di casa", "slider", 0.0, 3.0, 0.2),
        ("home_venue_ppg5", "Punti a partita (ultime 5 in casa)", "Squadra di casa", "slider", 0.0, 3.0, 0.2),
        ("home_gf5", "Gol fatti (media ult. 5)", "Squadra di casa", "slider", 0.0, 4.0, 0.2),
        ("home_ga5", "Gol subiti (media ult. 5)", "Squadra di casa", "slider", 0.0, 4.0, 0.2),
        ("home_sot5", "Tiri in porta fatti (media ult. 5)", "Squadra di casa", "slider", 0.0, 10.0, 0.5),
        ("home_sota5", "Tiri in porta subiti (media ult. 5)", "Squadra di casa", "slider", 0.0, 10.0, 0.5),
        ("home_corners5", "Calci d'angolo (media ult. 5)", "Squadra di casa", "slider", 0.0, 12.0, 0.5),
        ("home_rest_days", "Giorni di riposo", "Squadra di casa", "slider", 2, 30, 1),
        ("away_pos", "Posizione in classifica", "Squadra ospite", "slider", 1, 24, 1),
        ("away_sppg", "Punti a partita in stagione", "Squadra ospite", "slider", 0.0, 3.0, 0.1),
        ("away_ppg5", "Punti a partita (ultime 5)", "Squadra ospite", "slider", 0.0, 3.0, 0.2),
        ("away_venue_ppg5", "Punti a partita (ultime 5 in trasferta)", "Squadra ospite", "slider", 0.0, 3.0, 0.2),
        ("away_gf5", "Gol fatti (media ult. 5)", "Squadra ospite", "slider", 0.0, 4.0, 0.2),
        ("away_ga5", "Gol subiti (media ult. 5)", "Squadra ospite", "slider", 0.0, 4.0, 0.2),
        ("away_sot5", "Tiri in porta fatti (media ult. 5)", "Squadra ospite", "slider", 0.0, 10.0, 0.5),
        ("away_sota5", "Tiri in porta subiti (media ult. 5)", "Squadra ospite", "slider", 0.0, 10.0, 0.5),
        ("away_corners5", "Calci d'angolo (media ult. 5)", "Squadra ospite", "slider", 0.0, 12.0, 0.5),
        ("away_rest_days", "Giorni di riposo", "Squadra ospite", "slider", 2, 30, 1),
        ("pos_diff", "Posizioni di vantaggio della casa", "Confronto", "slider", -23, 23, 1),
        ("ppg_diff", "Differenza punti a partita ult. 5 (casa - ospite)", "Confronto", "slider", -3.0, 3.0, 0.2),
        ("goals5", "Gol totali medi nelle partite recenti", "Confronto", "slider", 0.0, 6.0, 0.2),
    ],
}
FILTER_COLUMNS = {sp: {f[0]: (f[1], f[3]) for f in specs} for sp, specs in FILTERS.items()}
FILTER_LABELS = {sp: {f[0]: (f[1] if f[2] in ("Torneo", "Competizione", "Calendario", "Quote del match", "Confronto", "Confronto diretto")
                            else f"{f[2]}: {f[1]}") for f in specs} for sp, specs in FILTERS.items()}


def _get(m: pd.DataFrame, name: str) -> pd.Series:
    """Colonna del database, o NaN se il database e' di una versione precedente."""
    return m[name] if name in m.columns else pd.Series(np.nan, index=m.index, dtype="float64")


def _calendar(dates: pd.Series) -> dict:
    return {"year": dates.dt.year.astype("Int64").astype(str),
            "month": dates.dt.month.map(lambda i: MONTHS[int(i) - 1] if pd.notna(i) else None),
            "weekday": dates.dt.day_of_week.map(lambda i: WEEKDAYS[int(i)] if pd.notna(i) else None)}


def connect(db_path: str | Path | None = None, read_only: bool = True) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(db_path or C.DB_PATH), read_only=read_only)


# --------------------------------------------------------------------------- selezioni
def tennis_selections(matches: pd.DataFrame, price: str = "avg") -> pd.DataFrame:
    """Due righe per match (una per giocatore). I walkover sono esclusi (scommessa nulla).
    Le percentuali (form, servizio, risposta) sono espresse in punti 0-100."""
    m = matches[matches["status"].fillna("Completed") != "Walkover"]
    dates = pd.to_datetime(m["match_date"])
    indoor = _get(m, "indoor")
    common = {
        "match_id": m["match_id"], "date": dates, "season": m["season"], **_calendar(dates),
        "circuit": m["circuit"], "competition": m["tourney_name"], "level": m["tourney_level"],
        "series": m["series"], "surface": m["surface"], "round": m["round"], "best_of": m["best_of"],
        "court": np.where(indoor.isna(), None, np.where(indoor.eq(True), "Indoor", "Outdoor")),
        "status": m["status"],
    }
    # esito del primo set (dato successivo al match: serve solo alla simulazione del cash out, non ai filtri)
    first = m["score"].astype(str).str.extract(r"^\s*(\d+)-(\d+)") if "score" in m.columns else pd.DataFrame({0: [], 1: []})
    g_w = pd.to_numeric(first[0], errors="coerce").reindex(m.index)
    g_l = pd.to_numeric(first[1], errors="coerce").reindex(m.index)
    set1 = {True: (g_w > g_l).astype(float).where(g_w.notna() & g_l.notna() & (g_w != g_l)),
            False: (g_l > g_w).astype(float).where(g_w.notna() & g_l.notna() & (g_w != g_l))}
    sides = []
    for a, b, won in (("winner", "loser", True), ("loser", "winner", False)):
        x, y = a[0], b[0]
        pct = lambda col: _get(m, col) * 100
        sides.append(pd.DataFrame({
            **common,
            "player": m[f"{a}_name"], "opponent": m[f"{b}_name"],
            "rank": m[f"{a}_rank"], "opp_rank": m[f"{b}_rank"], "rank_points": _get(m, f"{a}_rank_points"),
            "age": _get(m, f"{a}_age"), "height": _get(m, f"{a}_ht"),
            "hand": _get(m, f"{a}_hand"), "opp_hand": _get(m, f"{b}_hand"), "country": _get(m, f"{a}_ioc"),
            "form10": pct(f"{a}_form10"), "opp_form10": pct(f"{b}_form10"),
            "form_surf": pct(f"{a}_form_surf"), "opp_form_surf": pct(f"{b}_form_surf"),
            "rest_days": _get(m, f"{a}_rest_days"), "opp_rest_days": _get(m, f"{b}_rest_days"),
            "matches14": _get(m, f"{a}_matches14"),
            "h2h_wins": _get(m, f"{a}_h2h_wins"), "h2h_losses": _get(m, f"{b}_h2h_wins"),
            "srv_won10": pct(f"{a}_srv_won10"), "ret_won10": pct(f"{a}_ret_won10"),
            "ace10": pct(f"{a}_ace10"), "df10": pct(f"{a}_df10"),
            "opp_srv_won10": pct(f"{b}_srv_won10"), "opp_ret_won10": pct(f"{b}_ret_won10"),
            "odds": m[f"{price}_{x}"], "opp_odds": m[f"{price}_{y}"],
            "close_odds": m[f"ps_{x}"], "_close_opp": m[f"ps_{y}"],
            "won": won, "set1_won": set1[won],
        }))
    s = pd.concat(sides, ignore_index=True)
    s["rank_adv"] = s["opp_rank"] - s["rank"]
    s["form_adv"] = s["form10"] - s["opp_form10"]
    s["h2h_diff"] = s["h2h_wins"] - s["h2h_losses"]
    inv = 1 / s["close_odds"] + 1 / s["_close_opp"]
    s["p_close"] = (1 / s["close_odds"]) / inv  # probabilita' di chiusura senza margine
    s["selection"] = np.select([s["odds"] < s["opp_odds"], s["odds"] > s["opp_odds"]], ["favourite", "underdog"], "pick")
    s["event"] = s["player"] + " vs " + s["opponent"]
    return s.drop(columns="_close_opp")


def _close_reference(m: pd.DataFrame, suffixes: list[str]) -> tuple[pd.DataFrame, pd.Series]:
    """Quote di chiusura di riferimento (prima fonte completa tra Pinnacle, Betfair Exchange, media)."""
    out = pd.DataFrame(np.nan, index=m.index, columns=suffixes)
    src = pd.Series(None, index=m.index, dtype=object)
    for book in CLOSE_PRIORITY:
        cols = [f"{book}_c{s}" for s in suffixes]
        ok = m[cols].notna().all(axis=1) & src.isna()
        out.loc[ok] = m.loc[ok, cols].to_numpy()
        src[ok] = book
    return out, src


def football_selections(matches: pd.DataFrame, market: str = "1x2", price: str = "avg", timing: str = "open") -> pd.DataFrame:
    """Una riga per esito: 1X2 -> home/draw/away, ou25 -> over/under."""
    m = matches.reset_index(drop=True)
    pre = "c" if timing == "close" else ""
    if market == "1x2":
        outcomes = [("home", "h", m["ftr"] == "H"), ("draw", "d", m["ftr"] == "D"), ("away", "a", m["ftr"] == "A")]
    elif market == "ou25":
        outcomes = [("over", "o25", m["total_goals"] > 2.5), ("under", "u25", m["total_goals"] < 2.5)]
    else:
        raise ValueError(f"mercato non supportato: {market}")
    sfx = [o[1] for o in outcomes]
    close, close_src = _close_reference(m, sfx)
    inv = (1 / close).sum(axis=1, min_count=len(sfx))
    oh, od, oa = m[f"{price}_{pre}h"], m[f"{price}_{pre}d"], m[f"{price}_{pre}a"]
    dates = pd.to_datetime(m["match_date"])
    feats = {f"{side}_{k}": _get(m, f"{side}_{k}") for side in ("home", "away") for k in C.FOOT_FEATURE_KEYS}
    common = {
        "match_id": m["match_id"], "date": dates, "season": m["season"], **_calendar(dates),
        "country": m["country"], "league": m["league"], "competition": m["league"],
        "home_team": m["home_team"], "away_team": m["away_team"],
        "event": m["home_team"] + " - " + m["away_team"],
        "odds_home": oh, "odds_draw": od, "odds_away": oa, "odds_over": _get(m, f"{price}_{pre}o25"),
        "odds_under": _get(m, f"{price}_{pre}u25"),
        # risultato all'intervallo (dato successivo al match: serve solo alla simulazione del cash out)
        "ht_home": _get(m, "hthg"), "ht_away": _get(m, "htag"),
        "ah_line": _get(m, "ah_cline" if timing == "close" else "ah_line"),
        **feats,
        "ppg_diff": feats["home_ppg5"] - feats["away_ppg5"],
        "pos_diff": feats["away_pos"] - feats["home_pos"],
        "goals5": (feats["home_gf5"] + feats["home_ga5"] + feats["away_gf5"] + feats["away_ga5"]) / 2,
        "close_source": close_src,
    }
    parts = []
    for name, suf, won in outcomes:
        d = pd.DataFrame({**common, "selection": name, "odds": m[f"{price}_{pre}{suf}"],
                          "close_odds": close[suf], "p_close": (1 / close[suf]) / inv, "won": won.to_numpy()})
        if market == "1x2" and name != "draw":
            other = oa if name == "home" else oh
            d["role"] = np.select([d["odds"] < other, d["odds"] > other], ["favourite", "underdog"], "pick")
        else:
            d["role"] = name
        parts.append(d)
    return pd.concat(parts, ignore_index=True)


def load_selections(sport: str, market: str | None = None, price: str = "avg", timing: str = "open",
                    con: duckdb.DuckDBPyConnection | None = None) -> pd.DataFrame:
    own = con is None
    con = con or connect()
    try:
        if sport == "tennis":
            return tennis_selections(con.execute("SELECT * FROM tennis_matches WHERE has_odds").df(), price)
        if sport == "football":
            return football_selections(con.execute("SELECT * FROM football_matches").df(), market or "1x2", price, timing)
        raise ValueError(f"sport non supportato: {sport}")
    finally:
        if own:
            con.close()


# --------------------------------------------------------------------------- filtri
def apply_filters(sel: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Applica selezione, range di quote, date e filtri generici {colonna: {min,max} | {in:[...]}}."""
    mask = sel["odds"].notna().to_numpy().copy()
    wanted = cfg.get("selection")
    if wanted:
        wanted = [wanted] if isinstance(wanted, str) else list(wanted)
        m = sel["selection"].isin(wanted)
        if "role" in sel.columns:
            m |= sel["role"].isin(wanted)
        mask &= m.to_numpy()
    if cfg.get("odds_min") is not None:
        mask &= (sel["odds"] >= float(cfg["odds_min"])).to_numpy()
    if cfg.get("odds_max") is not None:
        mask &= (sel["odds"] <= float(cfg["odds_max"])).to_numpy()
    if cfg.get("date_from"):
        mask &= (sel["date"] >= pd.Timestamp(cfg["date_from"])).to_numpy()
    if cfg.get("date_to"):
        mask &= (sel["date"] <= pd.Timestamp(cfg["date_to"])).to_numpy()
    if cfg.get("exclude_retirements") and "status" in sel.columns:
        mask &= (sel["status"] != "Retired").to_numpy()
    for col, rule in (cfg.get("filters") or {}).items():
        if col not in sel.columns or not rule:
            continue
        if rule.get("in"):
            mask &= sel[col].isin(rule["in"]).to_numpy()
        if rule.get("min") is not None:
            mask &= (sel[col] >= rule["min"]).to_numpy()  # NaN -> escluso
        if rule.get("max") is not None:
            mask &= (sel[col] <= rule["max"]).to_numpy()
    return sel[mask]


# --------------------------------------------------------------------------- settlement
def settle(sel: pd.DataFrame, side: str = "back", commission: float = 0.0, lay_mode: str = "liability",
           stake: float = 1.0) -> pd.DataFrame:
    """Calcola rischio, P&L e CLV di ogni operazione (vettorizzato).

    back : rischio = stake;  vinta -> stake*(quota-1)*(1-comm);  persa -> -stake
    lay  : 'liability' -> rischio = stake (responsabilita');  puntata del backer = stake/(quota-1)
           'stake'     -> puntata del backer = stake;         rischio = stake*(quota-1)
           selezione perdente -> +puntata_backer*(1-comm);  selezione vincente -> -rischio
    CLV% : back = quota/chiusura - 1;  lay = 1 - quota/chiusura
    EV%  : valore atteso per unita' di rischio usando la probabilita' di chiusura senza margine.
    """
    if side not in ("back", "lay"):
        raise ValueError("side deve essere 'back' o 'lay'")
    t = sel.sort_values(["date", "match_id", "selection"], kind="stable").reset_index(drop=True)
    o = t["odds"].to_numpy(dtype=float)
    won = t["won"].to_numpy(dtype=bool)
    c = t["close_odds"].to_numpy(dtype=float) if "close_odds" in t else np.full(len(t), np.nan)
    p = t["p_close"].to_numpy(dtype=float) if "p_close" in t else np.full(len(t), np.nan)
    if side == "back":
        risk = np.full(len(t), float(stake))
        pnl = np.where(won, stake * (o - 1) * (1 - commission), -stake)
        clv = o / c - 1
        win_ret = (o - 1) * (1 - commission)
        ev = p * win_ret - (1 - p)
    else:
        backer = np.full(len(t), float(stake)) if lay_mode == "stake" else stake / (o - 1)
        risk = backer * (o - 1)
        pnl = np.where(won, -risk, backer * (1 - commission))
        clv = 1 - o / c
        win_ret = backer * (1 - commission) / risk
        ev = (1 - p) * win_ret - p
    t["side"] = side
    t["risk"] = risk
    t["pnl"] = pnl
    t["win_return"] = win_ret  # vincita netta potenziale per unita' di rischio (serve a Kelly)
    t["trade_won"] = pnl > 0
    t["clv_pct"] = clv * 100
    t["ev_close_pct"] = ev * 100
    t["equity"] = np.cumsum(pnl)
    return t


# --------------------------------------------------------------------------- metriche
def max_drawdown(pnl: np.ndarray, bankroll: float = 100.0) -> tuple[float, float]:
    """Max drawdown picco-valle della curva cumulata: (unita', % del bankroll al picco, max 100)."""
    if len(pnl) == 0:
        return 0.0, 0.0
    eq = bankroll + np.concatenate([[0.0], np.cumsum(pnl)])
    peak = np.maximum.accumulate(eq)
    dd = peak - eq
    return float(dd.max()), float(min((dd / peak).max(), 1.0) * 100)  # 100% = bankroll azzerato


def longest_losing_streak(pnl: np.ndarray) -> int:
    lost = np.asarray(pnl) < 0
    if not lost.any():
        return 0
    padded = np.concatenate([[False], lost, [False]]).astype(np.int8)
    edges = np.flatnonzero(np.diff(padded))
    return int((edges[1::2] - edges[::2]).max())


def monthly_returns(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(columns=["month", "trades", "profit", "staked", "yield_pct"])
    g = trades.groupby(trades["date"].dt.to_period("M"))
    out = pd.DataFrame({"trades": g.size(), "profit": g["pnl"].sum(), "staked": g["risk"].sum()})
    out["yield_pct"] = out["profit"] / out["staked"] * 100
    out.index = out.index.astype(str)
    return out.rename_axis("month").reset_index()


def compute_metrics(trades: pd.DataFrame, bankroll: float = 100.0) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0, "wins": 0, "win_rate_pct": 0.0, "profit": 0.0, "staked": 0.0, "roi_pct": 0.0,
                "avg_odds": float("nan"), "max_dd_units": 0.0, "max_dd_pct": 0.0, "longest_losing_streak": 0,
                "clv_pct": float("nan"), "ev_close_pct": float("nan"), "clv_coverage_pct": 0.0, "t_stat": float("nan")}
    pnl = trades["pnl"].to_numpy(dtype=float)
    risk = trades["risk"].to_numpy(dtype=float)
    dd_u, dd_pct = max_drawdown(pnl, bankroll)
    clv = trades["clv_pct"]
    r = pnl / risk
    sd = r.std(ddof=1) if n > 1 else 0.0
    return {
        "trades": n,
        "wins": int((pnl > 0).sum()),
        "win_rate_pct": float((pnl > 0).mean() * 100),
        "profit": float(pnl.sum()),
        "staked": float(risk.sum()),
        "roi_pct": float(pnl.sum() / risk.sum() * 100),  # yield: profitto / totale rischiato
        "avg_odds": float(trades["odds"].mean()),
        "max_dd_units": dd_u,
        "max_dd_pct": dd_pct,
        "longest_losing_streak": longest_losing_streak(pnl),
        "clv_pct": float(clv.mean()) if clv.notna().any() else float("nan"),
        "ev_close_pct": float(trades["ev_close_pct"].mean()) if trades["ev_close_pct"].notna().any() else float("nan"),
        "clv_coverage_pct": float(clv.notna().mean() * 100),
        "t_stat": float(r.mean() / sd * np.sqrt(n)) if sd > 0 else float("nan"),
    }


@dataclass
class BacktestResult:
    config: dict
    trades: pd.DataFrame
    metrics: dict
    monthly: pd.DataFrame

    @property
    def equity_curve(self) -> pd.DataFrame:
        t = self.trades
        return pd.DataFrame({"trade": np.arange(1, len(t) + 1), "date": t["date"], "pnl": t["pnl"], "equity": t["equity"]})


def backtest_selections(sel: pd.DataFrame, cfg: dict, bankroll: float = 100.0) -> BacktestResult:
    """Backtest su un dataframe di selezioni gia' caricato (utile per riusare la cache)."""
    picked = apply_filters(sel, cfg)
    trades = settle(picked, cfg.get("side", "back"), float(cfg.get("commission") or 0.0),
                    cfg.get("lay_mode", "liability"), float(cfg.get("stake") or 1.0))
    if cashout.is_active(cfg):
        trades = cashout.apply(trades, cfg)
    return BacktestResult(cfg, trades, compute_metrics(trades, bankroll), monthly_returns(trades))


def run_backtest(cfg: dict, con: duckdb.DuckDBPyConnection | None = None, bankroll: float = 100.0) -> BacktestResult:
    sport = cfg["sport"]
    market = cfg.get("market") or MARKETS[sport][0]
    sel = load_selections(sport, market, cfg.get("price", "avg"), cfg.get("price_timing", "open"), con)
    return backtest_selections(sel, cfg, bankroll)


# --------------------------------------------------------------------------- analisi
ODDS_BINS = [1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0, 6.0, 1001.0]
ODDS_LABELS = ["1.01-1.25", "1.26-1.50", "1.51-1.75", "1.76-2.00", "2.01-2.50", "2.51-3.00", "3.01-4.00", "4.01-6.00", "oltre 6.00"]


def _aggregate(trades: pd.DataFrame, key) -> pd.DataFrame:
    """Statistiche per gruppo: operazioni, win rate, profitto, ROI, quota media, CLV, t-stat."""
    t = trades.assign(_r=trades["pnl"] / trades["risk"], _w=trades["pnl"] > 0)
    g = t.groupby(key, observed=True, sort=True)
    out = pd.DataFrame({
        "trades": g.size(), "win_rate_pct": g["_w"].mean() * 100, "profit": g["pnl"].sum(),
        "staked": g["risk"].sum(), "avg_odds": g["odds"].mean(), "clv_pct": g["clv_pct"].mean(),
    })
    out["roi_pct"] = out["profit"] / out["staked"] * 100
    sd = g["_r"].std(ddof=1)
    out["t_stat"] = (g["_r"].mean() / sd * np.sqrt(out["trades"])).where(sd > 0)
    return out[["trades", "win_rate_pct", "profit", "roi_pct", "avg_odds", "clv_pct", "t_stat"]]


def breakdown(trades: pd.DataFrame, by: str) -> pd.DataFrame:
    """Risultati scomposti per una dimensione ('odds_bucket' = fasce di quota)."""
    if trades.empty:
        return pd.DataFrame()
    if by == "odds_bucket":
        key = pd.cut(trades["odds"], ODDS_BINS, labels=ODDS_LABELS).rename("odds_bucket")
    else:
        key = trades[by]
    return _aggregate(trades, key).rename_axis(by).reset_index()


def segment_scan(trades: pd.DataFrame, sport: str, min_trades: int = 100, buckets: int = 4) -> pd.DataFrame:
    """Scompone le operazioni lungo tutte le dimensioni filtrabili e misura ogni segmento.
    `roi_1` / `roi_2`: ROI nella prima e nella seconda meta' cronologica (stabilita' nel tempo).
    `rule` e' il filtro da applicare per isolare il segmento."""
    rows = []
    if trades.empty:
        return pd.DataFrame(columns=["col", "dimensione", "segmento", "trades", "roi_pct", "clv_pct", "t_stat", "roi_1", "roi_2", "rule"])
    t = trades.reset_index(drop=True)
    half = np.where(np.arange(len(t)) < len(t) / 2, 1, 2)
    for spec in FILTERS[sport]:
        col, kind = spec[0], spec[3]
        if col not in t.columns or t[col].notna().sum() < min_trades:
            continue
        if kind == "cat":
            key, rules = t[col], None
        else:
            try:
                key, edges = pd.qcut(t[col], buckets, retbins=True, duplicates="drop")
            except ValueError:
                continue
            if len(edges) < 3:
                continue
            # estremi = valori realmente presenti nella fascia, cosi' il filtro riproduce esattamente il segmento
            span = t.groupby(key, observed=True)[col].agg(["min", "max"])
            rules = {iv: {"min": float(span.loc[iv, "min"]), "max": float(span.loc[iv, "max"])} for iv in span.index}
            rules[span.index[0]]["min"] = None
            rules[span.index[-1]]["max"] = None
        agg = _aggregate(t, key.rename("seg"))
        agg = agg[agg["trades"] >= min_trades]
        if agg.empty:
            continue
        h = t.assign(_h=half, _seg=key).groupby(["_seg", "_h"], observed=True)[["pnl", "risk"]].sum()
        roi_h = (h["pnl"] / h["risk"] * 100).unstack("_h")
        for seg, r in agg.iterrows():
            if rules is None:
                label, rule = str(seg), {"in": [seg]}
            else:
                rule = rules[seg]
                lo = "min" if rule["min"] is None else f"{rule['min']:g}"
                hi = "max" if rule["max"] is None else f"{rule['max']:g}"
                label = f"{lo} .. {hi}"
            rows.append({"col": col, "dimensione": FILTER_LABELS[sport][col], "segmento": label,
                         "trades": int(r["trades"]), "roi_pct": r["roi_pct"], "clv_pct": r["clv_pct"], "t_stat": r["t_stat"],
                         "roi_1": roi_h.loc[seg].get(1, np.nan) if seg in roi_h.index else np.nan,
                         "roi_2": roi_h.loc[seg].get(2, np.nan) if seg in roi_h.index else np.nan, "rule": rule})
    out = pd.DataFrame(rows)
    return out.sort_values("t_stat", ascending=False).reset_index(drop=True) if len(out) else out


def roi_confidence(trades: pd.DataFrame, n_boot: int = 1000, seed: int = 7) -> dict:
    """Intervallo di confidenza 95% del ROI e probabilita' che il ROI vero sia positivo.
    Bootstrap fino a 5.000 operazioni, approssimazione normale oltre (stesso risultato, piu' leggero)."""
    n = len(trades)
    if n < 2:
        return {"lo": float("nan"), "hi": float("nan"), "p_positive": float("nan"), "method": "n/d"}
    pnl, risk = trades["pnl"].to_numpy(dtype=float), trades["risk"].to_numpy(dtype=float)
    if n <= 5000:
        rng = np.random.default_rng(seed)
        idx = rng.integers(0, n, size=(n_boot, n))
        rois = pnl[idx].sum(axis=1) / risk[idx].sum(axis=1) * 100
        lo, hi = np.percentile(rois, [2.5, 97.5])
        return {"lo": float(lo), "hi": float(hi), "p_positive": float((rois > 0).mean() * 100), "method": "bootstrap"}
    from math import erf, sqrt
    roi = pnl.sum() / risk.sum()
    se = np.std(pnl - roi * risk, ddof=1) * np.sqrt(n) / risk.sum()
    z = roi / se if se > 0 else 0.0
    return {"lo": float((roi - 1.96 * se) * 100), "hi": float((roi + 1.96 * se) * 100),
            "p_positive": float(0.5 * (1 + erf(z / sqrt(2))) * 100), "method": "normale"}


def split_metrics(trades: pd.DataFrame, frac: float = 0.7, bankroll: float = 100.0) -> tuple[pd.Timestamp | None, dict, dict]:
    """Divide le operazioni in ordine di tempo: primo `frac` (campione di studio) e resto (verifica)."""
    n = len(trades)
    cut = int(round(n * frac))
    if n == 0 or cut == 0 or cut >= n:
        return None, compute_metrics(trades, bankroll), compute_metrics(trades.iloc[0:0], bankroll)
    a, b = trades.iloc[:cut].copy(), trades.iloc[cut:].copy()
    b["equity"] = b["pnl"].cumsum()
    return trades["date"].iloc[cut], compute_metrics(a, bankroll), compute_metrics(b, bankroll)


def robustness_checks(metrics: dict, oos: dict, yearly: pd.DataFrame, conf: dict) -> list[tuple[str, str, str]]:
    """Lista di controlli (nome, esito 'ok'|'warn'|'bad', dettaglio) per valutare se fidarsi del risultato."""
    n = metrics["trades"]
    checks = [("Dimensione del campione", "ok" if n >= 500 else "warn" if n >= 150 else "bad",
               f"{n} operazioni (meglio almeno 500)")]
    t = metrics["t_stat"]
    checks.append(("Significatività statistica", "ok" if t >= 2 else "warn" if t >= 1 else "bad",
                   f"t-stat {t:.2f} (sopra 2 è difficile da spiegare col caso)" if np.isfinite(t) else "non calcolabile"))
    if np.isfinite(conf.get("lo", float("nan"))):
        checks.append(("Intervallo di confidenza del ROI", "ok" if conf["lo"] > 0 else "warn" if conf["p_positive"] >= 80 else "bad",
                       f"95%: da {conf['lo']:+.1f}% a {conf['hi']:+.1f}% - probabilità ROI positivo {conf['p_positive']:.0f}%"))
    clv = metrics["clv_pct"]
    if np.isfinite(clv):
        checks.append(("Closing Line Value", "ok" if clv > 0.5 else "warn" if clv > -0.5 else "bad",
                       f"CLV medio {clv:+.2f}% (positivo = batti la chiusura)"))
    if oos["trades"] >= 30:
        checks.append(("Tenuta fuori campione", "ok" if oos["roi_pct"] > 0 else "bad",
                       f"ROI {oos['roi_pct']:+.2f}% sulle ultime {oos['trades']} operazioni"))
    if len(yearly) >= 3:
        pos = int((yearly["profit"] > 0).sum())
        share = pos / len(yearly)
        checks.append(("Costanza negli anni", "ok" if share >= 0.7 else "warn" if share >= 0.5 else "bad",
                       f"{pos} anni positivi su {len(yearly)}"))
    return checks


if __name__ == "__main__":
    demo = {"sport": "tennis", "market": "h2h", "selection": ["favourite"], "side": "back", "price": "max",
            "odds_min": 1.2, "odds_max": 1.8, "filters": {"surface": {"in": ["Clay"]}}}
    res = run_backtest(demo)
    for k, v in res.metrics.items():
        print(f"{k:>24}: {v:,.2f}" if isinstance(v, float) else f"{k:>24}: {v}")


    print(res.monthly.tail(6).to_string(index=False))
