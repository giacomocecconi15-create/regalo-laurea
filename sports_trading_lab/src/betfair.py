"""Quote reali in corso di partita dai dati storici di Betfair Exchange.

COME OTTENERLI (gratis nel livello BASIC, serve un account Betfair):
  historicdata.betfair.com -> "My Data" -> scegli sport (Tennis / Soccer), piano BASIC, periodo, mercato
  "Match Odds" -> scarica. Arriva un archivio .tar che contiene un file .bz2 per ogni mercato.
  Il piano BASIC riporta l'ultimo prezzo scambiato circa una volta al minuto.

FORMATO. Ogni file e' una sequenza di righe JSON ("mcm"): `marketDefinition` descrive mercato, partecipanti,
passaggio in-play ed esito finale; `rc` riporta i cambi dell'ultimo prezzo scambiato (`ltp`) per partecipante.

COSA SE NE RICAVA. Per ogni partecipante: quota all'ingresso (ultimo prezzo prima dell'inizio), percorso della
quota durante la partita ed esito. Su questi percorsi si simula il cash out con soglie di incasso e di taglio,
stavolta su prezzi veri e non stimati da un modello.

I dati importati stanno in `data/betfair.duckdb` (file dell'utente: non si riscarica da solo).
"""
from __future__ import annotations

import bz2
import io
import json
import sys
import tarfile
from contextlib import closing
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import config as C  # noqa: E402

DB_PATH = C.DATA_DIR / "betfair.duckdb"
SPORTS = {"1": "football", "2": "tennis"}
MAX_PRICE_POINTS = 2000


