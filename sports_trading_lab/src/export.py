"""Esportazione Excel completa del backtest corrente, pensata anche per un agente AI esterno.

Fogli:
  Riepilogo       strategia (filtri, ingresso, uscita, adjustment) e analisi completa: risultati, robustezza
                  (intervallo di confidenza, studio/verifica, controlli), anni, uscite, stima live
  Operazioni      una riga per operazione con tutte le colonne del backtest (quote pre-partita, live, corrette, di
                  uscita, di chiusura, eventi in partita, risultato) e il game per game in forma compatta
  Punto per punto una riga per game di servizio (partite registrate punto per punto): chi serve, punteggio prima del
                  game, punti, esito, palle break, vantaggi, probabilita' e quota stimate dopo il game
  Legenda         significato delle colonne e delle convenzioni
Tutte le righe di Operazioni e Punto per punto hanno la stessa `chiave` (partita|giocatore o partita|esito).
"""
from __future__ import annotations

import io
import json
from datetime import datetime

import numpy as np
import pandas as pd
from openpyxl import Workbook

from src import backtester as bt
from src import cashout as co

METRIC_LABELS = {
    "trades": "Operazioni", "wins": "Operazioni in utile", "win_rate_pct": "Win rate %", "profit": "Profitto netto (u)",
    "staked": "Totale rischiato (u)", "roi_pct": "ROI / yield %", "avg_odds": "Quota media", "max_dd_units": "Max drawdown (u)",
    "max_dd_pct": "Max drawdown % della banca", "longest_losing_streak": "Serie negativa piu' lunga",
    "clv_pct": "CLV medio %", "ev_close_pct": "Valore atteso alla chiusura %", "clv_coverage_pct": "Operazioni con CLV %",
    "t_stat": "t-stat",
}
# nomi leggibili per le colonne interne (le altre restano con il nome del backtest, spiegato nella Legenda)
RENAME = {"_fb_game": "game_primo_break", "_fb_prob": "prob_modello_dopo_primo_break", "_cb_game": "game_controbreak",
          "_cb_prob": "prob_modello_dopo_controbreak", "_press_t": "momento_sotto_pressione",
          "_press_prob": "prob_modello_sotto_pressione", "_pressing_t": "momento_mette_sotto_pressione",
          "_pressing_prob": "prob_modello_avversario_sotto_pressione", "_breaks": "break_json", "_pp": "punto_per_punto",
          "entry_t": "momento_ingresso"}
DROP = {"equity"}
LEGEND = [
    ("chiave", "partita|giocatore (tennis) o partita|esito (calcio): collega Operazioni e Punto per punto"),
    ("date, event, competition, round, surface", "data, partita, torneo o campionato, turno, superficie"),
    ("player, opponent, selection", "giocatore su cui si opera, avversario, ruolo pre-partita (favourite/underdog o esito 1X2/OU)"),
    ("side", "back = punta, lay = banca"),
    ("odds", "quota di ingresso usata nel backtest (pre-partita, live stimata o corretta a mano)"),
    ("book_odds / pre_odds", "quota pre-partita del bookmaker scelto (prima di stime live o correzioni)"),
    ("model_odds, odds_adj", "quota prima della correzione manuale; odds_adj = vero se la quota e' stata corretta a mano"),
    ("opp_odds, odds_home/draw/away/over/under", "quote pre-partita dell'avversario o degli altri esiti"),
    ("close_odds, p_close, clv_pct", "quota di chiusura Pinnacle (o di riferimento), probabilita' senza margine, CLV %"),
    ("live_prob", "probabilita' stimata della selezione nel momento dell'ingresso in partita (modello tarato)"),
    ("risk", "importo rischiato: puntata (back) o responsabilita' (lay)"),
    ("pnl, pnl_hold", "profitto in unita' dell'operazione; tenendo fino alla fine"),
    ("cashout, exit_point, exit_odds", "motivo dell'uscita (tp, sl, man = manuale, vuoto = tenuta), momento ed esito, quota di uscita"),
    ("won, trade_won", "la selezione ha vinto; l'operazione e' in utile"),
    ("score", "risultato finale dal punto di vista del giocatore (calcio: finale e, tra parentesi, primo tempo)"),
    ("ip_set1, ip_break1, ip_cbreak, ip_press, ip_pressing", "esiti reali in partita: primo set, primo break, controbreak, sotto pressione, mette sotto pressione"),
    ("ip_ht, ip_ht_sel, ip_ht_goals", "calcio: risultato all'intervallo, squadra scelta all'intervallo, gol nel primo tempo"),
    ("momento_*, game_*", "tempi come set*100 + game (fine set = set*100+99; 45 = intervallo; 0 = pre-partita)"),
    ("break_json", "tutti i break del match: [set, game, chi breakka, probabilita' del modello di chi breakka dopo il game]"),
    ("game_per_game", "sequenza compatta dei game: S1G3 = set 1 game 3; G> = serve il giocatore, A> = serve l'avversario; "
                      "tenuto / break; p = probabilita' del giocatore dopo il game (modello)"),
    ("Punto per punto: punti", "punteggio dopo ogni punto dal lato di chi serve (V = vantaggio); sequenza: S = punto di chi serve, R = punto di chi risponde"),
    ("Punto per punto: prob_giocatore_dopo / quota_stimata_dopo", "stima del modello a punti indipendenti (non quote reali) dopo il game"),
    ("Unita'", "ogni operazione rischia 1 unita'; ROI = profitto / totale rischiato"),
]


