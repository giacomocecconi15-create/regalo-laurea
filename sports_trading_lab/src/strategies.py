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
from src import update_data as ud  # noqa: E402

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


def _season_start(dates: pd.Series) -> pd.Series:
    return (dates.dt.year - (dates.dt.month < 7).astype(int)).astype("Int64")


def upcoming_football_features(con, home: pd.Series, away: pd.Series, div: pd.Series, dates: pd.Series) -> pd.DataFrame:
    """Variabili pre-partita delle partite in programma, calcolate con lo stesso codice del backtest:
    le partite future vengono accodate allo storico come righe senza risultato."""
    start = _season_start(dates)
    first = int(start.min()) - 1 if start.notna().any() else 0
    hist = con.execute("""SELECT match_id, div, season_start, home_team, away_team, match_date, ftr, fthg, ftag,
                                 home_sot, away_sot, home_corners, away_corners
                          FROM football_matches WHERE season_start >= ?""", [first]).df()
    future = pd.DataFrame({
        "match_id": ["__up" + str(i) for i in range(len(home))], "div": div.to_numpy(), "season_start": start.to_numpy(),
        "home_team": home.to_numpy(), "away_team": away.to_numpy(), "match_date": dates.to_numpy(),
        "ftr": None, "fthg": np.nan, "ftag": np.nan, "home_sot": np.nan, "away_sot": np.nan,
        "home_corners": np.nan, "away_corners": np.nan,
    })
    future = future[future["match_date"].notna() & future["div"].notna()]
    if future.empty:
        return pd.DataFrame(index=range(len(home)))
    # una partita del file gia' presente nello storico (appena giocata) non va accodata: si usa la sua riga
    key = lambda d: d["home_team"].astype(str) + "|" + d["away_team"].astype(str) + "|" + pd.to_datetime(d["match_date"]).dt.strftime("%Y%m%d")
    known = dict(zip(key(hist), hist["match_id"]))
    future["_known"] = key(future).map(known)
    todo = future[future["_known"].isna()].drop(columns="_known")
    feats = ud.football_features(pd.concat([hist, todo], ignore_index=True) if len(todo) else hist).set_index("match_id")
    ids = future.set_index("match_id")["_known"].fillna(pd.Series(future["match_id"].to_numpy(), index=future["match_id"].to_numpy()))
    order = pd.Series(["__up" + str(i) for i in range(len(home))]).map(ids)
    return feats.reindex(order.to_numpy()).reset_index(drop=True)


def upcoming_tennis_features(con, p1: pd.Series, p2: pd.Series, surface: pd.Series, dates: pd.Series) -> pd.DataFrame:
    """Come sopra per il tennis: player1 nel ruolo 'winner', player2 nel ruolo 'loser' (solo come etichette)."""
    hist = con.execute("""SELECT match_id, match_date, round_order, surface, winner_name, loser_name,
                                 w_svpt, w_1stWon, w_2ndWon, w_ace, w_df, l_svpt, l_1stWon, l_2ndWon, l_ace, l_df
                          FROM tennis_matches""").df()
    hist["is_future"] = False
    ids = ["__up" + str(i) for i in range(len(p1))]
    future = pd.DataFrame({"match_id": ids, "match_date": dates.to_numpy(), "round_order": 99.0,
                           "surface": surface.to_numpy(), "winner_name": p1.to_numpy(), "loser_name": p2.to_numpy(),
                           "is_future": True})
    future = future[future["match_date"].notna()]
    if future.empty:
        return pd.DataFrame(index=range(len(p1)))
    # un match del file gia' presente nello storico (appena giocato) non va accodato: si usa la sua riga,
    # scambiando i ruoli se nel database il vincitore e' player2
    h = hist.assign(_d=pd.to_datetime(hist["match_date"]))
    f = future.assign(_d=pd.to_datetime(future["match_date"]))
    straight = f.merge(h, on=["winner_name", "loser_name"], suffixes=("", "_h"))
    swapped = f.merge(h, left_on=["winner_name", "loser_name"], right_on=["loser_name", "winner_name"], suffixes=("", "_h"))
    near = lambda m: m[(m["_d"] - m["_d_h"]).dt.days.abs() <= 1].drop_duplicates("match_id")
    straight, swapped = near(straight), near(swapped)
    done = set(straight["match_id"]) | set(swapped["match_id"])
    new = future[~future["match_id"].isin(done)]
    feats = ud.tennis_features(pd.concat([hist, new], ignore_index=True) if len(new) else hist).set_index("match_id")
    out = feats.reindex(ids)
    if len(straight):
        out.loc[straight["match_id"].to_numpy()] = feats.reindex(straight["match_id_h"].to_numpy()).to_numpy()
    if len(swapped):
        flip = [c.replace("winner_", "#").replace("loser_", "winner_").replace("#", "loser_") for c in feats.columns]
        out.loc[swapped["match_id"].to_numpy()] = feats.reindex(swapped["match_id_h"].to_numpy())[flip].to_numpy()
    return out.reset_index(drop=True)


