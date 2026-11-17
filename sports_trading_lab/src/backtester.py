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
        ("model_prob", "Probabilità di vittoria secondo il modello Elo (%)", "Modello", "slider", 0, 100, 5),
        ("model_edge", "Vantaggio del modello sulla quota (punti %)", "Modello", "slider", -30, 30, 1),
        ("ip_set1", "Primo set", "In partita", "cat"),
        ("ip_set1_tb", "Primo set deciso al tie-break", "In partita", "cat"),
        ("ip_break1", "Primo break del primo set", "In partita", "cat"),
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
        ("model_prob", "Probabilità dell'esito secondo il modello Elo (%, solo 1X2)", "Modello", "slider", 0, 100, 5),
        ("model_edge", "Vantaggio del modello sulla quota (punti %, solo 1X2)", "Modello", "slider", -30, 30, 1),
        ("ip_ht_sel", "Squadra scelta all'intervallo (solo 1 e 2)", "In partita", "cat"),
        ("ip_ht", "Risultato all'intervallo", "In partita", "cat"),
        ("ip_ht_goals", "Gol nel primo tempo", "In partita", "cat"),
        ("pos_diff", "Posizioni di vantaggio della casa", "Confronto", "slider", -23, 23, 1),
        ("ppg_diff", "Differenza punti a partita ult. 5 (casa - ospite)", "Confronto", "slider", -3.0, 3.0, 0.2),
        ("goals5", "Gol totali medi nelle partite recenti", "Confronto", "slider", 0.0, 6.0, 0.2),
    ],
}
FILTER_COLUMNS = {sp: {f[0]: (f[1], f[3]) for f in specs} for sp, specs in FILTERS.items()}
FILTER_LABELS = {sp: {f[0]: (f[1] if f[2] in ("Torneo", "Competizione", "Calendario", "Quote del match", "Confronto", "Confronto diretto", "Modello", "In partita")
                            else f"{f[2]}: {f[1]}") for f in specs} for sp, specs in FILTERS.items()}


# condizioni note solo a partita iniziata: con questi filtri l'ingresso avviene dopo l'evento, a quota live stimata
INPLAY = {"tennis": {"ip_set1", "ip_set1_tb", "ip_break1"}, "football": {"ip_ht_sel", "ip_ht", "ip_ht_goals"}}
LIVE_MARGIN = 3.0          # margine di default sulle quote in partita (%)
BREAK_MOMENTUM = 0.4       # correzione (logit) per chi fa il primo break, misurata sui match punto per punto
ENTRY_LABEL = {"set1": "dopo il primo set", "break1": "dopo il primo break (o a fine primo set se non c'e' break)",
               "ht": "all'intervallo"}


DRAW_PEAK, DRAW_WIDTH, ELO_HOME, ELO_SCALE = 0.29, 250.0, 55.0, 0.75   # tarati sullo storico (minima log-loss)


def elo_win_prob(elo, opp_elo):
    """Probabilita' di vittoria dalla differenza Elo (formula logistica standard)."""
    return 1 / (1 + 10 ** (-(np.asarray(elo, dtype=float) - np.asarray(opp_elo, dtype=float)) / 400))


def football_model_probs(home_elo, away_elo, home_adv: float = ELO_HOME, scale: float = ELO_SCALE) -> np.ndarray:
    """Probabilita' (1, X, 2) del modello Elo: il pareggio e' piu' probabile quanto piu' le squadre sono vicine.
    `scale` riduce la differenza Elo: senza, il modello interno risulta troppo sicuro sulle favorite."""
    dr = (np.asarray(home_elo, dtype=float) - np.asarray(away_elo, dtype=float)) * scale + home_adv
    e = 1 / (1 + 10 ** (-dr / 400))
    d = DRAW_PEAK * np.exp(-dr ** 2 / (2 * DRAW_WIDTH ** 2))
    p = np.stack([np.clip(e - d / 2, 0.02, 0.96), d, np.clip(1 - e - d / 2, 0.02, 0.96)], axis=1)
    return p / p.sum(axis=1, keepdims=True)