def connect(path: Path | None = None) -> duckdb.DuckDBPyConnection:
    path = Path(path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute("""CREATE TABLE IF NOT EXISTS bf_runners (
        market_id VARCHAR, runner_id BIGINT, sport VARCHAR, event VARCHAR, market_time TIMESTAMP, runner VARCHAR,
        won BOOLEAN, entry_odds DOUBLE, n_prices INTEGER, minutes DOUBLE, PRIMARY KEY (market_id, runner_id))""")
    con.execute("CREATE TABLE IF NOT EXISTS bf_prices (market_id VARCHAR, runner_id BIGINT, t_sec DOUBLE, ltp DOUBLE)")
    return con


# --------------------------------------------------------------------------- lettura dei file
def parse_market(lines) -> tuple[list[dict], list[tuple]] | None:
    """Legge il flusso di un mercato. Ritorna (partecipanti, prezzi in-play) oppure None se il mercato non e'
    un 'Match Odds' concluso e passato in-play."""
    definition, runners = None, {}
    inplay, start = False, None
    pre: dict = {}
    path: dict = {}
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        pt = msg.get("pt")
        for mc in msg.get("mc") or []:
            md = mc.get("marketDefinition")
            if md:
                definition = {**(definition or {}), **md, "id": mc.get("id")}
                for r in md.get("runners") or []:
                    runners[r.get("id")] = {**runners.get(r.get("id"), {}), **r}
                if md.get("inPlay") and not inplay:
                    inplay, start = True, pt
            for rc in mc.get("rc") or []:
                rid, ltp = rc.get("id"), rc.get("ltp")
                if rid is None or not isinstance(ltp, (int, float)) or ltp <= 1:
                    continue
                if inplay and start is not None and pt is not None:
                    path.setdefault(rid, []).append(((pt - start) / 1000.0, float(ltp)))
                else:
                    pre[rid] = float(ltp)
    if not definition or definition.get("marketType") != "MATCH_ODDS" or not inplay:
        return None
    sport = SPORTS.get(str(definition.get("eventTypeId")))
    statuses = {r.get("status") for r in runners.values()}
    if sport is None or "WINNER" not in statuses:
        return None
    out_r, out_p = [], []
    mid = str(definition.get("id"))
    when = pd.to_datetime(definition.get("marketTime"), errors="coerce", utc=True)
    for rid, r in runners.items():
        if r.get("status") not in ("WINNER", "LOSER") or rid not in pre:
            continue
        prices = path.get(rid, [])
        if len(prices) > MAX_PRICE_POINTS:                       # dati piu' fitti del necessario: si campiona
            idx = np.linspace(0, len(prices) - 1, MAX_PRICE_POINTS).astype(int)
            prices = [prices[i] for i in idx]
        out_r.append({"market_id": mid, "runner_id": int(rid), "sport": sport, "event": definition.get("eventName") or "",
                      "market_time": when.tz_localize(None) if pd.notna(when) else None, "runner": r.get("name") or str(rid),
                      "won": r.get("status") == "WINNER", "entry_odds": pre[rid], "n_prices": len(prices),
                      "minutes": prices[-1][0] / 60 if prices else 0.0})
        out_p += [(mid, int(rid), t, p) for t, p in prices]
    return (out_r, out_p) if out_r else None


def _open_text(data: bytes):
    """Righe di testo da un contenuto compresso bz2 oppure in chiaro."""
    if data[:3] == b"BZh":
        data = bz2.decompress(data)
    return io.StringIO(data.decode("utf-8", errors="replace"))


def iter_markets(path: str | Path):
    """Scorre file singoli, cartelle e archivi .tar (anche annidati) restituendo il contenuto di ogni mercato."""
    path = Path(path).expanduser()
    if path.is_dir():
        for child in sorted(path.rglob("*")):
            if child.is_file():
                yield from iter_markets(child)
        return
    if not path.is_file():
        return
    if tarfile.is_tarfile(path):
        with tarfile.open(path) as tar:
            for member in tar:
                if member.isfile():
                    handle = tar.extractfile(member)      # letto in memoria: nessun file viene estratto su disco
                    if handle is not None:
                        yield member.name, handle.read()
        return
    yield path.name, path.read_bytes()


def import_path(path: str | Path, con=None) -> dict:
    """Importa tutti i mercati trovati. I mercati gia' presenti vengono saltati."""
    own = con is None
    con = con or connect()
    stats = {"files": 0, "imported": 0, "skipped": 0, "ignored": 0}
    try:
        have = {r[0] for r in con.execute("SELECT DISTINCT market_id FROM bf_runners").fetchall()}
        for _, data in iter_markets(path):
            stats["files"] += 1
            try:
                parsed = parse_market(_open_text(data))
            except (OSError, ValueError, EOFError):
                parsed = None
            if parsed is None:
                stats["ignored"] += 1
                continue
            runners, prices = parsed
            if runners[0]["market_id"] in have:
                stats["skipped"] += 1
                continue
            have.add(runners[0]["market_id"])
            con.register("_r", pd.DataFrame(runners))
            con.execute("INSERT INTO bf_runners SELECT market_id, runner_id, sport, event, market_time, runner, won, entry_odds, n_prices, minutes FROM _r")
            con.unregister("_r")
            if prices:
                con.register("_p", pd.DataFrame(prices, columns=["market_id", "runner_id", "t_sec", "ltp"]))
                con.execute("INSERT INTO bf_prices SELECT * FROM _p")
                con.unregister("_p")
            stats["imported"] += 1
    finally:
        if own:
            con.close()
    return stats


def load(con=None) -> tuple[pd.DataFrame, pd.DataFrame]:
    own = con is None
    if own and not DB_PATH.exists():
        return pd.DataFrame(), pd.DataFrame()
    con = con or connect()
    try:
        runners = con.execute("SELECT * FROM bf_runners ORDER BY market_time, market_id, runner_id").df()
        prices = con.execute("SELECT * FROM bf_prices ORDER BY market_id, runner_id, t_sec").df()
    finally:
        if own:
            con.close()
    if len(runners):
        fav = runners.groupby("market_id")["entry_odds"].transform("min")
        runners["role"] = np.where(runners["runner"].str.lower().str.contains("draw"), "draw",
                                   np.where(runners["entry_odds"] == fav, "favourite", "underdog"))
    return runners, prices


# --------------------------------------------------------------------------- simulazione su prezzi reali
def simulate(runners: pd.DataFrame, prices: pd.DataFrame, side: str = "back", take_profit_pct: float | None = 30.0,
             stop_loss_pct: float | None = None, exit_minutes: float | None = None, spread_pct: float = 0.5,
             commission: float = 0.02, filters: dict | None = None) -> pd.DataFrame:
    """Entra all'ultimo prezzo pre-partita e segue il percorso reale della quota.
    Chiude alla prima rilevazione in cui il cash out vale almeno +take_profit_pct% del rischio o al massimo
    -stop_loss_pct%; oppure dopo `exit_minutes`; altrimenti tiene fino all'esito.
    Valore del cash out per unita' di rischio:  back = q_ingresso / q_attuale - 1
                                                lay  = (1 - q_ingresso / q_attuale) / (q_ingresso - 1)"""
    f = filters or {}
    r = runners
    if r.empty:
        return pd.DataFrame(columns=["date", "match_id", "selection", "odds", "risk", "pnl", "pnl_hold", "cashout", "equity"])
    if f.get("sport"):
        r = r[r["sport"].isin(f["sport"])]
    if f.get("role"):
        r = r[r["role"].isin(f["role"])]
    if f.get("odds_min") is not None:
        r = r[r["entry_odds"] >= f["odds_min"]]
    if f.get("odds_max") is not None:
        r = r[r["entry_odds"] <= f["odds_max"]]
    s = spread_pct / 100
    groups = {k: (g["t_sec"].to_numpy(), g["ltp"].to_numpy()) for k, g in prices.groupby(["market_id", "runner_id"], sort=False)}
    rows = []
    for x in r.itertuples():
        o = x.entry_odds * (1 - s) if side == "back" else x.entry_odds * (1 + s)      # ingresso un po' peggiore del prezzo visto
        if o <= 1.01:
            continue
        if side == "back":
            hold = (o - 1) * (1 - commission) if x.won else -1.0
        else:
            hold = -1.0 if x.won else (1 / (o - 1)) * (1 - commission)
        t, ltp = groups.get((x.market_id, x.runner_id), (np.array([]), np.array([])))
        pnl, reason, minute = hold, "", np.nan
        if len(t):
            if side == "back":
                value = o / (ltp * (1 + s)) - 1
            else:
                value = (1 - o / (ltp * (1 - s))) / (o - 1)
            value = np.maximum(np.where(value > 0, value * (1 - commission), value), -1.0)
            hit = np.zeros(len(t), dtype=bool)
            why = np.full(len(t), "", dtype=object)
            if take_profit_pct is not None:
                m = value >= take_profit_pct / 100
                why[m & ~hit] = "tp"
                hit |= m
            if stop_loss_pct is not None:
                m = value <= -stop_loss_pct / 100
                why[m & ~hit] = "sl"
                hit |= m
            if exit_minutes is not None:
                m = t >= exit_minutes * 60
                why[m & ~hit] = "time"
                hit |= m
            if hit.any():
                k = int(hit.argmax())
                pnl, reason, minute = float(value[k]), why[k], t[k] / 60
        rows.append({"date": x.market_time, "match_id": f"{x.market_id}|{x.runner_id}", "sport": x.sport, "event": x.event,
                     "selection": x.runner, "role": x.role, "side": side, "odds": x.entry_odds, "won": bool(x.won), "risk": 1.0,
                     "pnl_hold": hold, "pnl": pnl, "cashout": reason, "exit_minute": minute, "win_return": max(hold, 0.01),
                     "clv_pct": np.nan, "ev_close_pct": np.nan})
    t = pd.DataFrame(rows)
    if t.empty:
        return t
    t = t.sort_values(["date", "match_id"], kind="stable").reset_index(drop=True)
    t["trade_won"] = t["pnl"] > 0
    t["equity"] = t["pnl"].cumsum()
    return t


def threshold_grid(runners: pd.DataFrame, prices: pd.DataFrame, side: str = "back", spread_pct: float = 0.5,
                   commission: float = 0.02, filters: dict | None = None,
                   tps=(None, 10, 20, 30, 50, 75), sls=(None, 20, 30, 50, 70)) -> pd.DataFrame:
    """Rendimento per ogni combinazione di soglie di incasso e di taglio, sui prezzi reali."""
    rows = []
    for tp in tps:
        for sl in sls:
            t = simulate(runners, prices, side, tp, sl, None, spread_pct, commission, filters)
            if t.empty:
                continue
            eq = t["equity"].to_numpy()
            rows.append({"incassa_da": "mai" if tp is None else f"+{tp}%", "taglia_da": "mai" if sl is None else f"-{sl}%",
                         "operazioni": len(t), "chiuse_pct": (t["cashout"] != "").mean() * 100, "roi_pct": t["pnl"].mean() * 100,
                         "profit": t["pnl"].sum(), "max_dd": float((np.maximum.accumulate(np.maximum(eq, 0)) - eq).max())})
    return pd.DataFrame(rows)


def summary(con=None) -> dict:
    runners, _ = load(con)
    if runners.empty:
        return {"markets": 0, "runners": 0}
    return {"markets": int(runners["market_id"].nunique()), "runners": len(runners),
            "by_sport": runners.groupby("sport")["market_id"].nunique().to_dict(),
            "from": runners["market_time"].min(), "to": runners["market_time"].max(),
            "prices_per_market": float(runners["n_prices"].mean())}