def tennis_player_profiles(con) -> pd.DataFrame:
    """Ultimi dati anagrafici e di ranking noti per giocatore."""
    return con.execute("""
        WITH long AS (
            SELECT winner_name AS player, winner_rank AS rnk, winner_rank_points AS pts, winner_hand AS hand,
                   winner_ht AS ht, winner_ioc AS ioc, winner_age AS age, match_date, round_order FROM tennis_matches
            UNION ALL
            SELECT loser_name, loser_rank, loser_rank_points, loser_hand, loser_ht, loser_ioc, loser_age, match_date, round_order
            FROM tennis_matches
        )
        SELECT player, arg_max(rnk, match_date) FILTER (WHERE rnk IS NOT NULL) AS rank,
               arg_max(pts, match_date) FILTER (WHERE pts IS NOT NULL) AS rank_points,
               max(hand) AS hand, max(ht) AS height, max(ioc) AS country,
               max(age + date_diff('day', match_date, current_date) / 365.25) FILTER (WHERE age IS NOT NULL) AS age
        FROM long GROUP BY player""").df().set_index("player")


def tournament_profiles(con) -> pd.DataFrame:
    """Superficie, livello e formato dell'ultima edizione di ogni torneo ATP (chiave: nome in minuscolo)."""
    df = con.execute("""
        SELECT lower(tourney_name) AS key, arg_max(surface, match_date) AS surface, arg_max(tourney_level, match_date) AS level,
               arg_max(series, match_date) FILTER (WHERE series IS NOT NULL) AS series,
               arg_max(indoor, match_date) FILTER (WHERE indoor IS NOT NULL) AS indoor, max(best_of) AS best_of
        FROM tennis_matches WHERE circuit = 'ATP' GROUP BY 1""").df()
    return df.set_index("key")


TOURNAMENT_ALIASES = {"french open": "roland garros", "us open": "us open", "aus open": "australian open"}