def _norm_name(name) -> str:
    import re
    import unicodedata
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", s.lower())


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
    # primo set dal punteggio finale, solo se il set e' stato completato (6-0..6-4, 7-5, 7-6): un ritiro a meta'
    # primo set lascia il dato vuoto
    first = m["score"].astype(str).str.extract(r"^\s*(\d+)-(\d+)") if "score" in m.columns else pd.DataFrame({0: [], 1: []})
    g_w = pd.to_numeric(first[0], errors="coerce").reindex(m.index)
    g_l = pd.to_numeric(first[1], errors="coerce").reindex(m.index)
    hi, lo = np.maximum(g_w, g_l), np.minimum(g_w, g_l)
    complete = ((hi == 6) & (lo <= 4)) | ((hi == 7) & ((lo == 5) | (lo == 6)))
    set1 = {True: (g_w > g_l).astype(float).where(complete), False: (g_l > g_w).astype(float).where(complete)}
    tb = pd.Series(np.where(complete, np.where(lo == 6, "Sì", "No"), None), index=m.index)
    breaker = _get(m, "fb_breaker").map(lambda n: _norm_name(n) if isinstance(n, str) else n)
    has_events = _get(m, "_ev_match").notna().to_numpy()
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
            "elo": _get(m, f"{a}_elo"), "opp_elo": _get(m, f"{b}_elo"),
            "odds": m[f"{price}_{x}"], "opp_odds": m[f"{price}_{y}"],
            "close_odds": m[f"ps_{x}"], "_close_opp": m[f"ps_{y}"],
            "won": won, "set1_won": set1[won],
            "ip_set1": set1[won].map({1.0: "Vinto", 0.0: "Perso"}), "ip_set1_tb": tb,
            "ip_break1": np.where(~has_events, None, np.where(breaker.isna(), "Nessun break",
                                  np.where(breaker == m[f"{a}_name"].map(_norm_name), "Fatto",
                                           np.where(breaker == m[f"{b}_name"].map(_norm_name), "Subito", None)))),
            "_fb_game": _get(m, "fb_game"),
            "_fb_prob": np.where(breaker == m[f"{a}_name"].map(_norm_name), _get(m, "fb_prob"),
                                 np.where(breaker == m[f"{b}_name"].map(_norm_name), 1 - _get(m, "fb_prob"), np.nan)),
        }))
    s = pd.concat(sides, ignore_index=True)
    s["rank_adv"] = s["opp_rank"] - s["rank"]
    s["form_adv"] = s["form10"] - s["opp_form10"]
    s["h2h_diff"] = s["h2h_wins"] - s["h2h_losses"]
    s["model_prob"] = elo_win_prob(s["elo"], s["opp_elo"]) * 100
    s["model_edge"] = s["model_prob"] - (1 / s["odds"]) / (1 / s["odds"] + 1 / s["opp_odds"]) * 100   # modello meno quota senza margine
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
    model = football_model_probs(feats["home_elo"], feats["away_elo"]) * 100
    imp = 1 / np.stack([oh.to_numpy(dtype=float), od.to_numpy(dtype=float), oa.to_numpy(dtype=float)], axis=1)
    imp = imp / imp.sum(axis=1, keepdims=True) * 100
    hh, ha = _get(m, "hthg"), _get(m, "htag")
    ht_ok = hh.notna() & ha.notna()
    common["ht_home"], common["ht_away"] = hh, ha
    common["ip_ht"] = np.where(~ht_ok, None, np.where(hh > ha, "Casa avanti", np.where(hh < ha, "Ospite avanti", "Pari")))
    tot = hh + ha
    common["ip_ht_goals"] = np.where(~ht_ok, None, np.where(tot >= 3, "3+", tot.fillna(0).astype(int).astype(str)))
    for k, (name, suf, won) in enumerate(outcomes):
        d = pd.DataFrame({**common, "selection": name, "odds": m[f"{price}_{pre}{suf}"],
                          "close_odds": close[suf], "p_close": (1 / close[suf]) / inv, "won": won.to_numpy()})
        lead = (hh - ha) if name == "home" else (ha - hh) if name == "away" else None
        d["ip_ht_sel"] = None if lead is None else np.where(~ht_ok, None, np.where(lead > 0, "In vantaggio", np.where(lead < 0, "In svantaggio", "Pari")))
        d["model_prob"] = model[:, k] if market == "1x2" else np.nan
        d["model_edge"] = model[:, k] - imp[:, k] if market == "1x2" else np.nan
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
            tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
            if "tennis_match_events" in tables:
                q = """SELECT t.*, e.fb_breaker, e.fb_prob, e.fb_game, e.db_match_id AS _ev_match FROM tennis_matches t
                       LEFT JOIN tennis_match_events e ON e.db_match_id = t.match_id WHERE t.has_odds"""
            else:
                q = "SELECT * FROM tennis_matches WHERE has_odds"
            return tennis_selections(con.execute(q).df(), price)
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
    live: dict | None = None     # ingresso in partita: quote medie pre/live, frequenza reale, probabilita' stimata

    @property
    def equity_curve(self) -> pd.DataFrame:
        t = self.trades
        return pd.DataFrame({"trade": np.arange(1, len(t) + 1), "date": t["date"], "pnl": t["pnl"], "equity": t["equity"]})


