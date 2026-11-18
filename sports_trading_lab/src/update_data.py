"""Scarica, pulisce e normalizza gli storici di tennis e calcio in DuckDB.

Uso:
    python src/update_data.py                 # aggiornamento incrementale
    python src/update_data.py --full          # riscarica tutto ignorando la cache
    python src/update_data.py --sport tennis  # solo uno sport

Aggiornamento incrementale: le stagioni concluse restano in cache (`data/raw/`) e non vengono
riscaricate; quelle in corso vengono riscaricate e sostituite nel database (delete + insert per
stagione, dentro una transazione), quindi nessun record viene duplicato. Un indice UNIQUE su
`match_id` garantisce l'unicita' in ogni caso.
"""
from __future__ import annotations

import argparse
import csv
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import re
import sys
import time
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import config as C  # noqa: E402

SESSION = requests.Session()
SESSION.headers["User-Agent"] = "sports-trading-lab/1.0 (python-requests)"


def log(msg: str) -> None:
    print(msg, flush=True)


# --------------------------------------------------------------------------- download
def http_get(url: str, retries: int = 3) -> bytes | None:
    """GET con retry. Ritorna None se la risorsa non esiste o non e' raggiungibile."""
    for attempt in range(retries):
        try:
            r = SESSION.get(url, timeout=60)
            if r.status_code == 200 and r.content:
                return r.content
            if r.status_code in (403, 404, 410):
                return None
        except requests.RequestException:
            pass
        time.sleep(1.5 * (attempt + 1))
    return None


def fetch(cache: Path, urls: list[str], refresh: bool, validate) -> tuple[bytes | None, str, bool]:
    """Ritorna (contenuto, origine, cambiato). Usa la cache se presente e non serve il refresh;
    se il download fallisce ripiega sulla cache. `cambiato` e' False quando il file scaricato
    e' identico a quello gia' in cache (niente da ricaricare nel database)."""
    old = cache.read_bytes() if cache.exists() else None
    if old is not None and not refresh:
        return old, "cache", False
    for url in urls:
        raw = http_get(url)
        if raw is not None and validate(raw):
            if raw != old:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_bytes(raw)
            return raw, url, raw != old
    if old is not None:
        return old, "cache (download fallito)", False
    return None, "non disponibile", False


def fetch_best(cache: Path, urls: list[str], refresh: bool, validate) -> tuple[bytes | None, str, bool]:
    """Come `fetch`, ma prova tutte le fonti e tiene il file con piu' righe: le copie dei dati di Sackmann non
    sono aggiornate tutte allo stesso modo."""
    old = cache.read_bytes() if cache.exists() else None
    if old is not None and not refresh:
        return old, "cache", False
    best, src = None, ""
    for url in urls:
        raw = http_get(url)
        if raw is not None and validate(raw) and (best is None or raw.count(b"\n") > best.count(b"\n")):
            best, src = raw, url
    if best is None:
        return (old, "cache (download fallito)", False) if old is not None else (None, "non disponibile", False)
    if old is not None and old.count(b"\n") > best.count(b"\n"):
        return old, "cache", False
    if best != old:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(best)
    return best, src, best != old


def parallel(jobs: dict, workers: int = 8) -> dict:
    """Esegue in parallelo {chiave: funzione senza argomenti} e ritorna {chiave: risultato}."""
    if not jobs:
        return {}
    with ThreadPoolExecutor(max_workers=min(workers, len(jobs))) as pool:
        futures = {k: pool.submit(fn) for k, fn in jobs.items()}
        return {k: f.result() for k, f in futures.items()}


def decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def read_csv_loose(raw: bytes) -> pd.DataFrame:
    """Legge un CSV tollerando BOM, encoding misti, righe piu' lunghe/corte dell'header e righe vuote."""
    rows = list(csv.reader(io.StringIO(decode(raw))))
    if not rows:
        return pd.DataFrame()
    header = [h.strip() for h in rows[0]]
    keep = [i for i, h in enumerate(header) if h]
    names = [header[i] for i in keep]
    data = []
    for r in rows[1:]:
        if not any(c.strip() for c in r):
            continue
        r = r + [""] * (len(header) - len(r))
        data.append([r[i].strip() for i in keep])
    df = pd.DataFrame(data, columns=names, dtype=object)
    df = df.loc[:, ~df.columns.duplicated()]
    return df.mask(df == "", np.nan)


def num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").astype("float64")


def clean_odds(s: pd.Series) -> pd.Series:
    """Quote decimali valide: > 1 e < 1001; tutto il resto diventa NULL."""
    v = num(s)
    return v.where((v > 1.0) & (v < 1001.0))


def drop_outlier_odds(d: pd.DataFrame, cols: list[str]) -> int:
    """Annulla le quote incoerenti col consenso degli altri bookmaker sullo stesso esito
    (refusi nei file sorgente, prezzi exchange non liquidi tipo 1.01). Il riferimento e' la mediana
    delle probabilita' implicite; servono almeno 3 quote. Ritorna il numero di valori annullati."""
    cols = [c for c in cols if c in d.columns]
    if len(cols) < 3 or d.empty:
        return 0
    probs = 1 / d[cols]
    ref = probs.median(axis=1)
    ratio = probs.div(ref, axis=0)
    bad = (probs.sub(ref, axis=0).abs() > 0.06) | (ratio > 1 / 0.6) | (ratio < 0.6)
    bad = bad & (probs.notna().sum(axis=1) >= 3).to_numpy()[:, None]
    d[cols] = d[cols].mask(bad)
    return int(bad.to_numpy().sum())


def pick(df: pd.DataFrame, names: list[str]) -> pd.Series:
    for n in names:
        if n in df.columns:
            return df[n]
    return pd.Series(np.nan, index=df.index, dtype=object)