def tennis_upload_selections(df: pd.DataFrame, con=None) -> pd.DataFrame:
    df = _norm_columns(df).reset_index(drop=True)
    dates = pd.to_datetime(_col(df, "date"), errors="coerce")
    known = dates.fillna(pd.Timestamp.today().normalize())
    name = {s: df[f"player{s}"].astype(str).str.strip() for s in "12"}
    if con is not None:  # allinea i nomi a quelli del database (maiuscole, accenti e trattini non contano)
        known_names = con.execute("SELECT DISTINCT winner_name FROM tennis_matches UNION SELECT DISTINCT loser_name FROM tennis_matches").df().iloc[:, 0].dropna()
        canon = {ud.norm_key(n): n for n in known_names}
        name = {s: v.map(lambda n: canon.get(ud.norm_key(n), n)) for s, v in name.items()}
    tour = _col(df, "tournament")
    surface = _col(df, "surface").astype(str).str.title()
    surface = surface.where(~surface.isin(["Nan", "None", ""]))
    level, series, best_of, court = _col(df, "level"), _col(df, "series"), _col(df, "best_of", True), _col(df, "court")
    prof = pd.DataFrame(columns=["rank", "rank_points", "hand", "height", "country", "age"])
    feats = pd.DataFrame(index=df.index)
    if con is not None:
        prof = tennis_player_profiles(con)
        tp = tournament_profiles(con)
        key = tour.astype(str).str.strip().str.lower().replace(TOURNAMENT_ALIASES)
        names_db = list(tp.index)

        def closest(k: str) -> str:  # 'paris' -> 'paris masters', 'rome masters 1000' -> 'rome masters'
            if k in tp.index or not k or k == "nan":
                return k
            hits = [n for n in names_db if k in n or n in k]
            return max(hits, key=len) if hits else k

        key = key.map(closest)
        fill = lambda a, b: a.astype(object).where(a.notna(), b.astype(object))  # il dato del file ha la precedenza
        surface = fill(surface, key.map(tp["surface"]))
        level, series = fill(level, key.map(tp["level"])), fill(series, key.map(tp["series"]))
        best_of = best_of.fillna(key.map(tp["best_of"]))
        court = fill(court, key.map(tp["indoor"]).map({True: "Indoor", False: "Outdoor"}))
        feats = upcoming_tennis_features(con, name["1"], name["2"], surface, known)
    role = {"1": "winner", "2": "loser"}
    feat = lambda s, k: (feats[f"{role[s]}_{k}"] if f"{role[s]}_{k}" in feats.columns else pd.Series(np.nan, index=df.index))
    parts = []
    for a, b in (("1", "2"), ("2", "1")):
        player, opp = name[a], name[b]
        rank = _col(df, f"rank{a}", True).fillna(player.map(prof["rank"]))
        opp_rank = _col(df, f"rank{b}", True).fillna(opp.map(prof["rank"]))
        odds, opp_odds = _col(df, f"odds{a}", True), _col(df, f"odds{b}", True)
        form = _col(df, f"form{a}", True).fillna(feat(a, "form10") * 100)
        opp_form = _col(df, f"form{b}", True).fillna(feat(b, "form10") * 100)
        h2h_w, h2h_l = feat(a, "h2h_wins"), feat(b, "h2h_wins")
        parts.append(pd.DataFrame({
            "match_id": "row" + df.index.astype(str), "date": dates, **bt._calendar(known),
            "circuit": _col(df, "circuit").fillna("ATP"), "competition": tour, "level": level, "series": series,
            "surface": surface, "court": court, "round": _col(df, "round"), "best_of": best_of,
            "player": player, "opponent": opp, "rank": rank, "opp_rank": opp_rank, "rank_adv": opp_rank - rank,
            "rank_points": player.map(prof["rank_points"]), "age": player.map(prof["age"]), "height": player.map(prof["height"]),
            "hand": player.map(prof["hand"]), "opp_hand": opp.map(prof["hand"]), "country": player.map(prof["country"]),
            "form10": form, "opp_form10": opp_form, "form_adv": form - opp_form,
            "form_surf": feat(a, "form_surf") * 100, "opp_form_surf": feat(b, "form_surf") * 100,
            "rest_days": feat(a, "rest_days"), "opp_rest_days": feat(b, "rest_days"), "matches14": feat(a, "matches14"),
            "h2h_wins": h2h_w, "h2h_losses": h2h_l, "h2h_diff": h2h_w - h2h_l,
            "srv_won10": feat(a, "srv_won10") * 100, "ret_won10": feat(a, "ret_won10") * 100,
            "ace10": feat(a, "ace10") * 100, "df10": feat(a, "df10") * 100,
            "opp_srv_won10": feat(b, "srv_won10") * 100, "opp_ret_won10": feat(b, "ret_won10") * 100,
            "elo": feat(a, "elo"), "opp_elo": feat(b, "elo"),
            "model_prob": bt.elo_win_prob(feat(a, "elo"), feat(b, "elo")) * 100,
            "model_edge": bt.elo_win_prob(feat(a, "elo"), feat(b, "elo")) * 100 - (1 / odds) / (1 / odds + 1 / opp_odds) * 100,
            "odds": odds, "opp_odds": opp_odds,
            "selection": np.select([odds < opp_odds, odds > opp_odds], ["favourite", "underdog"], "pick"),
            "event": name["1"] + " vs " + name["2"], "pick": player,
        }))
    return pd.concat(parts, ignore_index=True)


