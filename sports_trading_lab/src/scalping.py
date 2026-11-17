"""Scalping sul servizio nel tennis: punti chi serve e chiudi dopo pochi punti.

DATI. Il Match Charting Project (Jeff Sackmann, pubblico) registra ogni punto di migliaia di match ATP:
chi serve e chi vince il punto. Da li' si ricostruisce ogni game di servizio come sequenza di punti.

QUOTE. Non esistono quote in diretta gratuite, quindi il prezzo a ogni punteggio e' calcolato con il modello
standard del tennis (punti indipendenti): la probabilita' di vincere il punto al servizio di ciascun giocatore
e' tarata in modo che la probabilita' di vincere il match a inizio partita coincida con la quota pre-match
(Pinnacle o media mercato; Elo se il match non ha quote). Da quelle due probabilita' il modello ricava game,
tie-break, set e match a qualunque punteggio.

OPERAZIONE. Si punta il giocatore al servizio (mercato vincente match) a un punteggio di ingresso e si chiude:
dopo N punti, oppure a fine game, con eventuale uscita anticipata alla prima palla break.
    rendimento per unita' puntata = quota_ingresso / quota_uscita - 1 = P_uscita / P_ingresso - 1
meno lo spread (un po' peggio sia in ingresso sia in uscita) e la commissione sui profitti.

LIMITE DA TENERE PRESENTE. Con prezzi di modello, l'esito dei punti e' reale ma le quote sono teoriche: il
backtest misura quanto i punti reali si discostano dall'ipotesi di punti indipendenti (per esempio se chi serve
recupera dallo 0-30 piu' o meno spesso del previsto), non la liquidita' ne' le reazioni del mercato vero.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import config as C  # noqa: E402
from src import update_data as ud  # noqa: E402

MCP = "https://raw.githubusercontent.com/JeffSackmann/tennis_MatchChartingProject/master/"
FILES = {"matches": "charting-m-matches.csv", "p2020": "charting-m-points-2020s.csv", "p2010": "charting-m-points-2010s.csv"}
ENTRY_SCORES = ["0-0", "15-0", "0-15", "15-15", "30-0", "0-30", "30-15", "15-30", "30-30", "40-15", "15-40", "40-30", "30-40", "40-40"]
PTS = {"0": 0, "15": 1, "30": 2, "40": 3}
MAXLEN = 30
GAME_COLUMNS = ["match_id", "match_date", "tournament", "round", "surface", "best_of", "server", "returner", "set_no", "game_no",
                "srv_games", "ret_games", "srv_sets", "ret_sets", "seq", "held", "p_srv", "v_hold", "v_break", "pre_prob", "price_source"]


# --------------------------------------------------------------------------- modello del tennis
def hold_prob(p):
    """Probabilita' che il battitore tenga il servizio da 0-0, vincendo ogni punto con probabilita' p."""
    p = np.asarray(p, dtype=float)
    q = 1 - p
    return p ** 4 * (1 + 4 * q + 10 * q ** 2) + 20 * p ** 3 * q ** 3 * p ** 2 / (p ** 2 + q ** 2)


def game_table(p) -> np.ndarray:
    """T[n, a, b] = probabilita' che il battitore vinca il game dal punteggio (a, b) in punti (0..3; 4 = vantaggio)."""
    p = np.asarray(p, dtype=float)
    q = 1 - p
    deuce = p ** 2 / (p ** 2 + q ** 2)
    t = np.zeros((len(p), 5, 5))
    t[:, 4, :3] = 1.0
    t[:, 4, 3] = p + q * deuce          # vantaggio battitore
    t[:, 3, 4] = p * deuce              # vantaggio risposta (palla break)
    t[:, 3, 3] = deuce
    for a in range(3, -1, -1):
        for b in range(3, -1, -1):
            if a == 3 and b == 3:
                continue
            t[:, a, b] = p * t[:, a + 1, b] + q * t[:, a, b + 1]
    return t


