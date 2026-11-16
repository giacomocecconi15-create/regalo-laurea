"""Gestionale: registro delle giocate reali, conti (bookmaker / exchange) e movimenti di cassa.

I dati stanno in `data/gestionale.duckdb`, separato dal database degli storici: aggiornare o
ricostruire gli storici non tocca mai il registro.

Convenzioni:
- `stake` e' sempre l'importo a rischio: la puntata per un back, la responsabilita' per un lay.
- esito 'won' / 'lost' riferito alla giocata (un lay e' vinto quando la selezione perde).
- P&L:  back vinto  = stake * (quota - 1) * (1 - commissione)
        lay vinto   = stake / (quota - 1) * (1 - commissione)
        perso       = -stake;   'void' (rimborsata) = 0;   'open' = non ancora chiusa.
- CLV%: back = quota / chiusura - 1;  lay = 1 - quota / chiusura.
- Saldo di un conto = depositi - prelievi + P&L delle giocate chiuse; il saldo disponibile toglie
  anche l'importo a rischio delle giocate aperte.
"""
from __future__ import annotations

import sys
from contextlib import closing
from datetime import date, datetime
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import config as C  # noqa: E402

LEDGER_PATH = C.DATA_DIR / "gestionale.duckdb"
RESULTS = ["open", "won", "lost", "void"]
BET_COLUMNS = ["id", "placed_date", "sport", "event", "selection", "side", "odds", "stake", "commission",
               "account", "strategy", "status", "close_odds", "pnl", "note", "created_at"]