def football_upload_selections(df: pd.DataFrame, market: str, con=None) -> pd.DataFrame:
    df = _norm_columns(df).reset_index(drop=True)
    home, away = df["home_team"].astype(str).str.strip(), df["away_team"].astype(str).str.strip()
    by_div = {k: v[1] for k, v in C.FOOTBALL_LEAGUES.items()}
    by_league = {v[1]: k for k, v in C.FOOTBALL_LEAGUES.items()}
    league = _col(df, "league")
    div = _col(df, "div")
    league = league.fillna(div.map(by_div)).replace(by_div)
    div = div.fillna(league.map(by_league))
    country = league.map({v[1]: v[0] for v in C.FOOTBALL_LEAGUES.values()})
    dates = pd.to_datetime(_col(df, "date"), errors="coerce")
    known = dates.fillna(pd.Timestamp.today().normalize())
    auto = upcoming_football_features(con, home, away, div, known) if con is not None else pd.DataFrame(index=df.index)
    feat = {}
    for side in ("home", "away"):
        for k in C.FOOT_FEATURE_KEYS:
            col = f"{side}_{k}"
            feat[col] = _col(df, col, True).fillna(auto[col] if col in auto.columns else np.nan)
    oh, od, oa = (_col(df, c, True) for c in ("odds_home", "odds_draw", "odds_away"))
    common = {
        "match_id": "row" + df.index.astype(str), "date": dates, **bt._calendar(known),
        "country": country, "league": league, "competition": league, "season": np.nan,
        "home_team": home, "away_team": away, "event": home + " - " + away,
        "odds_home": oh, "odds_draw": od, "odds_away": oa, "odds_over": _col(df, "odds_over25", True),
        "odds_under": _col(df, "odds_under25", True),
        "ah_line": _col(df, "ah_line", True), **feat,
        "ppg_diff": feat["home_ppg5"] - feat["away_ppg5"], "pos_diff": feat["away_pos"] - feat["home_pos"],
        "goals5": (feat["home_gf5"] + feat["home_ga5"] + feat["away_gf5"] + feat["away_ga5"]) / 2,
    }
    if market == "1x2":
        outcomes = [("home", oh, home), ("draw", od, "Pareggio"), ("away", oa, away)]
    else:
        outcomes = [("over", _col(df, "odds_over25", True), "Over 2.5"), ("under", _col(df, "odds_under25", True), "Under 2.5")]
    parts = []
    model = bt.football_model_probs(feat["home_elo"], feat["away_elo"]) * 100
    imp = 1 / np.stack([oh.to_numpy(dtype=float), od.to_numpy(dtype=float), oa.to_numpy(dtype=float)], axis=1)
    imp = imp / imp.sum(axis=1, keepdims=True) * 100
    for k, (name, odds, label) in enumerate(outcomes):
        d = pd.DataFrame({**common, "selection": name, "odds": odds, "pick": label})
        d["model_prob"] = model[:, k] if market == "1x2" else np.nan
        d["model_edge"] = model[:, k] - imp[:, k] if market == "1x2" else np.nan
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
            "stake_%", "importo_rischio", "puntata_exchange", "roi_storico_%", "condizione_in_partita", "quota_live_stimata"]
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
        conds = []
        for col, rule in (cfg.get("filters") or {}).items():
            if col in ("season", "year"):
                continue
            if col in bt.INPLAY.get(sport, set()):      # condizione nota solo a partita iniziata: si verifica dal vivo
                if rule and rule.get("in"):
                    conds.append((col, rule["in"]))
                continue
            if col not in sel.columns or sel[col].isna().all():
                notes.append(f"'{name}': filtro '{col}' non verificabile (dato assente nel CSV e nel database) - ignorato.")
                continue
            live["filters"][col] = rule
        hit = bt.apply_filters(sel, live)
        if hit.empty:
            continue
        if cfg.get("side") == "lay":      # si banca alla quota equa piu' lo spread dell'exchange, non alla quota back
            hit = hit.assign(odds=bt.lay_price(hit, float(cfg.get("lay_spread", bt.LAY_SPREAD))))
        st = stake_for(cfg, metrics, hit["odds"].to_numpy(), bankroll, method, flat_pct, kelly_fraction, cap_pct)
        if method == "kelly" and not float(metrics.get("roi_pct") or 0) > 0:
            notes.append(f"'{name}': ROI storico non positivo, Kelly assegna stake 0.")
        lay = cfg.get("side") == "lay"
        cond_text = ""
        live_q = np.full(len(hit), np.nan)
        if conds:
            point = bt.entry_point({"sport": sport, "filters": {c: {"in": v} for c, v in conds}})
            cond_text = f"Entra {bt.ENTRY_LABEL[point].split(' (')[0]} solo se: " + "; ".join(
                f"{bt.FILTER_LABELS[sport].get(c, c)} = {' o '.join(map(str, v))}" for c, v in conds)
            set_states = dict(conds).get("ip_set1")
            if point == "set1" and set_states and len(set_states) == 1 and not (set(dict(conds)) - {"ip_set1"}):
                from src import cashout
                p = cashout.tennis_checkpoint_prob(hit.assign(set1_won=1.0 if set_states[0] == "Vinto" else 0.0,
                                                              best_of=hit["best_of"].fillna(3)))
                live_q = bt.live_odds(p, "lay" if lay else "back", float(cfg.get("live_margin", bt.LIVE_MARGIN)))
            if cfg.get("live_odds_override"):
                live_q = np.full(len(hit), float(cfg["live_odds_override"]))
        rows.append(pd.DataFrame({
            "strategia": name, "data": hit["date"].dt.strftime("%Y-%m-%d").to_numpy(), "evento": hit["event"].to_numpy(),
            "competizione": hit["competition"].to_numpy(), "selezione": hit["pick"].to_numpy(),
            "lato": "BANCA" if lay else "PUNTA", "quota": hit["odds"].to_numpy(),
            "stake_%": st["stake_pct"].round(2).to_numpy(), "importo_rischio": st["risk"].round(2).to_numpy(),
            "puntata_exchange": st["backer_stake"].round(2).to_numpy(),
            "roi_storico_%": round(float(metrics.get("roi_pct") or 0), 2),
            "condizione_in_partita": cond_text, "quota_live_stimata": np.round(live_q, 2),
        }))
    if not any(e.get("config", {}).get("sport") == sport for e in vault.values()):
        notes.append(f"Nessuna strategia salvata per lo sport rilevato ({sport}).")
    out = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=cols)
    return out, notes
