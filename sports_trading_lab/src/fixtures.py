"""Partite in programma con quote, da fonti gratuite, per lo scanner.

- Calcio: `fixtures.csv` di football-data.co.uk. Gratuito e senza registrazione; contiene le partite
  dei prossimi giorni di tutti i campionati coperti dal sito, con quote 1X2, Over/Under 2.5 e
  handicap asiatico (Bet365, media e massimo mercato, Betfair Exchange).
- Tennis: non esiste una fonte gratuita senza registrazione con le quote. Si usa The Odds API, che
  ha un piano gratuito (500 crediti al mese) con chiave personale: ogni torneo richiesto costa 1 credito.

Le impostazioni dell'utente (chiave, aggiornamento automatico) stanno in `data/settings.json`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import config as C  # noqa: E402
from src import update_data as ud  # noqa: E402

FIXTURES_URLS = ["https://www.football-data.co.uk/fixtures.csv"]
ODDS_API = "https://api.the-odds-api.com/v4"
SETTINGS_PATH = C.DATA_DIR / "settings.json"
DEFAULT_SETTINGS = {"auto_update": True, "auto_update_hours": 12, "odds_api_key": ""}
BOOK_PREFIX = {"avg": "Avg", "max": "Max", "b365": "B365", "bfe": "BFE"}


# --------------------------------------------------------------------------- impostazioni
def load_settings(path: Path | None = None) -> dict:
    try:
        data = json.loads(Path(path or SETTINGS_PATH).read_text(encoding="utf-8"))
        return {**DEFAULT_SETTINGS, **data} if isinstance(data, dict) else dict(DEFAULT_SETTINGS)
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_SETTINGS)


def save_settings(values: dict, path: Path | None = None) -> dict:
    path = Path(path or SETTINGS_PATH)
    merged = {**load_settings(path), **values}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return merged


# --------------------------------------------------------------------------- calcio
def parse_football_fixtures(raw: bytes, book: str = "avg") -> pd.DataFrame:
    """Converte fixtures.csv nel formato dello scanner. Se il bookmaker scelto non quota un esito
    si usa la media mercato."""
    src = ud.read_csv_loose(raw)
    cols = ["date", "time", "div", "league", "home_team", "away_team", "odds_home", "odds_draw", "odds_away",
            "odds_over25", "odds_under25", "ah_line"]
    if src.empty or not {"Date", "HomeTeam", "AwayTeam"} <= set(src.columns):
        return pd.DataFrame(columns=cols)
    pre = BOOK_PREFIX.get(book, "Avg")

    def price(suffix: str) -> pd.Series:
        main = ud.clean_odds(ud.pick(src, [pre + suffix]))
        return main.fillna(ud.clean_odds(ud.pick(src, ["Avg" + suffix])))

    dates = src["Date"].astype(str).str.strip()
    parsed = pd.to_datetime(dates, format="%d/%m/%Y", errors="coerce").fillna(pd.to_datetime(dates, format="%d/%m/%y", errors="coerce"))
    div = src["Div"].astype(str).str.strip() if "Div" in src.columns else pd.Series("", index=src.index)
    out = pd.DataFrame({
        "date": parsed.dt.strftime("%Y-%m-%d"), "time": ud.pick(src, ["Time"]), "div": div,
        "league": div.map({k: v[1] for k, v in C.FOOTBALL_LEAGUES.items()}).fillna(div),
        "home_team": src["HomeTeam"], "away_team": src["AwayTeam"],
        "odds_home": price("H"), "odds_draw": price("D"), "odds_away": price("A"),
        "odds_over25": price(">2.5"), "odds_under25": price("<2.5"), "ah_line": ud.num(ud.pick(src, ["AHh"])),
    })
    out = out[parsed.notna() & out["home_team"].notna() & out["away_team"].notna()]
    return out.sort_values(["date", "time", "div"], kind="stable").reset_index(drop=True)[cols]


def fetch_football_fixtures(book: str = "avg") -> tuple[pd.DataFrame, str]:
    """Scarica le partite in programma. Ritorna (tabella, messaggio di errore o '')."""
    for url in FIXTURES_URLS:
        raw = ud.http_get(url, retries=2)
        if raw is not None and b"HomeTeam" in raw[:600]:
            df = parse_football_fixtures(raw, book)
            covered = df["div"].isin(C.FOOTBALL_LEAGUES)
            return df, "" if len(df) else "Il file delle partite in programma è vuoto in questo momento."
    return pd.DataFrame(), "Impossibile scaricare le partite in programma da football-data.co.uk: controlla la connessione e riprova."


# --------------------------------------------------------------------------- tennis (The Odds API)
def parse_odds_api_events(events: list, tournament: str) -> list[dict]:
    """Eventi di un torneo -> righe {date, tournament, player1, player2, odds1, odds2, ...}.
    odds = media dei bookmaker; max = miglior quota; ps = Pinnacle se presente."""
    rows = []
    for ev in events or []:
        p1, p2 = ev.get("home_team"), ev.get("away_team")
        if not p1 or not p2:
            continue
        prices = {p1: [], p2: []}
        pinnacle = {}
        for bm in ev.get("bookmakers") or []:
            for market in bm.get("markets") or []:
                if market.get("key") != "h2h":
                    continue
                quote = {o.get("name"): o.get("price") for o in market.get("outcomes") or []}
                if all(isinstance(quote.get(p), (int, float)) and quote[p] > 1 for p in (p1, p2)):
                    prices[p1].append(float(quote[p1]))
                    prices[p2].append(float(quote[p2]))
                    if bm.get("key") == "pinnacle":
                        pinnacle = quote
        if not prices[p1]:
            continue
        when = pd.to_datetime(ev.get("commence_time"), errors="coerce", utc=True)
        rows.append({
            "date": when.tz_convert("Europe/Rome").strftime("%Y-%m-%d") if pd.notna(when) else None,
            "time": when.tz_convert("Europe/Rome").strftime("%H:%M") if pd.notna(when) else None,
            "tournament": tournament, "player1": p1, "player2": p2,
            "odds1": round(float(np.mean(prices[p1])), 3), "odds2": round(float(np.mean(prices[p2])), 3),
            "max1": max(prices[p1]), "max2": max(prices[p2]),
            "ps1": pinnacle.get(p1), "ps2": pinnacle.get(p2), "bookmakers": len(prices[p1]),
        })
    return rows


def fetch_tennis_odds(api_key: str, book: str = "avg", session=None) -> tuple[pd.DataFrame, str]:
    """Quote ATP dei tornei in corso da The Odds API. Ritorna (tabella, messaggio).
    `book`: 'avg' media, 'max' miglior quota, 'ps' Pinnacle (se disponibile, altrimenti media)."""
    api_key = (api_key or "").strip()
    if not api_key:
        return pd.DataFrame(), "Inserisci la chiave gratuita di The Odds API."
    http = session or requests
    try:
        r = http.get(f"{ODDS_API}/sports/", params={"apiKey": api_key}, timeout=30)
        if r.status_code in (401, 403):
            return pd.DataFrame(), "Chiave non valida o crediti del mese esauriti."
        r.raise_for_status()
        sports = [s for s in r.json() if str(s.get("key", "")).startswith("tennis_atp") and s.get("active")]
        rows, remaining = [], None
        for sp in sports:
            r = http.get(f"{ODDS_API}/sports/{sp['key']}/odds/",
                         params={"apiKey": api_key, "regions": "eu", "markets": "h2h", "oddsFormat": "decimal"}, timeout=30)
            if r.status_code in (401, 403, 429):
                return pd.DataFrame(rows), "Crediti del mese esauriti o troppe richieste: riprova più tardi."
            r.raise_for_status()
            remaining = r.headers.get("x-requests-remaining", remaining)
            rows += parse_odds_api_events(r.json(), str(sp.get("title", sp["key"])).replace("ATP ", "").strip())
    except (requests.RequestException, ValueError) as exc:
        return pd.DataFrame(), f"Servizio quote non raggiungibile: {exc}"
    df = pd.DataFrame(rows)
    if df.empty:
        return df, "Nessun torneo ATP con quote disponibili in questo momento."
    if book == "max":
        df["odds1"], df["odds2"] = df["max1"], df["max2"]
    elif book == "ps":
        df["odds1"] = pd.to_numeric(df["ps1"], errors="coerce").fillna(df["odds1"])
        df["odds2"] = pd.to_numeric(df["ps2"], errors="coerce").fillna(df["odds2"])
    msg = f"Crediti rimasti questo mese: {remaining}" if remaining is not None else ""
    return df.sort_values(["date", "time"], kind="stable").reset_index(drop=True), msg