def _clean(v):
    """Valore scrivibile in una cella (niente NaN, numpy, date con fuso, testi oltre il limite di Excel)."""
    if v is None:
        return None
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        return None if not np.isfinite(v) else float(v)
    if isinstance(v, pd.Timestamp):
        return None if pd.isna(v) else v.to_pydatetime().replace(tzinfo=None)
    if isinstance(v, (dict, list, tuple)):
        v = json.dumps(v, ensure_ascii=False, default=str)
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    s = str(v) if not isinstance(v, (int, float, datetime)) else v
    return s[:32000] if isinstance(s, str) else s


def _table(ws, df: pd.DataFrame) -> None:
    ws.append([str(c) for c in df.columns])
    for row in df.itertuples(index=False, name=None):
        ws.append([_clean(v) for v in row])


def _section(ws, title: str) -> None:
    ws.append([])
    ws.append([title])


def game_rows(trades: pd.DataFrame, games: pd.DataFrame | None) -> pd.DataFrame:
    """Game di servizio di ogni operazione con il punto per punto, dal punto di vista del giocatore dell'operazione."""
    from src import scalping as sc
    cols = ["chiave", "match_id", "giocatore", "set", "game", "serve", "serve_il_giocatore", "game_prima_giocatore",
            "game_prima_avversario", "sequenza", "punti", "esito", "palle_break", "vantaggi", "prob_giocatore_dopo",
            "quota_stimata_dopo"]
    if games is None or games.empty or "player" not in trades.columns or trades.empty:
        return pd.DataFrame(columns=cols)
    base = trades.assign(_mid=trades["match_id"].astype(str).str.split("|").str[0])
    g = games[games["db_match_id"].isin(set(base["_mid"]))].sort_values(["db_match_id", "set_no", "game_no"], kind="stable")
    if g.empty:
        return pd.DataFrame(columns=cols)
    by_match = {mid: x for mid, x in g.groupby("db_match_id", sort=False)}
    rows = []
    for key, mid, player in zip(bt.trade_key(base), base["_mid"], base["player"]):
        x = by_match.get(mid)
        if x is None:
            continue
        me = bt._norm_name(player)
        for r in x.itertuples():
            mine = bt._norm_name(r.server) == me
            held = bool(r.held)
            p_srv = float(r.v_hold if held else r.v_break)
            p_me = p_srv if mine else 1 - p_srv
            pts, bp, deuce = sc.point_scores(r.seq)
            rows.append({"chiave": key, "match_id": mid, "giocatore": player, "set": int(r.set_no), "game": int(r.game_no),
                         "serve": r.server, "serve_il_giocatore": mine,
                         "game_prima_giocatore": int(r.srv_games if mine else r.ret_games),
                         "game_prima_avversario": int(r.ret_games if mine else r.srv_games),
                         "sequenza": str(r.seq).replace("1", "S").replace("0", "R"), "punti": pts,
                         "esito": "tenuto" if held else ("break subito" if mine else "break fatto"),
                         "palle_break": bp, "vantaggi": deuce, "prob_giocatore_dopo": round(p_me, 4),
                         "quota_stimata_dopo": round(1 / p_me, 3) if p_me > 0 else None})
    return pd.DataFrame(rows, columns=cols)


def compact_games(pp: pd.DataFrame) -> dict:
    """chiave -> sequenza compatta dei game, per la colonna game_per_game del foglio Operazioni."""
    out = {}
    for key, x in pp.groupby("chiave", sort=False):
        parts = [f"S{s}G{g} {'G' if mine else 'A'}>{'tenuto' if e == 'tenuto' else 'break'} p={p:.2f}"
                 for s, g, mine, e, p in zip(x["set"], x["game"], x["serve_il_giocatore"], x["esito"], x["prob_giocatore_dopo"])]
        out[key] = " | ".join(parts)
    return out