def connect(path: Path | None = None) -> duckdb.DuckDBPyConnection:
    path = Path(path or LEDGER_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute("CREATE SEQUENCE IF NOT EXISTS bet_seq START 1")
    con.execute("CREATE SEQUENCE IF NOT EXISTS mov_seq START 1")
    con.execute("""CREATE TABLE IF NOT EXISTS accounts (
        name VARCHAR PRIMARY KEY, kind VARCHAR, created_at TIMESTAMP)""")
    con.execute("""CREATE TABLE IF NOT EXISTS movements (
        id BIGINT PRIMARY KEY, move_date DATE, account VARCHAR, kind VARCHAR, amount DOUBLE, note VARCHAR)""")
    con.execute("""CREATE TABLE IF NOT EXISTS bets (
        id BIGINT PRIMARY KEY, placed_date DATE, sport VARCHAR, event VARCHAR, selection VARCHAR, side VARCHAR,
        odds DOUBLE, stake DOUBLE, commission DOUBLE, account VARCHAR, strategy VARCHAR, status VARCHAR,
        close_odds DOUBLE, pnl DOUBLE, note VARCHAR, created_at TIMESTAMP)""")
    return con


# --------------------------------------------------------------------------- calcoli
def bet_pnl(side: str, odds: float, stake: float, status: str, commission: float = 0.0) -> float | None:
    """Profitto di una giocata chiusa (None se ancora aperta)."""
    if status == "open":
        return None
    if status == "void":
        return 0.0
    if status == "lost":
        return -float(stake)
    if status != "won":
        raise ValueError(f"esito non valido: {status}")
    gross = stake * (odds - 1) if side == "back" else stake / (odds - 1)
    return float(gross * (1 - commission))


def clv_pct(side: str, odds: float, close_odds: float | None) -> float:
    if close_odds is None or not np.isfinite(close_odds) or close_odds <= 1:
        return float("nan")
    return float((odds / close_odds - 1) * 100 if side == "back" else (1 - odds / close_odds) * 100)


def _check_bet(side: str, odds: float, stake: float, status: str) -> None:
    if side not in ("back", "lay"):
        raise ValueError("lato non valido: usare 'back' o 'lay'")
    if not (odds and odds > 1):
        raise ValueError("la quota deve essere maggiore di 1")
    if not (stake and stake > 0):
        raise ValueError("l'importo a rischio deve essere maggiore di 0")
    if status not in RESULTS:
        raise ValueError(f"esito non valido: {status}")


# --------------------------------------------------------------------------- conti e movimenti
def add_account(con, name: str, kind: str = "Bookmaker", opening_balance: float = 0.0, when: date | None = None) -> None:
    name = name.strip()
    if not name:
        raise ValueError("il nome del conto e' obbligatorio")
    if con.execute("SELECT 1 FROM accounts WHERE lower(name) = lower(?)", [name]).fetchone():
        raise ValueError(f"esiste gia' un conto chiamato '{name}'")
    con.execute("INSERT INTO accounts VALUES (?, ?, ?)", [name, kind, datetime.now()])
    if opening_balance and opening_balance > 0:
        add_movement(con, name, "deposit", opening_balance, when, "Saldo iniziale")


def add_movement(con, account: str, kind: str, amount: float, when: date | None = None, note: str = "") -> None:
    if kind not in ("deposit", "withdrawal"):
        raise ValueError("tipo di movimento non valido")
    if not (amount and amount > 0):
        raise ValueError("l'importo deve essere maggiore di 0")
    if not con.execute("SELECT 1 FROM accounts WHERE name = ?", [account]).fetchone():
        raise ValueError(f"conto inesistente: {account}")
    con.execute("INSERT INTO movements VALUES (nextval('mov_seq'), ?, ?, ?, ?, ?)",
                [when or date.today(), account, kind, float(amount), note or ""])


def delete_movement(con, move_id: int) -> None:
    con.execute("DELETE FROM movements WHERE id = ?", [int(move_id)])


def delete_account(con, name: str) -> None:
    """Elimina un conto solo se non ha giocate ne' movimenti."""
    used = con.execute("SELECT (SELECT count(*) FROM bets WHERE account = ?) + (SELECT count(*) FROM movements WHERE account = ?)",
                       [name, name]).fetchone()[0]
    if used:
        raise ValueError("il conto ha giocate o movimenti registrati: non puo' essere eliminato")
    con.execute("DELETE FROM accounts WHERE name = ?", [name])


def accounts(con) -> pd.DataFrame:
    """Saldo, esposizione aperta e disponibile per conto."""
    return con.execute("""
        SELECT a.name AS conto, a.kind AS tipo,
               coalesce(m.dep, 0) AS depositi, coalesce(m.wd, 0) AS prelievi,
               coalesce(b.pnl, 0) AS profitto, coalesce(b.n, 0) AS giocate,
               coalesce(m.dep, 0) - coalesce(m.wd, 0) + coalesce(b.pnl, 0) AS saldo,
               coalesce(b.open_risk, 0) AS in_gioco,
               coalesce(m.dep, 0) - coalesce(m.wd, 0) + coalesce(b.pnl, 0) - coalesce(b.open_risk, 0) AS disponibile
        FROM accounts a
        LEFT JOIN (SELECT account, sum(CASE WHEN kind = 'deposit' THEN amount ELSE 0 END) AS dep,
                          sum(CASE WHEN kind = 'withdrawal' THEN amount ELSE 0 END) AS wd
                   FROM movements GROUP BY 1) m ON m.account = a.name
        LEFT JOIN (SELECT account, sum(coalesce(pnl, 0)) AS pnl, count(*) AS n,
                          sum(CASE WHEN status = 'open' THEN stake ELSE 0 END) AS open_risk
                   FROM bets GROUP BY 1) b ON b.account = a.name
        ORDER BY a.name""").df()


def movements(con) -> pd.DataFrame:
    return con.execute("""SELECT id, move_date AS data, account AS conto,
                                 CASE kind WHEN 'deposit' THEN 'Deposito' ELSE 'Prelievo' END AS tipo,
                                 amount AS importo, note AS nota
                          FROM movements ORDER BY move_date DESC, id DESC""").df()


# --------------------------------------------------------------------------- giocate
def add_bet(con, *, placed_date: date, sport: str, event: str, selection: str, side: str, odds: float, stake: float,
            account: str, strategy: str = "", commission: float = 0.0, status: str = "open",
            close_odds: float | None = None, note: str = "") -> int:
    _check_bet(side, odds, stake, status)
    if not str(event).strip():
        raise ValueError("l'evento e' obbligatorio")
    if not con.execute("SELECT 1 FROM accounts WHERE name = ?", [account]).fetchone():
        raise ValueError("scegli un conto esistente (creane uno nella scheda Conti)")
    bet_id = con.execute("SELECT nextval('bet_seq')").fetchone()[0]
    con.execute("INSERT INTO bets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [
        bet_id, placed_date, sport, str(event).strip(), str(selection).strip(), side, float(odds), float(stake),
        float(commission or 0), account, (strategy or "").strip(), status,
        float(close_odds) if close_odds else None, bet_pnl(side, odds, stake, status, commission or 0),
        note or "", datetime.now()])
    return int(bet_id)


def settle_bet(con, bet_id: int, status: str, close_odds: float | None = None) -> None:
    """Chiude (o riapre con 'open') una giocata e ricalcola il profitto."""
    row = con.execute("SELECT side, odds, stake, commission, close_odds FROM bets WHERE id = ?", [int(bet_id)]).fetchone()
    if row is None:
        raise ValueError(f"giocata {bet_id} inesistente")
    side, odds, stake, commission, old_close = row
    _check_bet(side, odds, stake, status)
    close = float(close_odds) if close_odds else old_close
    con.execute("UPDATE bets SET status = ?, pnl = ?, close_odds = ? WHERE id = ?",
                [status, bet_pnl(side, odds, stake, status, commission or 0), close, int(bet_id)])