def tiebreak_prob(pa, pb):
    """Probabilita' che A vinca il tie-break (A serve il primo punto, poi due a testa)."""
    pa, pb = np.asarray(pa, dtype=float), np.asarray(pb, dtype=float)
    x, y = pa * (1 - pb), (1 - pa) * pb
    w = np.zeros((len(pa), 8, 8))
    w[:, 7, :6] = 1.0
    w[:, 6, 6] = x / (x + y)
    for total in range(11, -1, -1):
        for i in range(min(6, total), -1, -1):
            j = total - i
            if j > 6 or j < 0 or (i == 6 and j == 6):
                continue
            a_serves = ((total + 1) // 2) % 2 == 0
            win = pa if a_serves else 1 - pb
            w[:, i, j] = win * w[:, i + 1, j] + (1 - win) * w[:, i, j + 1]
    return w[:, 0, 0]


def set_table(pa, pb) -> np.ndarray:
    """W[n, gA, gB, s] = probabilita' che A vinca il set da (gA, gB) con A (s=0) o B (s=1) al servizio."""
    ha, hb = hold_prob(pa), hold_prob(pb)
    w = np.zeros((len(ha), 8, 8, 2))
    w[:, 6, :5, :] = 1.0
    w[:, 7, :, :] = 1.0
    w[:, 6, 6, :] = tiebreak_prob(pa, pb)[:, None]
    for total in range(11, -1, -1):
        for ga in range(min(6, total), -1, -1):
            gb = total - ga
            if gb > 6 or (ga == 6 and gb <= 4) or (gb == 6 and ga <= 4) or (ga == 6 and gb == 6):
                continue
            for s, win in ((0, ha), (1, 1 - hb)):
                w[:, ga, gb, s] = win * w[:, ga + 1, gb, 1 - s] + (1 - win) * w[:, ga, gb + 1, 1 - s]
    return w


def match_from_sets(set_win, best_of) -> np.ndarray:
    """M[n, sA, sB] = probabilita' che A vinca il match dal punteggio in set (all'inizio di un set)."""
    set_win = np.asarray(set_win, dtype=float)
    need = (np.asarray(best_of, dtype=int) + 1) // 2
    m = np.zeros((len(set_win), 4, 4))
    for sa in range(3, -1, -1):
        for sb in range(3, -1, -1):
            a_won, b_won = sa >= need, sb >= need
            nxt_a = m[:, min(sa + 1, 3), sb] if sa < 3 else np.ones(len(set_win))
            nxt_b = m[:, sa, min(sb + 1, 3)] if sb < 3 else np.zeros(len(set_win))
            val = set_win * np.where(sa + 1 >= need, 1.0, nxt_a) + (1 - set_win) * np.where(sb + 1 >= need, 0.0, nxt_b)
            m[:, sa, sb] = np.where(a_won, 1.0, np.where(b_won, 0.0, val))
    return m


def match_prob(pa, pb, best_of) -> np.ndarray:
    """Probabilita' che A vinca il match da 0-0."""
    w = set_table(pa, pb)
    return match_from_sets((w[:, 0, 0, 0] + w[:, 0, 0, 1]) / 2, best_of)[:, 0, 0]


def solve_serve_probs(pre_prob, base, best_of) -> tuple[np.ndarray, np.ndarray]:
    """Probabilita' di vincere il punto al servizio di A e di B (pA = base + d, pB = base - d) tali che il
    modello dia ad A la probabilita' di match `pre_prob`."""
    pre_prob, base, best_of = (np.asarray(x, dtype=float) for x in (pre_prob, base, best_of))
    d = np.zeros(len(pre_prob))
    grid = np.arange(-0.22, 0.2201, 0.004)
    for b in np.unique(np.round(base, 4)):
        for bo in (3, 5):
            m = (np.round(base, 4) == b) & (np.where(best_of == 5, 5, 3) == bo)
            if m.any():
                curve = match_prob(b + grid, b - grid, np.full(len(grid), bo))
                d[m] = np.interp(np.clip(pre_prob[m], curve[0], curve[-1]), curve, grid)
    return np.clip(base + d, 0.3, 0.92), np.clip(base - d, 0.3, 0.92)


# --------------------------------------------------------------------------- costruzione della tabella dei game
def _sequences(points: pd.DataFrame) -> pd.DataFrame:
    """Una riga per game di servizio (tie-break esclusi) con la sequenza dei punti ('1' = punto del battitore)."""
    p = points.dropna(subset=["Svr", "PtWinner"]).copy()
    for c in ("Pt", "Set1", "Set2", "Gm1", "Gm2", "Svr", "PtWinner"):
        p[c] = pd.to_numeric(p[c], errors="coerce")
    p = p.dropna(subset=["Pt", "Set1", "Set2", "Gm1", "Gm2", "Svr", "PtWinner"])
    p = p[p["Svr"].isin([1, 2]) & p["PtWinner"].isin([1, 2]) & ~((p["Gm1"] == 6) & (p["Gm2"] == 6)) & (p["Gm1"] <= 6) & (p["Gm2"] <= 6)]
    p = p.sort_values(["match_id", "Pt"], kind="stable")
    p["w"] = np.where(p["Svr"] == p["PtWinner"], "1", "0")
    g = p.groupby(["match_id", "Set1", "Set2", "Gm1", "Gm2"], sort=False)
    out = g.agg(seq=("w", "".join), server=("Svr", "first"), n_srv=("Svr", "nunique"), first_pt=("Pt", "first")).reset_index()
    out = out[(out["n_srv"] == 1) & (out["seq"].str.len() <= MAXLEN)]

    def result(seq: str):
        a = b = 0
        for i, ch in enumerate(seq):
            a, b = (a + 1, b) if ch == "1" else (a, b + 1)
            if (a >= 4 or b >= 4) and abs(a - b) >= 2:
                return (a > b) if i == len(seq) - 1 else None      # punti oltre la fine del game: registrazione errata
        return None                                                 # game non concluso

    out["held"] = out["seq"].map(result)
    return out[out["held"].notna()].drop(columns="n_srv")


def build_games(matches: pd.DataFrame, points: pd.DataFrame, db_matches: pd.DataFrame) -> pd.DataFrame:
    """Tabella dei game di servizio con i prezzi di modello dopo servizio tenuto e dopo break."""
    m = matches.rename(columns={"Player 1": "p1", "Player 2": "p2", "Best of": "best_of"}).copy()
    m["match_date"] = pd.to_datetime(m["Date"].astype(str).str.slice(0, 8), format="%Y%m%d", errors="coerce")
    m["surface"] = m["Surface"].astype(str).str.title()
    m["best_of"] = pd.to_numeric(m["best_of"], errors="coerce").fillna(3).where(lambda s: s.isin([3, 5]), 3)
    m = m[m["match_date"].notna() & m["surface"].isin(["Hard", "Clay", "Grass"]) & (m["match_date"] >= f"{C.FIRST_YEAR}-01-01")]
    m["k1"], m["k2"] = m["p1"].map(ud.norm_key), m["p2"].map(ud.norm_key)

    # probabilita' pre-match del giocatore 1: quote senza margine se il match e' nel database, altrimenti Elo
    d = db_matches.copy()
    d["kw"], d["kl"] = d["winner_name"].map(ud.norm_key), d["loser_name"].map(ud.norm_key)
    d["match_date"] = pd.to_datetime(d["match_date"])
    ow, ol = d["ps_w"].fillna(d["avg_w"]), d["ps_l"].fillna(d["avg_l"])
    d["pw"] = (1 / ow) / (1 / ow + 1 / ol)
    d["src"] = np.where(d["pw"].notna(), "quote", "elo")
    d["pw"] = d["pw"].fillna(1 / (1 + 10 ** ((d["loser_elo"] - d["winner_elo"]) / 400)))
    d = d[d["pw"].notna()]
    cand = pd.concat([
        m.merge(d, left_on=["k1", "k2"], right_on=["kw", "kl"], suffixes=("", "_db")).assign(p1_pre=lambda x: x["pw"]),
        m.merge(d, left_on=["k1", "k2"], right_on=["kl", "kw"], suffixes=("", "_db")).assign(p1_pre=lambda x: 1 - x["pw"]),
    ])
    cand["gap"] = (cand["match_date"] - cand["match_date_db"]).dt.days.abs()
    cand = cand[cand["gap"] <= 3].sort_values("gap").drop_duplicates("match_id")
    m = m.merge(cand[["match_id", "p1_pre", "src"]], on="match_id")
    if m.empty:
        return pd.DataFrame(columns=GAME_COLUMNS)

    g = _sequences(points[points["match_id"].isin(m["match_id"])])
    g = g.merge(m[["match_id", "match_date", "Tournament", "Round", "surface", "best_of", "p1", "p2", "p1_pre", "src"]], on="match_id")
    if g.empty:
        return pd.DataFrame(columns=GAME_COLUMNS)
    # livello medio del servizio per superficie, misurato sugli stessi punti
    won = g["seq"].str.count("1")
    base = (won.groupby(g["surface"]).sum() / g["seq"].str.len().groupby(g["surface"]).sum()).to_dict()
    m = m[m["match_id"].isin(g["match_id"])].reset_index(drop=True)
    # taratura: il livello di base viene corretto finche' la quota di servizi tenuti prevista dal modello coincide
    # con quella reale di ogni superficie (i punti non sono davvero indipendenti, e senza correzione il modello
    # sopravvaluterebbe chi serve in modo sistematico)
    real_hold = g.groupby("surface")["held"].mean()
    srv1_all = (g["server"] == 1).to_numpy()
    pos = pd.Series(np.arange(len(m)), index=m["match_id"])
    gi = g["match_id"].map(pos).to_numpy()
    for _ in range(4):
        pa, pb = solve_serve_probs(m["p1_pre"], m["surface"].map(base), m["best_of"])
        model_hold = pd.Series(hold_prob(np.where(srv1_all, pa[gi], pb[gi]))).groupby(g["surface"].to_numpy()).mean()
        for surf in base:
            base[surf] -= (model_hold[surf] - real_hold[surf]) / 1.7
    pa, pb = solve_serve_probs(m["p1_pre"], m["surface"].map(base), m["best_of"])
    w = set_table(pa, pb)
    mm = match_from_sets((w[:, 0, 0, 0] + w[:, 0, 0, 1]) / 2, m["best_of"])
    idx = pd.Series(np.arange(len(m)), index=m["match_id"])
    i = g["match_id"].map(idx).to_numpy()
    s1, s2 = g["Set1"].to_numpy(dtype=int).clip(0, 3), g["Set2"].to_numpy(dtype=int).clip(0, 3)
    g1, g2 = g["Gm1"].to_numpy(dtype=int), g["Gm2"].to_numpy(dtype=int)
    srv1 = g["server"].to_numpy() == 1
    need = ((m["best_of"].to_numpy(dtype=int) + 1) // 2)[i]

    def value(ga, gb, next_server):          # probabilita' che il giocatore 1 vinca il match dopo il game
        wset = w[i, ga, gb, next_server]
        up = np.where(s1 + 1 >= need, 1.0, mm[i, np.minimum(s1 + 1, 3), s2])
        down = np.where(s2 + 1 >= need, 0.0, mm[i, s1, np.minimum(s2 + 1, 3)])
        return wset * up + (1 - wset) * down

    nxt = np.where(srv1, 1, 0)               # dopo il game serve l'altro
    v_hold_1 = value(g1 + srv1, g2 + ~srv1, nxt)
    v_break_1 = value(g1 + ~srv1, g2 + srv1, nxt)
    out = pd.DataFrame({
        "match_id": g["match_id"], "match_date": g["match_date"], "tournament": g["Tournament"], "round": g["Round"],
        "surface": g["surface"], "best_of": g["best_of"], "server": np.where(srv1, g["p1"], g["p2"]),
        "returner": np.where(srv1, g["p2"], g["p1"]), "set_no": s1 + s2 + 1, "game_no": g1 + g2 + 1,
        "srv_games": np.where(srv1, g1, g2), "ret_games": np.where(srv1, g2, g1),
        "srv_sets": np.where(srv1, s1, s2), "ret_sets": np.where(srv1, s2, s1),
        "seq": g["seq"], "held": g["held"].astype(bool), "p_srv": np.where(srv1, pa[i], pb[i]),
        "v_hold": np.where(srv1, v_hold_1, 1 - v_hold_1), "v_break": np.where(srv1, v_break_1, 1 - v_break_1),
        "pre_prob": np.where(srv1, g["p1_pre"], 1 - g["p1_pre"]), "price_source": g["src"],
    })
    return out.sort_values(["match_date", "match_id", "set_no", "game_no"], kind="stable").reset_index(drop=True)[GAME_COLUMNS]


def update_points(con, refresh: bool = False) -> dict:
    """Scarica (o rilegge dalla cache) i punti e ricostruisce la tabella `tennis_service_games`."""
    raw, changed = {}, False
    for key, name in FILES.items():
        data, _, new = ud.fetch(C.RAW_DIR / "points" / name, [MCP + name], refresh, lambda b: b"match_id" in b[:200])
        if data is None:
            return {"games": 0, "matches": 0, "note": "dati punto per punto non raggiungibili"}
        raw[key], changed = data, changed or new
    have = "tennis_service_games" in {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    if have and not changed and not refresh:
        n = con.execute("SELECT count(*), count(DISTINCT match_id) FROM tennis_service_games").fetchone()
        return {"games": n[0], "matches": n[1], "note": "gia' aggiornati"}
    import io
    matches = pd.read_csv(io.BytesIO(raw["matches"]), dtype=str, encoding="utf-8", encoding_errors="replace", on_bad_lines="skip")
    cols = ["match_id", "Pt", "Set1", "Set2", "Gm1", "Gm2", "Svr", "PtWinner"]
    points = pd.concat([pd.read_csv(io.BytesIO(raw[k]), usecols=cols, dtype=str, encoding="utf-8", encoding_errors="replace",
                                    on_bad_lines="skip") for k in ("p2020", "p2010")], ignore_index=True)
    points = points[points["match_id"].str.slice(0, 4) >= str(C.FIRST_YEAR)]
    db = con.execute("""SELECT match_date, winner_name, loser_name, ps_w, ps_l, avg_w, avg_l, winner_elo, loser_elo
                        FROM tennis_matches""").df()
    games = build_games(matches, points, db)
    con.register("_games", games)
    con.execute("CREATE OR REPLACE TABLE tennis_service_games AS SELECT * FROM _games")
    con.unregister("_games")
    return {"games": len(games), "matches": int(games["match_id"].nunique()), "note": "ricostruiti"}


def load_games(con) -> pd.DataFrame:
    if "tennis_service_games" not in {r[0] for r in con.execute("SHOW TABLES").fetchall()}:
        return pd.DataFrame(columns=GAME_COLUMNS)
    g = con.execute("SELECT * FROM tennis_service_games").df()
    g["match_date"] = pd.to_datetime(g["match_date"])
    return g


# --------------------------------------------------------------------------- simulazione
def _matrix(seq: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    """(punti vinti dal battitore come matrice n x MAXLEN con -1 di riempimento, lunghezza di ogni game)."""
    n = len(seq)
    lens = seq.str.len().to_numpy()
    flat = np.frombuffer("".join(s.ljust(MAXLEN, "2") for s in seq).encode("ascii"), dtype=np.uint8).reshape(n, MAXLEN).astype(np.int16) - 48
    return np.where(flat == 2, -1, flat).astype(np.int8), lens


def _norm(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Riporta il punteggio alla tabella 0..4: oltre il 40-40 contano solo parita' e vantaggi."""
    deuce = (a >= 3) & (b >= 3)
    diff = a - b
    na = np.where(deuce, np.where(diff > 0, 4, 3), a)
    nb = np.where(deuce, np.where(diff < 0, 4, 3), b)
    return np.minimum(na, 4), np.minimum(nb, 4)


def simulate(games: pd.DataFrame, entry: str = "0-0", exit_points: int | None = 2, stop_at_bp: bool = False,
             spread_pct: float = 0.5, commission: float = 0.0, filters: dict | None = None) -> pd.DataFrame:
    """Simula lo scalping. Per ogni game che passa dal punteggio `entry` (battitore-risposta) si punta chi serve;
    si esce dopo `exit_points` punti (None = a fine game), o prima alla prima palla break se `stop_at_bp`.
    Ritorna le operazioni nel formato del backtester (rischio 1 unita' per operazione)."""
    f = filters or {}
    g = games
    if f.get("surface"):
        g = g[g["surface"].isin(f["surface"])]
    if f.get("set_no"):
        g = g[g["set_no"].isin(f["set_no"])]
    if f.get("price_source"):
        g = g[g["price_source"].isin(f["price_source"])]
    for col, key in (("pre_prob", "pre_prob"), ("game_no", "game_no"), ("p_srv", "p_srv")):
        lo, hi = f.get(key, (None, None))
        if lo is not None:
            g = g[g[col] >= lo]
        if hi is not None:
            g = g[g[col] <= hi]
    if f.get("date_from"):
        g = g[g["match_date"] >= pd.Timestamp(f["date_from"])]
    if g.empty:
        return pd.DataFrame(columns=["date", "match_id", "selection", "odds", "risk", "pnl", "equity"])
    g = g.reset_index(drop=True)
    pts, lens = _matrix(g["seq"])
    n = len(g)
    won = np.where(pts == 1, 1, 0)
    lost = np.where(pts == 0, 1, 0)
    a = np.concatenate([np.zeros((n, 1), int), np.cumsum(won, axis=1)], axis=1)     # punteggio PRIMA del punto k (colonna k)
    b = np.concatenate([np.zeros((n, 1), int), np.cumsum(lost, axis=1)], axis=1)
    ea, eb = (PTS[x] for x in entry.split("-"))
    pos = np.arange(MAXLEN + 1)[None, :]
    live = pos < lens[:, None]                                                      # punti ancora da giocare
    na, nb = _norm(a, b)
    at_entry = live & (na == ea) & (nb == eb)
    has = at_entry.any(axis=1)
    k_in = at_entry.argmax(axis=1)
    k_out = np.minimum(k_in + exit_points, lens) if exit_points else lens.copy()
    if stop_at_bp:                                                                  # palla break: risposta a un punto dal game
        bp = live & (nb >= 3) & (nb > na) & (pos > k_in[:, None])
        k_bp = np.where(bp.any(axis=1), bp.argmax(axis=1), MAXLEN + 1)
        stopped = k_bp < k_out
        k_out = np.minimum(k_out, k_bp)
    else:
        stopped = np.zeros(n, dtype=bool)
    rows = np.arange(n)
    table = _game_table_cached(g["p_srv"].to_numpy())
    vh, vb = g["v_hold"].to_numpy(), g["v_break"].to_numpy()

    def price(k):                                                                   # probabilita' match del battitore al punto k
        ended = k >= lens
        pg = np.where(ended, g["held"].to_numpy().astype(float), table[rows, na[rows, np.minimum(k, MAXLEN)], nb[rows, np.minimum(k, MAXLEN)]])
        return pg * vh + (1 - pg) * vb

    p_in, p_out = price(k_in), price(k_out)
    s = spread_pct / 100
    with np.errstate(divide="ignore", invalid="ignore"):
        gross = (p_out / p_in) * (1 - s) / (1 + s) - 1
        odds_in, odds_out = 1 / p_in, 1 / p_out
    pnl = np.where(gross > 0, gross * (1 - commission), gross)
    ok = has & (p_in > 0.02) & (p_in < 0.985) & np.isfinite(pnl)
    t = pd.DataFrame({
        "date": g["match_date"], "match_id": g["match_id"] + "|" + g["set_no"].astype(str) + "|" + g["game_no"].astype(str),
        "event": g["server"] + " (serve) vs " + g["returner"], "tournament": g["tournament"], "surface": g["surface"],
        "set_no": g["set_no"], "game_no": g["game_no"], "selection": "server", "odds": odds_in, "exit_odds": odds_out,
        "held": g["held"], "points": k_out - k_in, "exit": np.where(stopped, "palla break", np.where(k_out >= lens, "fine game", "dopo i punti")),
        "pre_prob": g["pre_prob"] * 100, "risk": 1.0, "pnl": pnl, "win_return": np.maximum(pnl, 0.01),
        "clv_pct": np.nan, "ev_close_pct": np.nan,
    })[ok].reset_index(drop=True)
    t["trade_won"] = t["pnl"] > 0
    t["equity"] = t["pnl"].cumsum()
    return t


_TABLE_CACHE: dict = {}


def _game_table_cached(p: np.ndarray) -> np.ndarray:
    key = (len(p), float(p[:50].sum()), float(p[-50:].sum()))
    if _TABLE_CACHE.get("key") != key:
        _TABLE_CACHE["key"], _TABLE_CACHE["t"] = key, game_table(p)
    return _TABLE_CACHE["t"]


def entry_grid(games: pd.DataFrame, exit_points: int | None = 2, stop_at_bp: bool = False, spread_pct: float = 0.5,
               commission: float = 0.0, filters: dict | None = None) -> pd.DataFrame:
    """Stessa regola di uscita provata da ogni punteggio di ingresso e su ogni superficie: dove rende e dove no."""
    rows = []
    for score in ENTRY_SCORES:
        t = simulate(games, score, exit_points, stop_at_bp, spread_pct, commission, filters)
        for surface in ["Tutte"] + sorted(t["surface"].unique()) if len(t) else []:
            x = t if surface == "Tutte" else t[t["surface"] == surface]
            if len(x) < 30:
                continue
            r = x["pnl"].to_numpy()
            sd = r.std(ddof=1)
            rows.append({"ingresso": score, "superficie": surface, "operazioni": len(x), "roi_pct": r.mean() * 100,
                         "vinte_pct": (r > 0).mean() * 100, "vincita_media_pct": r[r > 0].mean() * 100 if (r > 0).any() else np.nan,
                         "perdita_media_pct": r[r < 0].mean() * 100 if (r < 0).any() else np.nan,
                         "t_stat": r.mean() / sd * np.sqrt(len(r)) if sd > 0 else np.nan})
    return pd.DataFrame(rows)


def state_stats(games: pd.DataFrame) -> pd.DataFrame:
    """Per ogni punteggio di ingresso: quante volte il battitore tiene davvero il servizio contro quanto prevede
    il modello a punti indipendenti. Uno scarto positivo dice che da li' chi serve fa meglio del previsto."""
    if games.empty:
        return pd.DataFrame()
    g = games.reset_index(drop=True)
    pts, lens = _matrix(g["seq"])
    n = len(g)
    a = np.concatenate([np.zeros((n, 1), int), np.cumsum(pts == 1, axis=1)], axis=1)
    b = np.concatenate([np.zeros((n, 1), int), np.cumsum(pts == 0, axis=1)], axis=1)
    na, nb = _norm(a, b)
    live = np.arange(MAXLEN + 1)[None, :] < lens[:, None]
    table = game_table(g["p_srv"].to_numpy())
    held = g["held"].to_numpy().astype(float)
    rows = []
    for score in ENTRY_SCORES:
        ea, eb = (PTS[x] for x in score.split("-"))
        hit = (live & (na == ea) & (nb == eb)).any(axis=1)
        if hit.sum() < 30:
            continue
        for surface in ["Tutte"] + sorted(g["surface"].unique()):
            m = hit if surface == "Tutte" else hit & (g["surface"].to_numpy() == surface)
            if m.sum() < 30:
                continue
            rows.append({"punteggio": score, "superficie": surface, "game": int(m.sum()), "tenuti_pct": held[m].mean() * 100,
                         "modello_pct": table[m, ea, eb].mean() * 100})
    out = pd.DataFrame(rows)
    out["scarto"] = out["tenuti_pct"] - out["modello_pct"]
    return out