# --------------------------------------------------------------------------- calcio
def parse_football(raw: bytes, div: str, season: str) -> tuple[pd.DataFrame, int]:
    """Normalizza un CSV football-data.co.uk. Ritorna (dataframe, righe scartate)."""
    src = read_csv_loose(raw)
    if src.empty or "Date" not in src.columns:
        return pd.DataFrame(columns=C.FOOT_COLUMNS), 0
    n_in = len(src)
    d = pd.DataFrame(index=src.index)
    dates = src["Date"].astype(str).str.strip()
    parsed = pd.to_datetime(dates, format="%d/%m/%Y", errors="coerce")
    short = pd.to_datetime(dates, format="%d/%m/%y", errors="coerce")
    d["match_date"] = parsed.fillna(short)
    d["kickoff"] = pick(src, ["Time"])
    d["home_team"] = pick(src, ["HomeTeam", "HT"])
    d["away_team"] = pick(src, ["AwayTeam", "AT"])
    d["fthg"], d["ftag"] = num(pick(src, ["FTHG", "HG"])), num(pick(src, ["FTAG", "AG"]))
    d["hthg"], d["htag"] = num(pick(src, ["HTHG"])), num(pick(src, ["HTAG"]))
    d["ftr"] = pick(src, ["FTR", "Res"]).astype(str).str.strip().str.upper()
    d["htr"] = pick(src, ["HTR"])
    for dest, col in C.FOOT_STATS.items():
        d[dest] = num(pick(src, [col]))
    for dest, cols in C.FOOT_ODDS_MAP.items():
        d[dest] = clean_odds(pick(src, cols))
    outliers = 0
    for suffix in ("h", "d", "a", "ch", "cd", "ca", "o25", "u25", "co25", "cu25"):
        outliers += drop_outlier_odds(d, [f"{b}_{suffix}" for b in C.FOOT_BOOKS])
    d.attrs["outliers"] = outliers
    d["ah_line"] = num(pick(src, ["AHh", "BbAHh"]))
    d["ah_cline"] = num(pick(src, ["AHCh"]))

    ok = (
        d["match_date"].notna() & d["home_team"].notna() & d["away_team"].notna()
        & d["fthg"].notna() & d["ftag"].notna() & d["ftr"].isin(["H", "D", "A"])
    )
    d = d[ok].copy()
    # il risultato deve essere coerente con i gol
    calc = np.where(d["fthg"] > d["ftag"], "H", np.where(d["fthg"] < d["ftag"], "A", "D"))
    d = d[d["ftr"].to_numpy() == calc].copy()

    start = 2000 + int(season[:2])
    d["div"] = div
    d["country"], d["league"] = C.FOOTBALL_LEAGUES[div]
    d["season"] = f"{start}/{(start + 1) % 100:02d}"
    d["season_start"] = start
    d["total_goals"] = d["fthg"] + d["ftag"]
    d["match_id"] = (
        div + "|" + d["match_date"].dt.strftime("%Y%m%d") + "|" + d["home_team"] + "|" + d["away_team"]
    )
    d = d.drop_duplicates("match_id")
    for f in C.FOOT_FEATURES:
        d[f] = np.nan
    out = d[C.FOOT_COLUMNS].reset_index(drop=True)
    out.attrs["outliers"] = outliers
    return out, n_in - len(d)



def _roll_mean(long: pd.DataFrame, keys, cols: list[str], window: int, min_periods: int) -> pd.DataFrame:
    """Media mobile delle `window` osservazioni precedenti (quella corrente esclusa) per gruppo."""
    shifted = long.groupby(keys, sort=False)[cols].shift(1)
    key_frame = long[[keys] if isinstance(keys, str) else keys]
    out = pd.concat([key_frame, shifted], axis=1).groupby(keys, sort=False)[cols].rolling(window, min_periods=min_periods).mean()
    return out.reset_index(level=list(range(out.index.nlevels - 1)), drop=True).reindex(long.index)


def _col(df: pd.DataFrame, name: str) -> pd.Series:
    return df[name] if name in df.columns else pd.Series(np.nan, index=df.index, dtype="float64")


def _table_positions(long: pd.DataFrame) -> pd.Series:
    """Posizione in classifica prima della partita (punti, differenza reti, gol fatti); NaN nelle prime 3 giornate."""
    pos = pd.Series(np.nan, index=long.index, dtype="float64")
    for _, grp in long.groupby(["div", "season"], sort=False):
        state = {t: [0.0, 0.0, 0.0, 0] for t in grp["team"].unique()}  # punti, diff. reti, gol fatti, giocate
        for _, day in grp.groupby("date", sort=True):
            order = sorted(state, key=lambda t: (-state[t][0], -state[t][1], -state[t][2], t))
            rank = {t: i + 1 for i, t in enumerate(order)}
            for idx, team in zip(day.index, day["team"]):
                if state[team][3] >= 3:
                    pos[idx] = rank[team]
            for team, pts, gf, ga in zip(day["team"], day["pts"], day["gf"], day["ga"]):
                if gf != gf or ga != ga:  # NaN: partita non ancora giocata
                    continue
                st = state[team]
                st[0] += pts
                st[1] += gf - ga
                st[2] += gf
                st[3] += 1
    return pos


# --------------------------------------------------------------------------- modello Elo
ELO_START, ELO_HOME = 1500.0, 65.0
ELO_TIER = {"E0": 0, "D1": 0, "I1": 0, "SP1": 0, "F1": 0, "N1": -60, "P1": -60, "B1": -90, "T1": -90, "SC0": -120, "G1": -120,
            "E1": -150, "D2": -160, "I2": -170, "SP2": -160, "F2": -170, "E2": -290, "E3": -380}