def delete_bet(con, bet_id: int) -> None:
    con.execute("DELETE FROM bets WHERE id = ?", [int(bet_id)])


def bets(con, status: str | None = None) -> pd.DataFrame:
    """Tutte le giocate (o solo quelle con un certo stato) con il CLV calcolato."""
    where = "WHERE status = ?" if status else ""
    df = con.execute(f"SELECT * FROM bets {where} ORDER BY placed_date, id", [status] if status else []).df()
    df["placed_date"] = pd.to_datetime(df["placed_date"])
    ratio = df["odds"] / df["close_odds"].where(df["close_odds"] > 1)
    df["clv_pct"] = np.where(df["side"] == "back", ratio - 1, 1 - ratio) * 100
    return df


def settled_trades(df: pd.DataFrame) -> pd.DataFrame:
    """Giocate chiuse nel formato del backtester (per riusare metriche e grafici). Le rimborsate sono escluse."""
    t = df[df["status"].isin(["won", "lost"])].copy()
    t = t.rename(columns={"placed_date": "date", "stake": "risk"})
    t["trade_won"] = t["status"] == "won"
    t["ev_close_pct"] = np.nan
    t = t.sort_values(["date", "id"], kind="stable").reset_index(drop=True)
    t["equity"] = t["pnl"].cumsum()
    return t


def by_strategy(df: pd.DataFrame, vault: dict | None = None) -> pd.DataFrame:
    """Risultato reale per strategia, affiancato al ROI e al CLV storici del backtest (se la strategia e' nel Vault)."""
    t = settled_trades(df)
    cols = ["strategia", "giocate", "aperte", "profitto", "rischiato", "roi_pct", "clv_pct", "roi_backtest_pct", "clv_backtest_pct"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    label = lambda s: s.fillna("").replace("", "(senza strategia)")
    opened = df[df["status"] == "open"].groupby(label(df["strategy"])).size()
    g = t.groupby(label(t["strategy"]))
    out = pd.DataFrame({"giocate": g.size(), "profitto": g["pnl"].sum(), "rischiato": g["risk"].sum(), "clv_pct": g["clv_pct"].mean()})
    out = out.reindex(out.index.union(opened.index))
    out["aperte"] = opened.reindex(out.index).fillna(0).astype(int)
    out["giocate"] = out["giocate"].fillna(0).astype(int)
    out[["profitto", "rischiato"]] = out[["profitto", "rischiato"]].fillna(0.0)
    out["roi_pct"] = (out["profitto"] / out["rischiato"] * 100).where(out["rischiato"] > 0)
    vault = vault or {}
    out["roi_backtest_pct"] = [vault.get(n, {}).get("metrics", {}).get("roi_pct") for n in out.index]
    out["clv_backtest_pct"] = [vault.get(n, {}).get("metrics", {}).get("clv_pct") for n in out.index]
    for c in ("roi_backtest_pct", "clv_backtest_pct"):
        out[c] = pd.to_numeric(out[c], errors="coerce")
    return out.rename_axis("strategia").reset_index()[cols]


def totals(con) -> dict:
    acc = accounts(con)
    return {"saldo": float(acc["saldo"].sum()) if len(acc) else 0.0,
            "in_gioco": float(acc["in_gioco"].sum()) if len(acc) else 0.0,
            "versato": float((acc["depositi"] - acc["prelievi"]).sum()) if len(acc) else 0.0,
            "profitto": float(acc["profitto"].sum()) if len(acc) else 0.0}


def add_signals(con, signals: pd.DataFrame, account: str, sport: str, strategies: dict | None = None,
                when: date | None = None) -> int:
    """Registra come giocate aperte i segnali dello scanner (salta quelli a stake 0)."""
    n = 0
    for r in signals.itertuples(index=False):
        if not (r.importo_rischio and r.importo_rischio > 0):
            continue
        cfg = (strategies or {}).get(r.strategia, {}).get("config", {})
        placed = pd.to_datetime(r.data, errors="coerce")
        add_bet(con, placed_date=(placed.date() if pd.notna(placed) else when or date.today()), sport=sport, event=r.evento,
                selection=str(r.selezione), side="lay" if r.lato == "BANCA" else "back", odds=float(r.quota),
                stake=float(r.importo_rischio), account=account, strategy=r.strategia,
                commission=float(cfg.get("commission") or 0.0), note="dallo scanner")
        n += 1
    return n


def open_ledger(path: Path | None = None):
    """Uso:  with open_ledger() as con: ..."""
    return closing(connect(path))