def build_workbook(cfg: dict, res: bt.BacktestResult, description: str = "", games: pd.DataFrame | None = None,
                   bankroll: float = 100.0) -> bytes:
    trades = res.trades
    wb = Workbook(write_only=True)

    # ------------------------------------------------------------------ Riepilogo
    ws = wb.create_sheet("Riepilogo")
    ws.append(["Sports Trading Lab - backtest esportato il", datetime.now().strftime("%d/%m/%Y %H:%M")])
    ws.append(["Strategia", description])
    _section(ws, "IMPOSTAZIONI")
    for k in ("sport", "market", "selection", "side", "price", "price_timing", "odds_min", "odds_max", "date_from", "date_to",
              "commission", "lay_mode", "lay_spread", "exclude_retirements", "live_margin", "live_odds_override",
              "live_odds_min", "live_odds_max"):
        if k in cfg:
            ws.append([k, _clean(cfg[k])])
    point = bt.entry_point(cfg)
    ws.append(["ingresso", bt.ENTRY_LABEL.get(point, "pre-partita") if point else "pre-partita"])
    _section(ws, "FILTRI")
    labels = bt.FILTER_LABELS.get(cfg.get("sport", "tennis"), {})
    for col, rule in (cfg.get("filters") or {}).items():
        if not rule:
            continue
        val = ", ".join(map(str, rule["in"])) if rule.get("in") else f"{rule.get('min', 'min')} .. {rule.get('max', 'max')}"
        ws.append([labels.get(col, col), val, col])
    _section(ws, "USCITA")
    c = co.settings(cfg)
    for k in ("mode", "method", "tp_event", "tp_quote", "tp", "sl_event", "sl_quote", "sl", "margin_pct"):
        ws.append([k, _clean(c.get(k))])
    _section(ws, "ADJUSTMENT")
    ws.append(["anni esclusi", ", ".join(map(str, cfg.get("exclude_years") or [])) or "nessuno"])
    ws.append(["partite escluse", len(cfg.get("exclude_matches") or [])])
    for k, v in (cfg.get("exclude_matches") and {"elenco partite escluse": ", ".join(cfg["exclude_matches"])} or {}).items():
        ws.append([k, v])
    ws.append(["quote di ingresso corrette", _clean(cfg.get("odds_adjust") or {})])
    ws.append(["chiusure manuali", _clean(cfg.get("exit_adjust") or {})])

    _section(ws, "RISULTATI")
    for k, v in res.metrics.items():
        ws.append([METRIC_LABELS.get(k, k), _clean(v)])
    if len(trades):
        tr = trades if "year" in trades.columns else trades.assign(year=pd.to_datetime(trades["date"]).dt.year)
        _section(ws, "ROBUSTEZZA")
        conf = bt.roi_confidence(tr)
        ws.append(["ROI: intervallo di confidenza 95% da", _clean(conf["lo"]), "a", _clean(conf["hi"])])
        ws.append(["Probabilita' che il ROI vero sia positivo %", _clean(conf["p_positive"])])
        cut, ins, oos = bt.split_metrics(tr, 0.7, bankroll)
        ws.append(["Studio (primo 70%) ROI %", _clean(ins["roi_pct"]), "operazioni", ins["trades"]])
        ws.append(["Verifica (ultimo 30%) ROI %", _clean(oos["roi_pct"]), "operazioni", oos["trades"],
                   "dal", _clean(cut)])
        yearly = bt.breakdown(tr, "year")
        for name, status, detail in bt.robustness_checks(res.metrics, oos, yearly, conf):
            ws.append([name, {"ok": "ok", "warn": "attenzione", "bad": "critico"}.get(status, status), detail])
        _section(ws, "PER ANNO")
        _table(ws, yearly)
        _section(ws, "PER FASCIA DI QUOTA")
        _table(ws, bt.breakdown(tr, "odds_bucket"))
    if res.live:
        _section(ws, "INGRESSO IN PARTITA")
        for k, v in res.live.items():
            ws.append([k, _clean(v)])
    if "cashout" in trades.columns and len(trades):
        _section(ws, "USCITE (PER ESITO E MOMENTO)")
        _table(ws, co.summary(trades))
        if c["method"] != "scalping" and co.is_active(cfg):
            fair = co.model_benchmark(trades, {**cfg, "cashout": {**c, "margin_pct": 0.0, "tp_quote": None, "sl_quote": None}})
            ws.append(["Controllo del modello (scarto punti di ROI, errore standard)", _clean(fair["gap"]), _clean(fair["se"])])
    _section(ws, "CONFIGURAZIONE COMPLETA (JSON)")
    ws.append([json.dumps(cfg, ensure_ascii=False, default=str, sort_keys=True)[:32000]])

    # ------------------------------------------------------------------ Operazioni e Punto per punto
    pp = game_rows(trades, games) if cfg.get("sport") == "tennis" else pd.DataFrame()
    ops = trades.drop(columns=[c for c in DROP if c in trades.columns]).rename(columns=RENAME)
    ops.insert(0, "chiave", bt.trade_key(trades).to_numpy())
    if "date" in ops.columns:
        ops["date"] = pd.to_datetime(ops["date"]).dt.date
    if not pp.empty:
        ops["game_per_game"] = ops["chiave"].map(compact_games(pp))
    ws2 = wb.create_sheet("Operazioni")
    _table(ws2, ops)
    ws3 = wb.create_sheet("Punto per punto")
    _table(ws3, pp if not pp.empty else pd.DataFrame({"nota": ["Nessuna operazione con dati punto per punto (solo tennis, "
                                                                "match registrati dal Match Charting Project)."]}))
    ws4 = wb.create_sheet("Legenda")
    ws4.append(["colonna", "significato"])
    for k, v in LEGEND:
        ws4.append([k, v])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
