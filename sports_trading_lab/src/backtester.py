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
      "filters": {"surface": {"in": ["Clay"]}, "rank": {"min": 1, "max": 50}}
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

# colonne filtrabili: nome -> (etichetta, tipo)
FILTER_COLUMNS = {
    "tennis": {
        "circuit": ("Circuito", "cat"), "level": ("Livello torneo", "cat"), "series": ("Serie", "cat"),
        "competition": ("Torneo", "cat"), "surface": ("Superficie", "cat"), "round": ("Turno", "cat"),
        "best_of": ("Al meglio di", "cat"),
        "rank": ("Ranking giocatore", "num"), "opp_rank": ("Ranking avversario", "num"),
        "rank_adv": ("Vantaggio ranking (avv - gioc)", "num"),
        "form10": ("Form giocatore (% vinte ultime 10)", "num"), "opp_form10": ("Form avversario", "num"),
        "age": ("Eta' giocatore", "num"), "opp_odds": ("Quota avversario", "num"),
    },
    "football": {
        "country": ("Paese", "cat"), "league": ("Lega", "cat"), "season": ("Stagione", "cat"),
        "odds_home": ("Quota casa", "num"), "odds_draw": ("Quota pareggio", "num"), "odds_away": ("Quota ospite", "num"),
        "home_ppg5": ("Punti/partita casa (ult. 5)", "num"), "away_ppg5": ("Punti/partita ospite (ult. 5)", "num"),
        "ppg_diff": ("Differenza form (casa - ospite)", "num"),
        "home_gf5": ("Gol fatti casa (media ult. 5)", "num"), "home_ga5": ("Gol subiti casa (media ult. 5)", "num"),
        "away_gf5": ("Gol fatti ospite (media ult. 5)", "num"), "away_ga5": ("Gol subiti ospite (media ult. 5)", "num"),
    },
}


def connect(db_path: str | Path | None = None, read_only: bool = True) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(db_path or C.DB_PATH), read_only=read_only)


# --------------------------------------------------------------------------- selezioni
def tennis_selections(matches: pd.DataFrame, price: str = "avg") -> pd.DataFrame:
    """Due righe per match (una per giocatore). I walkover sono esclusi (scommessa nulla)."""
    m = matches[matches["status"].fillna("Completed") != "Walkover"]
    common = {
        "match_id": m["match_id"], "date": pd.to_datetime(m["match_date"]), "season": m["season"],
        "circuit": m["circuit"], "competition": m["tourney_name"], "level": m["tourney_level"],
        "series": m["series"], "surface": m["surface"], "round": m["round"], "best_of": m["best_of"],
        "status": m["status"],
    }
    sides = []
    for a, b, won in (("winner", "loser", True), ("loser", "winner", False)):
        x, y = a[0], b[0]
        sides.append(pd.DataFrame({
            **common,
            "player": m[f"{a}_name"], "opponent": m[f"{b}_name"],
            "rank": m[f"{a}_rank"], "opp_rank": m[f"{b}_rank"],
            "form10": m[f"{a}_form10"], "opp_form10": m[f"{b}_form10"], "age": m[f"{a}_age"],
            "odds": m[f"{price}_{x}"], "opp_odds": m[f"{price}_{y}"],
            "close_odds": m[f"ps_{x}"], "_close_opp": m[f"ps_{y}"],
            "won": won,
        }))
    s = pd.concat(sides, ignore_index=True)
    s["rank_adv"] = s["opp_rank"] - s["rank"]
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
    common = {
        "match_id": m["match_id"], "date": pd.to_datetime(m["match_date"]), "season": m["season"],
        "country": m["country"], "league": m["league"], "competition": m["league"],
        "home_team": m["home_team"], "away_team": m["away_team"],
        "event": m["home_team"] + " - " + m["away_team"],
        "odds_home": oh, "odds_draw": od, "odds_away": oa,
        "home_ppg5": m["home_ppg5"], "away_ppg5": m["away_ppg5"], "ppg_diff": m["home_ppg5"] - m["away_ppg5"],
        "home_gf5": m["home_gf5"], "home_ga5": m["home_ga5"], "away_gf5": m["away_gf5"], "away_ga5": m["away_ga5"],
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
    return BacktestResult(cfg, trades, compute_metrics(trades, bankroll), monthly_returns(trades))


def run_backtest(cfg: dict, con: duckdb.DuckDBPyConnection | None = None, bankroll: float = 100.0) -> BacktestResult:
    sport = cfg["sport"]
    market = cfg.get("market") or MARKETS[sport][0]
    sel = load_selections(sport, market, cfg.get("price", "avg"), cfg.get("price_timing", "open"), con)
    return backtest_selections(sel, cfg, bankroll)


if __name__ == "__main__":
    demo = {"sport": "tennis", "market": "h2h", "selection": ["favourite"], "side": "back", "price": "max",
            "odds_min": 1.2, "odds_max": 1.8, "filters": {"surface": {"in": ["Clay"]}}}
    res = run_backtest(demo)
    for k, v in res.metrics.items():
        print(f"{k:>24}: {v:,.2f}" if isinstance(v, float) else f"{k:>24}: {v}")
    print(res.monthly.tail(6).to_string(index=False))
