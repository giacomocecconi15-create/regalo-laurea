"""Configurazione centrale: percorsi, sorgenti dati, leghe e schema delle tabelle."""
from __future__ import annotations

from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
DB_PATH = DATA_DIR / "trading_db.duckdb"
STRATEGIES_PATH = ROOT / "strategies.json"

FIRST_YEAR = 2019  # primo anno / stagione scaricata (>= 5 anni di storico)

# --------------------------------------------------------------------------- calcio
FOOTBALL_URL = "https://www.football-data.co.uk/mmz4281/{season}/{div}.csv"

FOOTBALL_LEAGUES = {
    "E0": ("Inghilterra", "Premier League"),
    "E1": ("Inghilterra", "Championship"),
    "E2": ("Inghilterra", "League One"),
    "E3": ("Inghilterra", "League Two"),
    "SC0": ("Scozia", "Premiership"),
    "D1": ("Germania", "Bundesliga"),
    "D2": ("Germania", "2. Bundesliga"),
    "I1": ("Italia", "Serie A"),
    "I2": ("Italia", "Serie B"),
    "SP1": ("Spagna", "La Liga"),
    "SP2": ("Spagna", "La Liga 2"),
    "F1": ("Francia", "Ligue 1"),
    "F2": ("Francia", "Ligue 2"),
    "N1": ("Olanda", "Eredivisie"),
    "B1": ("Belgio", "Pro League"),
    "P1": ("Portogallo", "Primeira Liga"),
    "T1": ("Turchia", "Super Lig"),
    "G1": ("Grecia", "Super League"),
}

FOOT_BOOKS = ["ps", "b365", "max", "avg", "bfe"]  # Pinnacle, Bet365, max mercato, media mercato, Betfair Exchange
# prefissi usati da football-data.co.uk (con le varianti storiche)
_FOOT_SRC = {
    "ps": (["PS", "P"], ["PSC", "PC"], "P", "PC"),
    "b365": (["B365"], ["B365C"], "B365", "B365C"),
    "max": (["Max", "BbMx"], ["MaxC"], "Max", "MaxC"),
    "avg": (["Avg", "BbAv"], ["AvgC"], "Avg", "AvgC"),
    "bfe": (["BFE"], ["BFEC"], "BFE", "BFEC"),
}

FOOT_STATS = {
    "home_shots": "HS", "away_shots": "AS", "home_sot": "HST", "away_sot": "AST",
    "home_fouls": "HF", "away_fouls": "AF", "home_corners": "HC", "away_corners": "AC",
    "home_yellow": "HY", "away_yellow": "AY", "home_red": "HR", "away_red": "AR",
    "home_xg": "HxG", "away_xg": "AxG",
}


def football_odds_map() -> dict[str, list[str]]:
    """colonna destinazione -> possibili intestazioni sorgente (in ordine di preferenza)."""
    out: dict[str, list[str]] = {}
    for book, (open_p, close_p, ou_open, ou_close) in _FOOT_SRC.items():
        for o in "hda":
            out[f"{book}_{o}"] = [p + o.upper() for p in open_p]
            out[f"{book}_c{o}"] = [p + o.upper() for p in close_p]
        alt = {"max": ["BbMx"], "avg": ["BbAv"]}.get(book, [])
        out[f"{book}_o25"] = [ou_open + ">2.5"] + [a + ">2.5" for a in alt]
        out[f"{book}_u25"] = [ou_open + "<2.5"] + [a + "<2.5" for a in alt]
        out[f"{book}_co25"] = [ou_close + ">2.5"]
        out[f"{book}_cu25"] = [ou_close + "<2.5"]
    return out


FOOT_ODDS_MAP = football_odds_map()
FOOT_INT_COLS = ["fthg", "ftag", "hthg", "htag"]
FOOT_TEXT_COLS = ["match_id", "div", "country", "league", "season", "kickoff", "home_team", "away_team", "ftr", "htr"]
FOOT_FEATURES = ["home_ppg5", "away_ppg5", "home_gf5", "home_ga5", "away_gf5", "away_ga5"]
FOOT_FLOAT_COLS = (
    FOOT_INT_COLS + list(FOOT_STATS) + list(FOOT_ODDS_MAP) + ["ah_line", "ah_cline", "total_goals"] + FOOT_FEATURES
)
FOOT_COLUMNS = (
    ["match_id", "div", "country", "league", "season", "season_start", "match_date", "kickoff",
     "home_team", "away_team", "fthg", "ftag", "ftr", "hthg", "htag", "htr", "total_goals"]
    + list(FOOT_STATS) + list(FOOT_ODDS_MAP) + ["ah_line", "ah_cline"] + FOOT_FEATURES
)