def inplay_active(cfg: dict) -> set:
    """Filtri in partita attivi nella configurazione."""
    sport = cfg.get("sport", "tennis")
    return {c for c, r in (cfg.get("filters") or {}).items() if c in INPLAY.get(sport, set()) and r and r.get("in")}


def entry_point(cfg: dict) -> str | None:
    """Momento di ingresso imposto dai filtri in partita (il piu' tardivo tra quelli attivi)."""
    active = inplay_active(cfg)
    if not active:
        return None
    if cfg.get("sport") == "football":
        return "ht"
    return "set1" if active & {"ip_set1", "ip_set1_tb"} else "break1"


def live_prob(sel: pd.DataFrame, point: str) -> np.ndarray:
    """Probabilita' stimata della selezione nel momento di ingresso (senza margine).
    set1: modello a set tarato sui risultati;  break1: modello punto per punto con correzione per il primo
    break (se nel primo set non c'e' break, si usa la fine del primo set);  ht: modello Poisson sui gol."""
    if sel.empty:
        return np.zeros(0)
    if point == "ht":
        return cashout.football_checkpoint_prob(sel)
    after_set = cashout.tennis_checkpoint_prob(sel)
    if point == "set1":
        return after_set
    p = np.clip(sel["_fb_prob"].to_numpy(dtype=float), 1e-4, 1 - 1e-4) if "_fb_prob" in sel.columns else np.full(len(sel), np.nan)
    breaker = sel["ip_break1"].to_numpy() == "Fatto" if "ip_break1" in sel.columns else np.zeros(len(sel), bool)
    broken = sel["ip_break1"].to_numpy() == "Subito" if "ip_break1" in sel.columns else np.zeros(len(sel), bool)
    logit = np.log(p / (1 - p)) + np.where(breaker, BREAK_MOMENTUM, np.where(broken, -BREAK_MOMENTUM, 0.0))
    corrected = 1 / (1 + np.exp(-logit))
    return np.where(breaker | broken, corrected, after_set)


def live_odds(p: np.ndarray, side: str, margin_pct: float) -> np.ndarray:
    """Quota disponibile in partita: quota equa 1/p peggiorata del margine (piu' bassa per chi punta,
    piu' alta per chi banca)."""
    m = margin_pct / 100
    fair = 1 / np.clip(p, 1e-4, 1)
    return np.maximum(fair * (1 - m), 1.01) if side == "back" else np.maximum(fair * (1 + m), 1.01)


