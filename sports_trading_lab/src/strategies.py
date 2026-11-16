"""Strategy Vault (strategies.json) e Pre-Match Scanner.

CSV delle quote del giorno - colonne riconosciute (le facoltative tra parentesi):
  tennis  : date, tournament, surface, player1, player2, odds1, odds2,
            (circuit, level, series, round, best_of, rank1, rank2, form1, form2 in % 0-100)
  calcio  : date, league, home_team, away_team, odds_home, odds_draw, odds_away,
            (odds_over25, odds_under25, home_ppg5, away_ppg5, home_gf5, home_ga5, away_gf5, away_ga5)
Ranking/form mancanti vengono recuperati dal database quando il nome coincide con quello storico.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import backtester as bt  # noqa: E402
from src import config as C  # noqa: E402

TENNIS_REQUIRED = ["player1", "player2", "odds1", "odds2"]
FOOTBALL_REQUIRED = ["home_team", "away_team", "odds_home", "odds_draw", "odds_away"]

TENNIS_TEMPLATE = pd.DataFrame({
    "date": ["2026-11-17", "2026-11-17", "2026-11-17"],
    "tournament": ["Roma", "Roma", "Roma"], "circuit": ["ATP"] * 3, "level": ["M"] * 3,
    "surface": ["Clay", "Clay", "Clay"], "round": ["R32", "R32", "R16"], "best_of": [3, 3, 3],
    "player1": ["Jannik Sinner", "Lorenzo Musetti", "Casper Ruud"],
    "player2": ["Alex De Minaur", "Taylor Fritz", "Matteo Berrettini"],
    "odds1": [1.22, 1.75, 1.62], "odds2": [4.60, 2.15, 2.40],
    "rank1": [1, 9, 7], "rank2": [8, 4, 30],
})
FOOTBALL_TEMPLATE = pd.DataFrame({
    "date": ["2026-11-21", "2026-11-21", "2026-11-22"],
    "league": ["Serie A", "Serie A", "Premier League"],
    "home_team": ["Inter", "Lecce", "Arsenal"], "away_team": ["Genoa", "Milan", "Everton"],
    "odds_home": [1.38, 4.20, 1.45], "odds_draw": [4.90, 3.55, 4.50], "odds_away": [8.50, 1.92, 7.20],
    "odds_over25": [1.62, 1.95, 1.70], "odds_under25": [2.30, 1.88, 2.15],
})


# --------------------------------------------------------------------------- vault
def load_vault(path: Path | None = None) -> dict:
    path = Path(path or C.STRATEGIES_PATH)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, np.generic):
        o = o.item()
    if isinstance(o, float) and not np.isfinite(o):
        return None
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.strftime("%Y-%m-%d")
    return o


def save_strategy(name: str, cfg: dict, metrics: dict, path: Path | None = None) -> dict:
    path = Path(path or C.STRATEGIES_PATH)
    vault = load_vault(path)
    vault[name] = {"config": _clean(cfg), "metrics": _clean(metrics),
                   "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M")}
    path.write_text(json.dumps(vault, indent=2, ensure_ascii=False), encoding="utf-8")
    return vault


def delete_strategy(name: str, path: Path | None = None) -> dict:
    path = Path(path or C.STRATEGIES_PATH)
    vault = load_vault(path)
    vault.pop(name, None)
    path.write_text(json.dumps(vault, indent=2, ensure_ascii=False), encoding="utf-8")
    return vault


# --------------------------------------------------------------------------- arricchimento
def latest_tennis_profiles(con) -> pd.DataFrame:
    """Ultimo ranking noto e form (% vittorie ultime 10) per giocatore."""
    return con.execute("""
        WITH long AS (
            SELECT winner_name AS player, winner_rank AS rnk, 1.0 AS won, match_date, round_order FROM tennis_matches
            UNION ALL
            SELECT loser_name, loser_rank, 0.0, match_date, round_order FROM tennis_matches
        ), ranked AS (
            SELECT *, row_number() OVER (PARTITION BY player ORDER BY match_date DESC, round_order DESC) AS n FROM long
        )
        SELECT player, max(CASE WHEN n = 1 THEN rnk END) AS rank,
               CASE WHEN count(*) >= 5 THEN avg(won) * 100 END AS form10
        FROM ranked WHERE n <= 10 GROUP BY player
    """).df().set_index("player")


def latest_football_form(con) -> pd.DataFrame:
    """Punti e gol medi nelle ultime 5 partite per squadra."""
    return con.execute("""
        WITH long AS (
            SELECT home_team AS team, match_date, fthg AS gf, ftag AS ga,
                   CASE ftr WHEN 'H' THEN 3.0 WHEN 'D' THEN 1.0 ELSE 0.0 END AS pts FROM football_matches
            UNION ALL
            SELECT away_team, match_date, ftag, fthg,
                   CASE ftr WHEN 'A' THEN 3.0 WHEN 'D' THEN 1.0 ELSE 0.0 END FROM football_matches
        ), ranked AS (SELECT *, row_number() OVER (PARTITION BY team ORDER BY match_date DESC) AS n FROM long)
        SELECT team, avg(pts) AS ppg5, avg(gf) AS gf5, avg(ga) AS ga5
        FROM ranked WHERE n <= 5 GROUP BY team HAVING count(*) = 5
    """).df().set_index("team")


# --------------------------------------------------------------------------- selezioni dal CSV
def _norm_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    return df


def detect_sport(df: pd.DataFrame) -> str | None:
    cols = set(_norm_columns(df).columns)
    if set(TENNIS_REQUIRED) <= cols:
        return "tennis"
    if set(FOOTBALL_REQUIRED) <= cols:
        return "football"
    return None


def _col(df: pd.DataFrame, name: str, numeric: bool = False) -> pd.Series:
    if name in df.columns:
        return pd.to_numeric(df[name], errors="coerce") if numeric else df[name]
    return pd.Series(np.nan, index=df.index, dtype="float64" if numeric else object)


def tennis_upload_selections(df: pd.DataFrame, con=None) -> pd.DataFrame:
    df = _norm_columns(df).reset_index(drop=True)
    prof = latest_tennis_profiles(con) if con is not None else pd.DataFrame(columns=["rank", "form10"])
    parts = []
    for a, b in (("1", "2"), ("2", "1")):
        player, opp = df[f"player{a}"].astype(str).str.strip(), df[f"player{b}"].astype(str).str.strip()
        rank = _col(df, f"rank{a}", True).fillna(player.map(prof["rank"]))
        opp_rank = _col(df, f"rank{b}", True).fillna(opp.map(prof["rank"]))
        odds, opp_odds = _col(df, f"odds{a}", True), _col(df, f"odds{b}", True)
        dates = pd.to_datetime(_col(df, "date"), errors="coerce")
        parts.append(pd.DataFrame({
            "match_id": "row" + df.index.astype(str), "date": dates, **bt._calendar(dates),
            "circuit": _col(df, "circuit"), "competition": _col(df, "tournament"), "level": _col(df, "level"),
            "series": _col(df, "series"), "surface": _col(df, "surface").astype(str).str.title().replace({"Nan": np.nan}),
            "round": _col(df, "round"), "best_of": _col(df, "best_of", True),
            "player": player, "opponent": opp, "rank": rank, "opp_rank": opp_rank, "rank_adv": opp_rank - rank,
            "form10": _col(df, f"form{a}", True).fillna(player.map(prof["form10"])),
            "opp_form10": _col(df, f"form{b}", True).fillna(opp.map(prof["form10"])),
            "age": np.nan, "odds": odds, "opp_odds": opp_odds,
            "selection": np.select([odds < opp_odds, odds > opp_odds], ["favourite", "underdog"], "pick"),
            "event": player + " vs " + opp, "pick": player,
        }))
    return pd.concat(parts, ignore_index=True)


def football_upload_selections(df: pd.DataFrame, market: str, con=None) -> pd.DataFrame:
    df = _norm_columns(df).reset_index(drop=True)
    form = latest_football_form(con) if con is not None else pd.DataFrame(columns=["ppg5", "gf5", "ga5"])
    home, away = df["home_team"].astype(str).str.strip(), df["away_team"].astype(str).str.strip()
    league = _col(df, "league")
    by_div = {k: v[1] for k, v in C.FOOTBALL_LEAGUES.items()}
    if "div" in df.columns:
        league = league.fillna(df["div"].map(by_div))
    league = league.replace(by_div)
    country = league.map({v[1]: v[0] for v in C.FOOTBALL_LEAGUES.values()})
    feat = {}
    for side, team in (("home", home), ("away", away)):
        for k in ("ppg5", "gf5", "ga5"):
            feat[f"{side}_{k}"] = _col(df, f"{side}_{k}", True).fillna(team.map(form[k]))
    oh, od, oa = (_col(df, c, True) for c in ("odds_home", "odds_draw", "odds_away"))
    dates = pd.to_datetime(_col(df, "date"), errors="coerce")
    common = {
        "match_id": "row" + df.index.astype(str), "date": dates, **bt._calendar(dates),
        "country": country, "league": league, "competition": league, "season": np.nan,
        "home_team": home, "away_team": away, "event": home + " - " + away,
        "odds_home": oh, "odds_draw": od, "odds_away": oa, "odds_over": _col(df, "odds_over25", True), **feat,
        "goals5": (feat["home_gf5"] + feat["home_ga5"] + feat["away_gf5"] + feat["away_ga5"]) / 2,
        "ppg_diff": feat["home_ppg5"] - feat["away_ppg5"],
    }
    if market == "1x2":
        outcomes = [("home", oh, home), ("draw", od, "Pareggio"), ("away", oa, away)]
    else:
        outcomes = [("over", _col(df, "odds_over25", True), "Over 2.5"), ("under", _col(df, "odds_under25", True), "Under 2.5")]
    parts = []
    for name, odds, label in outcomes:
        d = pd.DataFrame({**common, "selection": name, "odds": odds, "pick": label})
        if market == "1x2" and name != "draw":
            other = oa if name == "home" else oh
            d["role"] = np.select([odds < other, odds > other], ["favourite", "underdog"], "pick")
        else:
            d["role"] = name
        parts.append(d)
    return pd.concat(parts, ignore_index=True)


# --------------------------------------------------------------------------- stake
def stake_for(cfg: dict, metrics: dict, odds: np.ndarray, bankroll: float, method: str = "flat",
              flat_pct: float = 1.0, kelly_fraction: float = 0.25, cap_pct: float = 5.0) -> pd.DataFrame:
    """Importo da rischiare per segnale. Per il lay il rischio e' la responsabilita';
    `backer_stake` e' la puntata da inserire sull'exchange (rischio / (quota - 1))."""
    odds = np.asarray(odds, dtype=float)
    comm = float(cfg.get("commission") or 0.0)
    lay = cfg.get("side") == "lay"
    b = (1 - comm) / (odds - 1) if lay else (odds - 1) * (1 - comm)
    if method == "kelly":
        edge = float(metrics.get("roi_pct") or 0.0) / 100
        frac = np.clip(kelly_fraction * edge / b, 0, cap_pct / 100) if edge > 0 else np.zeros_like(b)
    else:
        frac = np.full_like(b, flat_pct / 100)
    risk = bankroll * frac
    return pd.DataFrame({"stake_pct": frac * 100, "risk": risk,
                         "backer_stake": risk / (odds - 1) if lay else risk})


# --------------------------------------------------------------------------- scanner
def scan(upload: pd.DataFrame, vault: dict, bankroll: float, con=None, method: str = "flat",
         flat_pct: float = 1.0, kelly_fraction: float = 0.25, cap_pct: float = 5.0) -> tuple[pd.DataFrame, list[str]]:
    """Ritorna (segnali, avvisi). Un segnale = match del CSV che soddisfa una strategia salvata."""
    sport = detect_sport(upload)
    cols = ["strategia", "data", "evento", "competizione", "selezione", "lato", "quota",
            "stake_%", "importo_rischio", "puntata_exchange", "roi_storico_%"]
    if sport is None:
        return pd.DataFrame(columns=cols), [
            "Formato non riconosciuto. Tennis: " + ", ".join(TENNIS_REQUIRED) + " - Calcio: " + ", ".join(FOOTBALL_REQUIRED)]
    notes, rows, cache = [], [], {}
    for name, entry in vault.items():
        cfg, metrics = entry.get("config", {}), entry.get("metrics", {})
        if cfg.get("sport") != sport:
            continue
        market = cfg.get("market") or bt.MARKETS[sport][0]
        if market not in cache:
            cache[market] = (tennis_upload_selections(upload, con) if sport == "tennis"
                             else football_upload_selections(upload, market, con))
        sel = cache[market]
        if sel["odds"].isna().all():
            notes.append(f"'{name}': nel CSV mancano le quote per il mercato {market}.")
            continue
        live = {k: v for k, v in cfg.items() if k not in ("date_from", "date_to", "exclude_retirements")}
        live["filters"] = {}
        for col, rule in (cfg.get("filters") or {}).items():
            if col in ("season", "year"):
                continue
            if col not in sel.columns or sel[col].isna().all():
                notes.append(f"'{name}': filtro '{col}' non verificabile (dato assente nel CSV e nel database) - ignorato.")
                continue
            live["filters"][col] = rule
        hit = bt.apply_filters(sel, live)
        if hit.empty:
            continue
        st = stake_for(cfg, metrics, hit["odds"].to_numpy(), bankroll, method, flat_pct, kelly_fraction, cap_pct)
        if method == "kelly" and not float(metrics.get("roi_pct") or 0) > 0:
            notes.append(f"'{name}': ROI storico non positivo, Kelly assegna stake 0.")
        lay = cfg.get("side") == "lay"
        rows.append(pd.DataFrame({
            "strategia": name, "data": hit["date"].dt.strftime("%Y-%m-%d").to_numpy(), "evento": hit["event"].to_numpy(),
            "competizione": hit["competition"].to_numpy(), "selezione": hit["pick"].to_numpy(),
            "lato": "BANCA" if lay else "PUNTA", "quota": hit["odds"].to_numpy(),
            "stake_%": st["stake_pct"].round(2).to_numpy(), "importo_rischio": st["risk"].round(2).to_numpy(),
            "puntata_exchange": st["backer_stake"].round(2).to_numpy(),
            "roi_storico_%": round(float(metrics.get("roi_pct") or 0), 2),
        }))
    if not any(e.get("config", {}).get("sport") == sport for e in vault.values()):
        notes.append(f"Nessuna strategia salvata per lo sport rilevato ({sport}).")
    out = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=cols)
    return out, notes