# --------------------------------------------------------------------------- tennis
# Quote ATP (Bet365, Pinnacle, max e media mercato): tennis-data.co.uk, con mirror pubblici di riserva
TENNIS_ODDS_OFFICIAL = [
    "http://www.tennis-data.co.uk/{year}/{year}.xlsx",
    "https://www.tennis-data.co.uk/{year}/{year}.xlsx",
]
TENNIS_ODDS_MIRRORS = [
    "https://raw.githubusercontent.com/KacWNK/Bachelor_Thesis-predicting_results_of_the_tennis_matches_using_machine_learning_models/main/raw_data/bets/{year}.xlsx",
    "https://raw.githubusercontent.com/cristiarazvan/tennisML/main/data/bets{year}.csv",
]
# Statistiche di servizio / ranking in formato Sackmann.
# Il repository originale JeffSackmann/tennis_atp resta primo tentativo per i Challenger;
# per il circuito maggiore si usa TML-Database (stesso formato, aggiornato) con Sackmann di riserva.
TENNIS_TOUR_SOURCES = [
    "https://raw.githubusercontent.com/Tennismylife/TML-Database/master/{year}.csv",
    "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_matches_{year}.csv",
]
TENNIS_CHALL_SOURCES = [
    "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_matches_qual_chall_{year}.csv",
    "https://media.githubusercontent.com/media/ivanposinovec/ATP/main/tennis_atp-master/atp_matches_qual_chall_{year}.csv",
]

TENNIS_BOOKS = ["ps", "b365", "max", "avg"]
TENNIS_STATS = ["ace", "df", "svpt", "1stIn", "1stWon", "2ndWon", "SvGms", "bpSaved", "bpFaced"]
TENNIS_TEXT_COLS = [
    "match_id", "circuit", "tourney_id", "tourney_name", "tourney_level", "series", "surface", "round",
    "winner_name", "loser_name", "winner_id", "loser_id", "winner_hand", "loser_hand", "winner_ioc",
    "loser_ioc", "score", "status",
]
TENNIS_FEATURES = ["winner_form10", "loser_form10"]
TENNIS_FLOAT_COLS = (
    ["draw_size", "best_of", "minutes", "winner_rank", "loser_rank", "winner_rank_points", "loser_rank_points",
     "winner_age", "loser_age", "winner_ht", "loser_ht"]
    + [f"{s}_{c}" for s in "wl" for c in TENNIS_STATS]
    + [f"{b}_{s}" for b in TENNIS_BOOKS for s in "wl"]
    + TENNIS_FEATURES
)
TENNIS_COLUMNS = (
    ["match_id", "circuit", "season", "tourney_id", "tourney_name", "tourney_level", "series", "surface",
     "indoor", "draw_size", "tourney_date", "match_date", "round", "round_order", "best_of",
     "winner_name", "loser_name", "winner_id", "loser_id", "winner_hand", "loser_hand", "winner_ioc",
     "loser_ioc", "winner_age", "loser_age", "winner_ht", "loser_ht", "winner_rank", "loser_rank",
     "winner_rank_points", "loser_rank_points", "score", "status", "minutes"]
    + [f"{s}_{c}" for s in "wl" for c in TENNIS_STATS]
    + [f"{b}_{s}" for b in TENNIS_BOOKS for s in "wl"]
    + ["has_odds"] + TENNIS_FEATURES
)

ROUND_ORDER = {
    "Q1": 0, "Q2": 1, "Q3": 2, "Q4": 2.5, "RR": 3, "ER": 3, "R128": 4, "R64": 5, "R32": 6, "R16": 7,
    "QF": 8, "SF": 9, "BR": 9.5, "F": 10,
    "Round Robin": 3, "1st Round": 4, "2nd Round": 5, "3rd Round": 6, "4th Round": 7,
    "Quarterfinals": 8, "Semifinals": 9, "The Final": 10,
}


def football_seasons(today: date | None = None) -> list[str]:
    """Codici stagione football-data ('1920', ..., stagione in corso)."""
    today = today or date.today()
    last_start = today.year if today.month >= 7 else today.year - 1
    return [f"{y % 100:02d}{(y + 1) % 100:02d}" for y in range(FIRST_YEAR, last_start + 1)]


def tennis_years(today: date | None = None) -> list[int]:
    today = today or date.today()
    return list(range(FIRST_YEAR, today.year + 1))