def backtest_selections(sel: pd.DataFrame, cfg: dict, bankroll: float = 100.0) -> BacktestResult:
    """Backtest su un dataframe di selezioni gia' caricato (utile per riusare la cache).
    Con filtri in partita attivi i range di quota restano sulle quote pre-partita, ma l'operazione entra dopo
    l'evento alla quota live stimata (vedi `live_prob`), cosi' il risultato non usa informazioni future."""
    picked = apply_filters(sel, cfg)
    point = entry_point(cfg)
    live = None
    side = cfg.get("side", "back")
    override = cfg.get("live_odds_override")
    override = float(override) if override not in (None, "", 0) and float(override) > 1 else None
    if point is not None and len(picked):
        p = live_prob(picked, point)
        picked = picked.assign(pre_odds=picked["odds"], live_prob=p)[np.isfinite(p)]
        p = picked["live_prob"].to_numpy()
        # quota live: stimata dal modello, oppure quella inserita a mano (uguale per tutte le operazioni)
        odds = np.full(len(picked), override) if override else live_odds(p, side, float(cfg.get("live_margin", LIVE_MARGIN)))
        picked = picked.assign(odds=odds, close_odds=np.nan, p_close=np.nan)
    trades = settle(picked, side, float(cfg.get("commission") or 0.0),
                    cfg.get("lay_mode", "liability"), float(cfg.get("stake") or 1.0))
    smart_skipped = False
    if cashout.is_active(cfg):
        if point is not None and cashout.settings(cfg)["mode"] == "smart":
            smart_skipped = True       # il punto di controllo del cash out coincide con l'ingresso: non ha senso
        else:
            trades = cashout.apply(trades, cfg)
    if point is not None and len(trades):
        pre_p = 1 / trades["pre_odds"]
        live = {"entry": point, "label": ENTRY_LABEL[point], "avg_pre_odds": float(trades["pre_odds"].mean()),
                "avg_live_odds": float(trades["odds"].median()), "real_win_pct": float(trades["won"].mean() * 100),
                "est_prob_pct": float(trades["live_prob"].mean() * 100), "pre_implied_pct": float(pre_p.mean() * 100),
                "margin_pct": float(cfg.get("live_margin", LIVE_MARGIN)), "smart_cashout_skipped": smart_skipped,
                "override": override,
                "est_live_odds": float(np.median(live_odds(trades["live_prob"].to_numpy(), side, float(cfg.get("live_margin", LIVE_MARGIN)))))}
    return BacktestResult(cfg, trades, compute_metrics(trades, bankroll), monthly_returns(trades), live)


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
        if col in INPLAY.get(sport, set()):      # condizioni in partita: il ROI a quote pre-partita non sarebbe reale
            continue
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