def football_elo(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Valutazione Elo delle due squadre PRIMA di ogni partita (ordine cronologico, nessun look-ahead).
    Vantaggio del campo 65 punti, K = 20 moltiplicato per lo scarto di gol; le partite senza risultato
    (in programma) ricevono la valutazione corrente e non la modificano."""
    order = np.lexsort((df["match_id"].to_numpy(), pd.to_datetime(df["match_date"]).to_numpy()))
    home, away = df["home_team"].to_numpy(), df["away_team"].to_numpy()
    hg, ag = df["fthg"].to_numpy(dtype=float), df["ftag"].to_numpy(dtype=float)
    div = df["div"].to_numpy() if "div" in df.columns else np.full(len(df), None)
    rating: dict = {}
    eh, ea = np.full(len(df), np.nan), np.full(len(df), np.nan)
    for i in order:
        start = ELO_START + ELO_TIER.get(div[i], -150)
        rh, ra = rating.get(home[i], start), rating.get(away[i], start)
        eh[i], ea[i] = rh, ra
        if hg[i] != hg[i] or ag[i] != ag[i]:
            continue
        exp_h = 1 / (1 + 10 ** (-(rh + ELO_HOME - ra) / 400))
        res = 1.0 if hg[i] > ag[i] else 0.5 if hg[i] == ag[i] else 0.0
        k = 20 * (1 + abs(hg[i] - ag[i])) ** 0.5
        rating[home[i]], rating[away[i]] = rh + k * (res - exp_h), ra - k * (res - exp_h)
    return eh, ea


def tennis_elo(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Elo dei due giocatori PRIMA di ogni match: media tra Elo generale ed Elo sulla superficie.
    K decrescente con l'esperienza (250 / (match giocati + 5)^0.4). I match futuri non aggiornano nulla."""
    dates = pd.to_datetime(df["match_date"]).to_numpy()
    rnd = df["round_order"].fillna(0).to_numpy(dtype=float) if "round_order" in df.columns else np.zeros(len(df))
    order = np.lexsort((df["match_id"].to_numpy(), rnd, dates))
    w, l = df["winner_name"].to_numpy(), df["loser_name"].to_numpy()
    surf = df["surface"].fillna("-").to_numpy() if "surface" in df.columns else np.full(len(df), "-")
    future = df["is_future"].fillna(False).to_numpy(dtype=bool) if "is_future" in df.columns else np.zeros(len(df), dtype=bool)
    gen: dict = {}
    by_surf: dict = {}
    played: dict = {}
    ew, el = np.full(len(df), np.nan), np.full(len(df), np.nan)
    for i in order:
        gw, gl = gen.get(w[i], ELO_START), gen.get(l[i], ELO_START)
        sw, sl = by_surf.get((w[i], surf[i]), gw), by_surf.get((l[i], surf[i]), gl)
        ew[i], el[i] = (gw + sw) / 2, (gl + sl) / 2
        if future[i]:
            continue
        kw, kl = 250 / (played.get(w[i], 0) + 5) ** 0.4, 250 / (played.get(l[i], 0) + 5) ** 0.4
        exp_g = 1 / (1 + 10 ** ((gl - gw) / 400))
        exp_s = 1 / (1 + 10 ** ((sl - sw) / 400))
        gen[w[i]], gen[l[i]] = gw + kw * (1 - exp_g), gl - kl * (1 - exp_g)
        by_surf[(w[i], surf[i])], by_surf[(l[i], surf[i])] = sw + kw * (1 - exp_s), sl - kl * (1 - exp_s)
        played[w[i]], played[l[i]] = played.get(w[i], 0) + 1, played.get(l[i], 0) + 1
    return ew, el


def football_features(df: pd.DataFrame) -> pd.DataFrame:
    """Feature pre-partita senza look-ahead per squadra di casa e ospite:
    ppg5/gf5/ga5 (ultime 5 partite), venue_ppg5 (ultime 5 in casa / in trasferta), sot5/sota5 (tiri in porta
    fatti/subiti), corners5, pos (classifica), sppg (punti a partita in stagione), rest_days (max 30)."""
    df = df.reset_index(drop=True)
    sides = []
    for side, opp, win in (("home", "away", "H"), ("away", "home", "A")):
        gf, ga = ("fthg", "ftag") if side == "home" else ("ftag", "fthg")
        sides.append(pd.DataFrame({
            "match_id": df["match_id"], "side": side, "team": df[f"{side}_team"], "date": pd.to_datetime(df["match_date"]),
            "div": _col(df, "div"), "season": _col(df, "season_start"),
            "pts": np.where(df["ftr"].isna(), np.nan, np.select([df["ftr"] == win, df["ftr"] == "D"], [3.0, 1.0], 0.0)),
            "gf": df[gf].astype(float), "ga": df[ga].astype(float),
            "sot": _col(df, f"{side}_sot"), "sota": _col(df, f"{opp}_sot"), "corners": _col(df, f"{side}_corners"),
        }))
    long = pd.concat(sides).sort_values(["team", "date", "match_id"], kind="stable").reset_index(drop=True)
    long[["div", "season"]] = long[["div", "season"]].astype(object).where(long[["div", "season"]].notna(), "-")
    src = ["pts", "gf", "ga", "sot", "sota", "corners"]
    long[["ppg5", "gf5", "ga5", "sot5", "sota5", "corners5"]] = _roll_mean(long, "team", src, 5, 5).to_numpy()
    long["venue_ppg5"] = _roll_mean(long, ["team", "side"], ["pts"], 5, 5)["pts"].to_numpy()
    long["rest_days"] = long.groupby("team", sort=False)["date"].diff().dt.days.clip(upper=30)
    season = long.groupby(["team", "div", "season"], sort=False)
    done = long["pts"].notna().astype(float)
    tot = long.assign(_d=done, _p=long["pts"].fillna(0)).groupby(["team", "div", "season"], sort=False)[["_d", "_p"]].cumsum()
    played = tot["_d"] - done
    long["sppg"] = ((tot["_p"] - long["pts"].fillna(0)) / played).where(played >= 3)
    long["pos"] = _table_positions(long)
    out = pd.DataFrame({"match_id": df["match_id"]})
    for side in ("home", "away"):
        part = long[long["side"] == side].set_index("match_id")
        for k in C.FOOT_FEATURE_KEYS:
            if k != "elo":
                out[f"{side}_{k}"] = out["match_id"].map(part[k]).astype("float64")
    out["home_elo"], out["away_elo"] = football_elo(df)
    return out[["match_id"] + C.FOOT_FEATURES]


# --------------------------------------------------------------------------- tennis
def norm_key(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", s.lower())


def td_surname_key(name: str) -> str:
    """'Auger-Aliassime F.' -> 'augeraliassime' (toglie le iniziali finali)."""
    tokens = str(name).strip().split()
    while len(tokens) > 1 and tokens[-1].endswith("."):
        tokens.pop()
    return norm_key("".join(tokens))


def full_name_keys(name: str) -> list[str]:
    """'Juan Martin del Potro' -> ['potro', 'delpotro', 'martindelpotro'] (possibili cognomi)."""
    tokens = str(name).strip().split()
    if len(tokens) <= 1:
        return [norm_key(name)]
    keys = [norm_key("".join(tokens[-k:])) for k in range(1, len(tokens))]
    keys.append(norm_key(tokens[0]))  # ordine cognome-nome (es. giocatori asiatici)
    return list(dict.fromkeys(k for k in keys if k))


def parse_tennis_odds(raw: bytes) -> pd.DataFrame:
    """File annuale tennis-data.co.uk (xlsx/xls oppure csv dei mirror)."""
    if raw[:2] == b"PK" or raw[:4] == b"\xd0\xcf\x11\xe0":
        src = pd.read_excel(io.BytesIO(raw))
    else:
        src = read_csv_loose(raw)
    if "Winner" not in src.columns or "Date" not in src.columns:
        return pd.DataFrame()
    src.columns = [str(c).strip() for c in src.columns]
    d = pd.DataFrame(index=src.index)
    d["match_date"] = pd.to_datetime(src["Date"], errors="coerce")
    d["td_tournament"] = src["Tournament"].astype(str).str.strip()
    d["series"] = pick(src, ["Series"])
    d["td_surface"] = pick(src, ["Surface"])
    d["td_indoor"] = pick(src, ["Court"]).astype(str).str.strip().str.lower().map({"indoor": True, "outdoor": False})
    d["td_round"] = pick(src, ["Round"])
    d["td_best_of"] = num(pick(src, ["Best of"]))
    d["td_winner"] = src["Winner"].astype(str).str.strip()
    d["td_loser"] = src["Loser"].astype(str).str.strip()
    d["td_wrank"], d["td_lrank"] = num(pick(src, ["WRank"])), num(pick(src, ["LRank"]))
    d["td_wpts"], d["td_lpts"] = num(pick(src, ["WPts"])), num(pick(src, ["LPts"]))
    sets = []
    for i in range(1, 6):
        w, l = num(pick(src, [f"W{i}"])), num(pick(src, [f"L{i}"]))
        sets.append(np.where(w.notna() & l.notna(), w.fillna(0).astype(int).astype(str) + "-" + l.fillna(0).astype(int).astype(str), ""))
    d["td_score"] = pd.Series([" ".join(x for x in row if x) for row in zip(*sets)], index=src.index)
    d["td_status"] = pick(src, ["Comment"]).astype(str).str.strip().str.title().replace({"Nan": "Completed", "Sched": "Completed"})
    d["ps_w"], d["ps_l"] = clean_odds(pick(src, ["PSW"])), clean_odds(pick(src, ["PSL"]))
    d["b365_w"], d["b365_l"] = clean_odds(pick(src, ["B365W"])), clean_odds(pick(src, ["B365L"]))
    d["max_w"], d["max_l"] = clean_odds(pick(src, ["MaxW"])), clean_odds(pick(src, ["MaxL"]))
    d["avg_w"], d["avg_l"] = clean_odds(pick(src, ["AvgW"])), clean_odds(pick(src, ["AvgL"]))
    for s in "wl":
        d.attrs["outliers"] = d.attrs.get("outliers", 0) + drop_outlier_odds(d, [f"{b}_{s}" for b in C.TENNIS_BOOKS])
    d = d[d["match_date"].notna() & d["td_winner"].notna() & d["td_loser"].notna() & (d["td_winner"] != "nan") & (d["td_loser"] != "nan")]
    d = d.drop_duplicates(["match_date", "td_winner", "td_loser"]).reset_index(drop=True)
    d["wk"] = d["td_winner"].map(td_surname_key)
    d["lk"] = d["td_loser"].map(td_surname_key)
    return d


def parse_sackmann(raw: bytes, circuit: str) -> pd.DataFrame:
    """File in formato Sackmann (tennis_atp / TML-Database)."""
    src = read_csv_loose(raw)
    if src.empty or "winner_name" not in src.columns:
        return pd.DataFrame()
    d = pd.DataFrame(index=src.index)
    for c in ("tourney_id", "tourney_name", "tourney_level", "surface", "round", "winner_name", "loser_name",
              "winner_id", "loser_id", "winner_hand", "loser_hand", "winner_ioc", "loser_ioc", "score"):
        d[c] = pick(src, [c]).astype(str).str.strip().replace({"nan": None})
    for c in ("winner_id", "loser_id"):
        d[c] = d[c].str.replace(r"\.0$", "", regex=True)
    d["surface"] = d["surface"].str.title()
    d["tourney_date"] = pd.to_datetime(pick(src, ["tourney_date"]).astype(str).str.slice(0, 8), format="%Y%m%d", errors="coerce")
    d["match_num"] = pick(src, ["match_num"]).astype(str).str.replace(r"\.0$", "", regex=True)
    d["indoor"] = pick(src, ["indoor"]).astype(str).str.upper().map({"I": True, "O": False})
    for c in ("draw_size", "best_of", "minutes", "winner_rank", "loser_rank", "winner_rank_points",
              "loser_rank_points", "winner_age", "loser_age", "winner_ht", "loser_ht"):
        d[c] = num(pick(src, [c]))
    for s in "wl":
        for c in C.TENNIS_STATS:
            d[f"{s}_{c}"] = num(pick(src, [f"{s}_{c}"]))
    d = d[d["tourney_date"].notna() & d["winner_name"].notna() & d["loser_name"].notna()].copy()
    if circuit == "CHALLENGER":  # il file qual_chall contiene anche le qualificazioni ATP
        d["circuit"] = np.where(d["tourney_level"] == "C", "CHALLENGER", "ATP_QUALI")
    else:
        d["circuit"] = "ATP"
    fallback = (d["round"].fillna("") + "-" + d["winner_name"] + "-" + d["loser_name"]).map(
        lambda x: hashlib.md5(x.encode()).hexdigest()[:10])
    match_num = d["match_num"].where(d["match_num"].notna() & ~d["match_num"].isin(["nan", "None", ""]), fallback)
    d["match_id"] = d["circuit"] + ":" + d["tourney_id"].astype(str) + ":" + match_num
    score = d["score"].fillna("").str.upper()
    d["status"] = np.select(
        [score.str.contains("W/O|WALKOVER"), score.str.contains("RET"), score.str.contains("DEF")],
        ["Walkover", "Retired", "Retired"], "Completed",
    )
    return d.drop_duplicates("match_id").reset_index(drop=True)


def match_odds(sack: pd.DataFrame, odds: pd.DataFrame) -> pd.Series:
    """Per ogni riga `sack` l'indice della riga `odds` corrispondente (NaN se assente).
    Join su cognome vincitore + cognome perdente con finestra temporale sul torneo."""
    if sack.empty or odds.empty:
        return pd.Series(np.nan, index=sack.index)
    left = pd.DataFrame({
        "si": sack.index, "tdate": sack["tourney_date"],
        "wk": sack["winner_name"].map(full_name_keys), "lk": sack["loser_name"].map(full_name_keys),
    }).explode("wk").explode("lk")
    right = odds[["wk", "lk", "match_date"]].reset_index().rename(columns={"index": "oi"})
    m = left.merge(right, on=["wk", "lk"])
    m["gap"] = (m["match_date"] - m["tdate"]).dt.days
    m = m[(m["gap"] >= -4) & (m["gap"] <= 24)]
    # una partita si gioca dopo l'inizio del torneo: i gap negativi sono ammessi solo come ripiego
    m["cost"] = np.where(m["gap"] >= 0, m["gap"], 1000 - m["gap"])
    m = m.sort_values(["cost", "oi", "si"]).drop_duplicates(["oi", "si"])
    used_o, used_s, pairs = set(), set(), {}
    for oi, si in zip(m["oi"].to_numpy(), m["si"].to_numpy()):  # assegnazione greedy uno-a-uno
        if oi not in used_o and si not in used_s:
            used_o.add(oi)
            used_s.add(si)
            pairs[si] = oi
    return pd.Series(pairs, dtype="float64").reindex(sack.index)


def build_tennis_year(year: int, tour: pd.DataFrame, chall: pd.DataFrame, odds: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Unisce statistiche (formato Sackmann) e quote (tennis-data) di un anno in righe normalizzate."""
    info = {"tour": len(tour), "chall": len(chall), "odds": len(odds), "matched": 0}
    parts = []
    used = set()
    if not tour.empty:
        oi = match_odds(tour, odds)
        t = tour.copy()
        if not odds.empty:
            o = odds.reindex(oi.to_numpy())
            o.index = t.index
            t = pd.concat([t, o.drop(columns=["wk", "lk"])], axis=1)
            used = set(oi.dropna().astype(int))
            info["matched"] = len(used)
        parts.append(t)
    if not odds.empty:
        rest = odds[~odds.index.isin(used)].drop(columns=["wk", "lk"]).copy()
        if not rest.empty:
            # mappa 'Sinner J.' -> 'Jannik Sinner' appresa dalle righe abbinate
            names = {}
            if parts:
                t = parts[0]
                ok = t["td_winner"].notna() if "td_winner" in t else pd.Series(False, index=t.index)
                names.update(dict(zip(t.loc[ok, "td_winner"], t.loc[ok, "winner_name"])))
                names.update(dict(zip(t.loc[ok, "td_loser"], t.loc[ok, "loser_name"])))
            rest["circuit"] = "ATP"
            rest["winner_name"] = rest["td_winner"].map(lambda n: names.get(n, n))
            rest["loser_name"] = rest["td_loser"].map(lambda n: names.get(n, n))
            rest["tourney_name"] = rest["td_tournament"]
            rest["tourney_date"] = rest.groupby("td_tournament")["match_date"].transform("min")
            rest["match_id"] = [
                "TD:" + hashlib.md5(f"{d:%Y%m%d}|{w}|{l}".encode()).hexdigest()[:16]
                for d, w, l in zip(rest["match_date"], rest["td_winner"], rest["td_loser"])
            ]
            parts.append(rest)
    if not chall.empty:
        parts.append(chall.copy())
    if not parts:
        return pd.DataFrame(columns=C.TENNIS_COLUMNS), info
    d = pd.concat(parts, ignore_index=True)
    for c in C.TENNIS_COLUMNS + ["td_surface", "td_indoor", "td_round", "td_best_of", "td_wrank", "td_lrank",
                                 "td_wpts", "td_lpts", "td_score", "td_status"]:
        if c not in d.columns:
            d[c] = np.nan
    d["season"] = year
    d["match_date"] = pd.to_datetime(d["match_date"]).fillna(d["tourney_date"])
    d["surface"] = d["surface"].fillna(d["td_surface"])
    d["indoor"] = d["indoor"].astype(object).where(d["indoor"].notna(), d["td_indoor"]).astype("boolean")
    d["round"] = d["round"].fillna(d["td_round"])
    d["round_order"] = d["round"].map(C.ROUND_ORDER).astype("float64")
    d["best_of"] = d["best_of"].fillna(d["td_best_of"])
    d["winner_rank"] = d["winner_rank"].fillna(d["td_wrank"])
    d["loser_rank"] = d["loser_rank"].fillna(d["td_lrank"])
    d["winner_rank_points"] = d["winner_rank_points"].fillna(d["td_wpts"])
    d["loser_rank_points"] = d["loser_rank_points"].fillna(d["td_lpts"])
    d["score"] = d["score"].astype(object).where(d["score"].notna(), d["td_score"])
    d["status"] = d["td_status"].where(d["td_status"].isin(["Completed", "Retired", "Walkover"]), d["status"]).fillna("Completed")
    d["has_odds"] = d[[f"{b}_{s}" for b in C.TENNIS_BOOKS for s in "wl"]].notna().any(axis=1)
    d = d.drop_duplicates("match_id")
    return d[C.TENNIS_COLUMNS].reset_index(drop=True), info



def tennis_features(df: pd.DataFrame) -> pd.DataFrame:
    """Feature pre-partita senza look-ahead per vincitore e perdente (valori in frazione 0-1 dove percentuali):
    form10 (% vittorie ultime 10), form_surf (ultime 20 sulla stessa superficie), rest_days (max 60),
    matches14 (partite nei 14 giorni precedenti), h2h_wins (vittorie precedenti contro l'avversario),
    srv_won10 / ret_won10 (punti vinti al servizio / in risposta), ace10, df10 (per punto di servizio)."""
    df = df.reset_index(drop=True)
    sides = []
    for a, b, won in (("winner", "loser", 1.0), ("loser", "winner", 0.0)):
        x, y = a[0], b[0]
        sides.append(pd.DataFrame({
            "match_id": df["match_id"], "side": a, "player": df[f"{a}_name"], "opponent": df[f"{b}_name"],
            "date": pd.to_datetime(df["match_date"]), "round_order": _col(df, "round_order"),
            "surface": df["surface"].fillna("-") if "surface" in df.columns else "-",
            "won": np.where(df["is_future"].fillna(False).astype(bool), np.nan, won) if "is_future" in df.columns else won,
            "svpt": _col(df, f"{x}_svpt"), "sv_won": _col(df, f"{x}_1stWon") + _col(df, f"{x}_2ndWon"),
            "ace": _col(df, f"{x}_ace"), "dfault": _col(df, f"{x}_df"),
            "o_svpt": _col(df, f"{y}_svpt"), "o_sv_won": _col(df, f"{y}_1stWon") + _col(df, f"{y}_2ndWon"),
        }))
    long = pd.concat(sides).sort_values(["player", "date", "round_order", "match_id"], kind="stable").reset_index(drop=True)
    # statistiche valide solo se il match ha i punti di servizio di entrambi
    ok = (long["svpt"] > 0) & (long["o_svpt"] > 0)
    stat_cols = ["svpt", "sv_won", "ace", "dfault", "o_svpt", "o_sv_won"]
    long.loc[~ok, stat_cols] = np.nan
    long["form10"] = _roll_mean(long, "player", ["won"], 10, 5)["won"].to_numpy()
    long["form_surf"] = _roll_mean(long, ["player", "surface"], ["won"], 20, 5)["won"].to_numpy()
    grp = long.groupby("player", sort=False)
    rest = grp["date"].diff().dt.days
    long["rest_days"] = rest.where(rest > 0).clip(upper=60)  # 0 = data del match non nota (solo data torneo)
    recent = long.set_index("date").groupby("player", sort=False)["won"].rolling("14D", closed="left", min_periods=0).count()
    long["matches14"] = recent.to_numpy()
    long["h2h_wins"] = long.assign(_w=long["won"].fillna(0)).groupby(["player", "opponent"], sort=False)["_w"].cumsum() - long["won"].fillna(0)
    sums = _roll_mean(long, "player", stat_cols, 10, 5)  # medie: i rapporti tra medie equivalgono ai rapporti tra somme
    long["srv_won10"] = (sums["sv_won"] / sums["svpt"]).to_numpy()
    long["ret_won10"] = (1 - sums["o_sv_won"] / sums["o_svpt"]).to_numpy()
    long["ace10"] = (sums["ace"] / sums["svpt"]).to_numpy()
    long["df10"] = (sums["dfault"] / sums["svpt"]).to_numpy()
    out = pd.DataFrame({"match_id": df["match_id"]})
    for side in ("winner", "loser"):
        part = long[long["side"] == side].set_index("match_id")
        for k in C.TENNIS_FEATURE_KEYS:
            if k != "elo":
                out[f"{side}_{k}"] = out["match_id"].map(part[k]).astype("float64")
    out["winner_elo"], out["loser_elo"] = tennis_elo(df)
    return out[["match_id"] + C.TENNIS_FEATURES]


# --------------------------------------------------------------------------- database

def _types(columns: list[str], text: list[str], floats: list[str]) -> dict[str, str]:
    types = {}
    for c in columns:
        if c in text:
            types[c] = "VARCHAR"
        elif c in floats:
            types[c] = "DOUBLE"
        elif c.endswith("_date"):
            types[c] = "DATE"
        elif c in ("indoor", "has_odds"):
            types[c] = "BOOLEAN"
        else:
            types[c] = "INTEGER"
    return types



def init_db(con: duckdb.DuckDBPyConnection) -> None:
    """Crea le tabelle se mancano e aggiunge le colonne nuove a un database creato da una versione precedente."""
    specs = {
        "football_matches": _types(C.FOOT_COLUMNS, C.FOOT_TEXT_COLS, C.FOOT_FLOAT_COLS),
        "tennis_matches": _types(C.TENNIS_COLUMNS, C.TENNIS_TEXT_COLS, C.TENNIS_FLOAT_COLS),
    }
    for table, types in specs.items():
        cols = ", ".join(f'"{c}" {t}' for c, t in types.items())
        con.execute(f"CREATE TABLE IF NOT EXISTS {table} ({cols})")
        have = {r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()}
        for c, t in types.items():
            if c not in have:
                con.execute(f'ALTER TABLE {table} ADD COLUMN "{c}" {t}')
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_football ON football_matches(match_id)")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_tennis ON tennis_matches(match_id)")
    con.execute("CREATE TABLE IF NOT EXISTS meta (key VARCHAR PRIMARY KEY, value VARCHAR)")


def canonicalize_tennis_names(con) -> int:
    """Le fonti scrivono alcuni nomi in modi diversi ('Auger-Aliassime' / 'Auger Aliassime'): senza
    unificarli lo stesso giocatore avrebbe form e precedenti spezzati. Per ogni giocatore vince la grafia
    piu' usata nel circuito ATP (poi la piu' frequente in assoluto). Ritorna quanti nomi sono stati corretti."""
    names = con.execute("""
        SELECT name, sum((circuit = 'ATP')::int) AS atp, count(*) AS n FROM (
            SELECT winner_name AS name, circuit FROM tennis_matches UNION ALL
            SELECT loser_name, circuit FROM tennis_matches) GROUP BY 1""").df()
    names = names[names["name"].notna()]
    if names.empty:
        return 0
    names["key"] = names["name"].map(norm_key)
    best = names.sort_values(["atp", "n", "name"], ascending=[False, False, True]).drop_duplicates("key").set_index("key")["name"]
    names["canon"] = names["key"].map(best)
    fix = names.loc[(names["name"] != names["canon"]) & (names["key"] != ""), ["name", "canon"]]
    if fix.empty:
        return 0
    con.register("_fix", fix)
    for col in ("winner_name", "loser_name"):
        con.execute(f"UPDATE tennis_matches t SET {col} = f.canon FROM _fix f WHERE t.{col} = f.name")
    con.unregister("_fix")
    return len(fix)


def normalize_tennis_labels(con) -> int:
    """Turni con codici unici (Q1..Q3, R128..R16, QF, SF, F, RR, BR). tennis-data scrive '1st Round', '2nd Round'...
    il cui significato dipende dal tabellone: il turno n di un torneo che arriva fino all'n-esimo turno numerato k
    e' R(16 * 2^(k-n)) (per esempio negli Slam il 1st Round e' R128, in un ATP 250 a 32 giocatori e' R32)."""
    fixed = 0
    for old, new in (("The Final", "F"), ("Final", "F"), ("Semifinals", "SF"), ("Quarterfinals", "QF"), ("Round Robin", "RR")):
        fixed += con.execute("UPDATE tennis_matches SET round = ? WHERE round = ?", [new, old]).fetchone()[0]
    rows = con.execute("""SELECT match_id, season, tourney_name, round FROM tennis_matches
                          WHERE regexp_matches(round, '^[0-9](st|nd|rd|th) Round$')""").df()
    if len(rows):
        rows["n"] = rows["round"].str[0].astype(int)
        top = rows.groupby(["season", "tourney_name"])["n"].transform("max")
        rows["new"] = "R" + (16 * 2 ** (top - rows["n"])).astype(str)
        con.register("_rounds", rows[["match_id", "new"]])
        con.execute("UPDATE tennis_matches t SET round = r.new FROM _rounds r WHERE t.match_id = r.match_id")
        con.unregister("_rounds")
        fixed += len(rows)
    con.execute("UPDATE tennis_matches SET round_order = CASE round WHEN 'Q1' THEN 0 WHEN 'Q2' THEN 1 WHEN 'Q3' THEN 2 "
                "WHEN 'RR' THEN 3 WHEN 'R128' THEN 4 WHEN 'R64' THEN 5 WHEN 'R32' THEN 6 WHEN 'R16' THEN 7 WHEN 'QF' THEN 8 "
                "WHEN 'SF' THEN 9 WHEN 'BR' THEN 9.5 WHEN 'F' THEN 10 ELSE round_order END")
    return fixed


def refresh_features(con, sport: str = "all") -> None:
    """Ricalcola da zero tutte le feature pre-partita (non richiede download)."""
    if sport in ("all", "football"):
        df = con.execute("""SELECT match_id, div, season_start, home_team, away_team, match_date, ftr, fthg, ftag,
                                   home_sot, away_sot, home_corners, away_corners FROM football_matches""").df()
        if not df.empty:
            write_features(con, "football_matches", football_features(df))
    if sport in ("all", "tennis"):
        canonicalize_tennis_names(con)
        normalize_tennis_labels(con)
        df = con.execute("""SELECT match_id, match_date, round_order, surface, winner_name, loser_name,
                                   w_svpt, w_1stWon, w_2ndWon, w_ace, w_df, l_svpt, l_1stWon, l_2ndWon, l_ace, l_df
                            FROM tennis_matches""").df()
        if not df.empty:
            write_features(con, "tennis_matches", tennis_features(df))
    con.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", [C.SCHEMA_VERSION])


def needs_feature_refresh(con) -> bool:
    try:
        row = con.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    except duckdb.Error:
        return True
    return row is None or row[0] != C.SCHEMA_VERSION


def replace_rows(con, table: str, df: pd.DataFrame, where: str, params: list) -> None:
    """Sostituzione atomica di una partizione (stagione): nessun duplicato possibile."""
    cols = ", ".join(f'"{c}"' for c in df.columns)
    con.execute("BEGIN")
    try:
        con.execute(f"DELETE FROM {table} WHERE {where}", params)
        if not df.empty:
            con.register("_incoming", df)
            con.execute(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM _incoming")
            con.unregister("_incoming")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def write_features(con, table: str, feats: pd.DataFrame) -> None:
    sets = ", ".join(f'"{c}" = f."{c}"' for c in feats.columns if c != "match_id")
    con.register("_feats", feats)
    con.execute(f"UPDATE {table} t SET {sets} FROM _feats f WHERE t.match_id = f.match_id")
    con.unregister("_feats")


# --------------------------------------------------------------------------- orchestrazione
def refresh_window(today: date | None = None) -> tuple[int, int]:
    """Quante stagioni calcio e quanti anni tennis riscaricare: di norma solo quelli in corso;
    anche i precedenti nei mesi in cui possono ancora ricevere rettifiche."""
    today = today or date.today()
    return (2 if today.month in (6, 7, 8, 9) else 1), (2 if today.month in (1, 2) else 1)


def update_football(con, full: bool, progress) -> dict:
    seasons = C.football_seasons()
    current = set(seasons[-refresh_window()[0]:])
    have = {(d, s) for d, s in con.execute("SELECT DISTINCT div, season_start FROM football_matches").fetchall()}
    stats = {"files": 0, "rows": 0, "dropped": 0, "outliers": 0, "missing": [], "changed": False}
    ok = lambda b: b"Date" in b[:400] or b"Div" in b[:400]
    todo = []
    for season in seasons:
        start = 2000 + int(season[:2])
        for div in C.FOOTBALL_LEAGUES:
            cache = C.RAW_DIR / "football" / f"{div}_{season}.csv"
            refresh = full or season in current
            if not refresh and cache.exists() and (div, start) in have:
                continue  # stagione conclusa gia' caricata
            todo.append((div, season, start, cache, refresh))
    fetched = parallel({(d, s): (lambda d=d, s=s, c=c, r=r: fetch(c, [C.FOOTBALL_URL.format(season=s, div=d)], r, ok))
                        for d, s, _, c, r in todo})
    for i, (div, season, start, cache, refresh) in enumerate(todo, 1):
        progress(f"calcio {div} {season}", i, len(todo))
        raw, origin, changed = fetched[(div, season)]
        if raw is None:
            stats["missing"].append(f"{div}_{season}")
            continue
        if not changed and not full and (div, start) in have:
            continue  # file identico a quello gia' caricato
        df, dropped = parse_football(raw, div, season)
        replace_rows(con, "football_matches", df, "div = ? AND season_start = ?", [div, start])
        stats["changed"] = True
        stats["files"] += 1
        stats["rows"] += len(df)
        stats["dropped"] += dropped
        stats["outliers"] += df.attrs.get("outliers", 0)
    if stats["changed"] or needs_feature_refresh(con):
        refresh_features(con, "football")
    return stats


def _best_odds_file(year: int, refresh: bool) -> tuple[pd.DataFrame, str, bool]:
    """Ritorna (quote, origine, cambiato)."""
    cache = C.RAW_DIR / "tennis" / f"odds_{year}.bin"
    ok = lambda b: len(b) > 2000 and (b[:2] == b"PK" or b[:4] == b"\xd0\xcf\x11\xe0" or b"Winner" in b[:400])
    old = cache.read_bytes() if cache.exists() else None
    if old is not None and not refresh:
        return parse_tennis_odds(old), "cache", False

    def keep(raw: bytes) -> bool:
        if raw != old:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(raw)
        return raw != old

    for url in C.TENNIS_ODDS_OFFICIAL:
        raw = http_get(url.format(year=year), retries=1)
        if raw is not None and ok(raw):
            df = parse_tennis_odds(raw)
            if not df.empty:
                return df, "tennis-data.co.uk", keep(raw)
    best, best_raw, src = pd.DataFrame(), None, "non disponibile"
    for url in C.TENNIS_ODDS_MIRRORS:  # tra i mirror vince il file piu' completo
        raw = http_get(url.format(year=year))
        if raw is not None and ok(raw):
            df = parse_tennis_odds(raw)
            if len(df) > len(best):
                best, best_raw, src = df, raw, "mirror " + url.split("/")[3]
    if old is not None:
        cached = parse_tennis_odds(old)
        if len(cached) >= len(best):
            return cached, "cache", False
    if best_raw is not None:
        return best, src, keep(best_raw)
    return best, src, False


def update_tennis(con, full: bool, progress) -> dict:
    years = C.tennis_years()
    current = set(years[-refresh_window()[1]:])
    have = {r[0] for r in con.execute("SELECT DISTINCT season FROM tennis_matches").fetchall()}
    stats = {"years": 0, "rows": 0, "odds_rows": 0, "matched": 0, "notes": [], "changed": False}
    is_csv = lambda b: b"winner_name" in b[:1500]
    raw_dir = C.RAW_DIR / "tennis"
    todo = [(y, full or y in current) for y in years
            if full or y in current or y not in have or not (raw_dir / f"tour_{y}.csv").exists()
            or not (raw_dir / f"chall_{y}.csv").exists()]

    def download(year: int, refresh: bool):
        return (fetch_best(raw_dir / f"tour_{year}.csv", [u.format(year=year) for u in C.TENNIS_TOUR_SOURCES], refresh, is_csv),
                fetch_best(raw_dir / f"chall_{year}.csv", [u.format(year=year) for u in C.TENNIS_CHALL_SOURCES], refresh, is_csv),
                _best_odds_file(year, refresh))

    fetched = parallel({y: (lambda y=y, r=r: download(y, r)) for y, r in todo}, workers=4)
    for i, (year, refresh) in enumerate(todo, 1):
        progress(f"tennis {year}", i, len(todo))
        (t_raw, t_src, t_new), (c_raw, c_src, c_new), (odds, o_src, o_new) = fetched[year]
        if not (t_new or c_new or o_new) and not full and year in have:
            continue  # nessun file cambiato
        tour = parse_sackmann(t_raw, "ATP") if t_raw else pd.DataFrame()
        chall = parse_sackmann(c_raw, "CHALLENGER") if c_raw else pd.DataFrame()
        df, info = build_tennis_year(year, tour, chall, odds)
        if df.empty:
            stats["notes"].append(f"{year}: nessun dato disponibile")
            continue
        replace_rows(con, "tennis_matches", df, "season = ?", [year])
        stats["changed"] = True
        stats["years"] += 1
        stats["rows"] += len(df)
        stats["odds_rows"] += info["odds"]
        stats["matched"] += info["matched"]
        stats["notes"].append(
            f"{year}: ATP {info['tour']} ({t_src.split('/')[3] if '/' in t_src else t_src}), "
            f"Challenger/quali {info['chall']} ({c_src.split('/')[3] if '/' in c_src else c_src}), "
            f"quote {info['odds']} ({o_src}), abbinate {info['matched']}"
        )
    if stats["changed"] or needs_feature_refresh(con):
        refresh_features(con, "tennis")
    return stats


def validate(con) -> list[str]:
    """Controlli di integrita' post-caricamento. Ritorna la lista dei problemi (vuota = ok)."""
    q = lambda s: con.execute(s).fetchone()[0]
    checks = {
        "calcio: match_id duplicati": "SELECT count(*) - count(DISTINCT match_id) FROM football_matches",
        "calcio: campi chiave nulli": "SELECT count(*) FROM football_matches WHERE match_date IS NULL OR home_team IS NULL OR away_team IS NULL OR ftr IS NULL",
        "calcio: risultato incoerente coi gol": "SELECT count(*) FROM football_matches WHERE ftr <> CASE WHEN fthg > ftag THEN 'H' WHEN fthg < ftag THEN 'A' ELSE 'D' END",
        "calcio: quote fuori range": "SELECT count(*) FROM football_matches WHERE avg_h <= 1 OR avg_d <= 1 OR avg_a <= 1 OR ps_ch <= 1",
        "calcio: quote exchange/Pinnacle incoerenti col mercato": "SELECT count(*) FROM football_matches WHERE abs(1/bfe_h - 1/avg_h) > 0.12 OR abs(1/bfe_o25 - 1/avg_o25) > 0.12 OR abs(1/ps_ch - 1/avg_ch) > 0.12",
        "tennis: quote incoerenti tra bookmaker": "SELECT count(*) FROM tennis_matches WHERE abs(1/avg_w - 1/ps_w) > 0.12 OR abs(1/max_w - 1/ps_w) > 0.12 OR abs(1/b365_l - 1/ps_l) > 0.12",
        "calcio: date fuori periodo": f"SELECT count(*) FROM football_matches WHERE match_date < DATE '{C.FIRST_YEAR}-06-01' OR match_date > current_date + 1",
        "tennis: match_id duplicati": "SELECT count(*) - count(DISTINCT match_id) FROM tennis_matches",
        "tennis: campi chiave nulli": "SELECT count(*) FROM tennis_matches WHERE match_date IS NULL OR winner_name IS NULL OR loser_name IS NULL",
        "tennis: quote fuori range": "SELECT count(*) FROM tennis_matches WHERE ps_w <= 1 OR ps_l <= 1 OR avg_w <= 1 OR avg_l <= 1",
        "tennis: ranking non positivi": "SELECT count(*) FROM tennis_matches WHERE winner_rank <= 0 OR loser_rank <= 0",
        "tennis: date fuori periodo": f"SELECT count(*) FROM tennis_matches WHERE match_date < DATE '{C.FIRST_YEAR - 1}-12-01' OR match_date > current_date + 1",
    }
    return [f"{name}: {q(sql)}" for name, sql in checks.items() if q(sql)]


def run(sport: str = "all", full: bool = False, db_path: Path | None = None) -> dict:
    """Aggiorna il database lavorando su una copia e sostituendo il file solo alla fine:
    l'app puo' continuare a leggere i dati mentre l'aggiornamento e' in corso, e un
    aggiornamento interrotto non lascia mai il database a meta'."""
    db_path = Path(db_path or C.DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    work = db_path.with_name(db_path.stem + ".aggiornamento.duckdb")
    for leftover in (work, Path(str(work) + ".wal")):
        if leftover.exists():
            leftover.unlink()
    if db_path.exists():
        shutil.copy2(db_path, work)

    def progress(label: str, i: int, n: int) -> None:
        log(f"PROGRESS {i}/{max(n, 1)} {label}")

    con = duckdb.connect(str(work))
    try:
        init_db(con)
        summary = {}
        if sport in ("all", "football"):
            summary["football"] = update_football(con, full, progress)
        if sport in ("all", "tennis"):
            summary["tennis"] = update_tennis(con, full, progress)
        if needs_feature_refresh(con):
            refresh_features(con)
        if sport in ("all", "tennis"):      # punti del Match Charting Project per lo scalping (riscaricati al massimo ogni 7 giorni)
            try:
                from src import scalping
                cached = C.RAW_DIR / "points" / scalping.FILES["p2020"]
                stale = (not cached.exists()) or (time.time() - cached.stat().st_mtime > 7 * 86400)
                progress("tennis punto per punto", 1, 1)
                summary["points"] = scalping.update_points(con, refresh=full or stale)
            except Exception as exc:  # una fonte secondaria non deve bloccare l'aggiornamento
                summary["points"] = {"games": 0, "matches": 0, "note": f"non aggiornati ({exc})"}
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        con.execute("INSERT OR REPLACE INTO meta VALUES ('last_update', ?)", [now])
        summary["changed"] = any(summary.get(k, {}).get("changed") for k in ("football", "tennis"))
        summary["problems"] = validate(con)
        summary["counts"] = {
            "football": con.execute("SELECT count(*) FROM football_matches").fetchone()[0],
            "tennis": dict(con.execute("SELECT circuit, count(*) FROM tennis_matches GROUP BY 1").fetchall()),
            "tennis_with_odds": con.execute("SELECT count(*) FROM tennis_matches WHERE has_odds").fetchone()[0],
        }
        con.execute("CHECKPOINT")
    finally:
        con.close()
    for attempt in range(20):  # su Windows il file puo' essere momentaneamente aperto in lettura dall'app
        try:
            os.replace(work, db_path)
            break
        except PermissionError:
            time.sleep(0.5)
    else:
        raise RuntimeError("impossibile sostituire il database: chiudi le altre finestre dell'app e riprova")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sport", choices=["all", "tennis", "football"], default="all")
    ap.add_argument("--full", action="store_true", help="riscarica tutto ignorando la cache")
    args = ap.parse_args()
    t0 = time.time()
    s = run(args.sport, args.full)
    log("")
    if "football" in s:
        f = s["football"]
        log(f"CALCIO  file aggiornati: {f['files']}, righe caricate: {f['rows']}, righe scartate: {f['dropped']}, "
            f"quote anomale annullate: {f['outliers']}")
        if f["missing"]:
            log(f"        non disponibili: {', '.join(f['missing'])}")
    if "tennis" in s:
        t = s["tennis"]
        log(f"TENNIS  anni aggiornati: {t['years']}, righe caricate: {t['rows']}, quote abbinate: {t['matched']}/{t['odds_rows']}")
        for n in t["notes"]:
            log("        " + n)
    if "points" in s:
        log(f"PUNTI   game di servizio: {s['points']['games']}, match: {s['points']['matches']} ({s['points']['note']})")
    log("NOVITA' " + ("nuovi dati caricati" if s["changed"] else "nessuna: i dati erano gia' aggiornati"))
    log(f"TOTALI  {s['counts']}")
    log("VALIDAZIONE " + ("OK" if not s["problems"] else "PROBLEMI: " + "; ".join(s["problems"])))
    log(f"Completato in {time.time() - t0:.0f}s - {date.today()}")
    return 1 if s["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