def walk_forward(trades: pd.DataFrame, n_bands: int = 5, min_trades: int = 40) -> tuple[pd.DataFrame, dict]:
    """Validazione walk-forward sulle fasce di quota.

    Le operazioni vengono divise in `n_bands` fasce di quota. Per ogni anno, a partire dal terzo, si guardano
    solo gli anni PRECEDENTI: si tengono le fasce che fin li' erano in utile (con almeno `min_trades`
    operazioni) e si applicano all'anno in corso. Il risultato e' cio' che avresti ottenuto scegliendo le
    fasce senza conoscere il futuro; il confronto e' giocare sempre tutte le fasce.
    Ritorna (tabella per anno, riepilogo)."""
    empty = {"trades": 0, "roi_pct": float("nan"), "naive_roi_pct": float("nan"), "years": 0}
    if len(trades) < 200 or trades["odds"].nunique() < n_bands:
        return pd.DataFrame(), empty
    t = trades[["date", "odds", "pnl", "risk"]].copy()
    t["year"] = t["date"].dt.year
    t["band"] = pd.qcut(t["odds"], n_bands, duplicates="drop")
    years = sorted(t["year"].unique())
    rows, picked = [], []
    for y in years[2:]:
        past, now = t[t["year"] < y], t[t["year"] == y]
        g = past.groupby("band", observed=True)[["pnl", "risk"]].sum().join(past.groupby("band", observed=True).size().rename("n"))
        good = g[(g["pnl"] > 0) & (g["n"] >= min_trades)].index
        use = now[now["band"].isin(good)]
        picked.append(use)
        roi = lambda x: float(x["pnl"].sum() / x["risk"].sum() * 100) if len(x) else float("nan")
        rows.append({"anno": int(y), "fasce_scelte": ", ".join(f"{b.left:.2f}-{b.right:.2f}" for b in good) or "nessuna",
                     "operazioni": len(use), "roi_pct": roi(use), "profitto": float(use["pnl"].sum()),
                     "tutte_le_fasce_op": len(now), "tutte_le_fasce_roi_pct": roi(now)})
    table = pd.DataFrame(rows)
    allp = pd.concat(picked) if picked else t.iloc[0:0]
    naive = t[t["year"].isin(years[2:])]
    summary = {"trades": int(len(allp)), "years": len(rows),
               "roi_pct": float(allp["pnl"].sum() / allp["risk"].sum() * 100) if len(allp) else float("nan"),
               "profit": float(allp["pnl"].sum()),
               "naive_roi_pct": float(naive["pnl"].sum() / naive["risk"].sum() * 100) if len(naive) else float("nan"),
               "naive_profit": float(naive["pnl"].sum()), "positive_years": int((table["profitto"] > 0).sum()) if len(table) else 0}
    return table, summary


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Intervallo di confidenza al 95% di una frequenza (metodo di Wilson)."""
    if n == 0:
        return float("nan"), float("nan")
    p = k / n
    den = 1 + z ** 2 / n
    mid = (p + z ** 2 / (2 * n)) / den
    half = z * np.sqrt(p * (1 - p) / n + z ** 2 / (4 * n ** 2)) / den
    return float(mid - half) * 100, float(mid + half) * 100


def conditional_stats(base: pd.DataFrame, sport: str, side: str = "back", margin_pct: float = LIVE_MARGIN) -> dict:
    """Quante volte vince la selezione in ogni situazione di partita. `base` = selezioni gia' filtrate SENZA
    i filtri in partita. Frequenze e intervalli sono conteggi esatti; prob./quota live sono stime."""
    out = {}
    if base.empty:
        return out
    groups = {"tennis": [("ip_set1", "set1", "Primo set"), ("ip_break1", "break1", "Primo break del primo set"),
                         ("ip_set1_tb", "set1", "Primo set al tie-break")],
              "football": [("ip_ht_sel", "ht", "Squadra scelta all'intervallo"), ("ip_ht", "ht", "Risultato all'intervallo"),
                           ("ip_ht_goals", "ht", "Gol nel primo tempo")]}[sport]
    for col, point, title in groups:
        if col not in base.columns:
            continue
        b = base[base[col].notna()]
        if b.empty:
            continue
        p = live_prob(b, point)
        b = b.assign(_p=p)
        rows = []
        for state, x in b.groupby(col, sort=True):
            n, k = len(x), int(x["won"].sum())
            lo, hi = wilson(k, n)
            pl = x["_p"].to_numpy(dtype=float)
            rows.append({"situazione": state, "partite": n, "vinte": k, "vince_pct": k / n * 100, "ic_basso": lo, "ic_alto": hi,
                         "implicita_pre_pct": float((1 / x["odds"]).mean() * 100),
                         "stimata_live_pct": float(np.nanmean(pl) * 100) if np.isfinite(pl).any() else np.nan,
                         "quota_pre_media": float(x["odds"].mean()),
                         "quota_live_mediana": float(np.nanmedian(live_odds(pl, side, margin_pct))) if np.isfinite(pl).any() else np.nan})
        out[title] = pd.DataFrame(rows)
    return out


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
