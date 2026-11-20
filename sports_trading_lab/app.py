"""Sports Trading Lab - dashboard Streamlit.

Avvio:  streamlit run app.py   (oppure start_app.sh / start_app.bat)
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from contextlib import closing
from datetime import date
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from src import analysis as an  # noqa: E402
from src import backtester as bt  # noqa: E402
from src import backup as bk  # noqa: E402
from src import betfair as bf  # noqa: E402
from src import notify  # noqa: E402
from src import cashout as co  # noqa: E402
from src import config as C  # noqa: E402
from src import fixtures as fx  # noqa: E402
from src import ledger as lg  # noqa: E402
from src import risk  # noqa: E402
from src import roadmap as rm  # noqa: E402
from src import scalping as sc  # noqa: E402
from src import strategies as sv  # noqa: E402
from src import update_data as ud  # noqa: E402

# palette (tema scuro): superfici, inchiostri e colori di serie verificati per daltonismo
PAGE, SURFACE, RAISED, GRID = "#080b11", "#10151f", "#161d2a", "#222b3b"
INK, INK2, MUTED = "#eef1f6", "#bcc5d6", "#8791a6"
BLUE, ORANGE, RED = "#3987e5", "#d95926", "#e66767"
GOOD, WARN, BAD = "#0ca30c", "#fab219", "#d03b3b"
FONT = "Inter, -apple-system, 'Segoe UI', Roboto, sans-serif"

SPORT_LABEL = {"tennis": "Tennis", "football": "Calcio"}
MARKET_LABEL = {"h2h": "Testa a Testa", "1x2": "1X2", "ou25": "Over/Under 2.5"}
SEL_LABEL = {"favourite": "Favorito", "underdog": "Sfavorito", "home": "1 (Casa)", "draw": "X (Pareggio)",
             "away": "2 (Ospite)", "over": "Over 2.5", "under": "Under 2.5"}
MAX_TABLE_ROWS = 5000   # righe mostrate a schermo (l'esportazione CSV è sempre completa)
MAX_CHART_POINTS = 3000

# Esempi di partenza: mostrano come si combinano i filtri, non sono consigli di gioco.
PRESETS = {
    "Tennis - favoriti top 30 sulla terra": {
        "sport": "tennis", "market": "h2h", "selection": ["favourite"], "side": "back", "price": "max",
        "odds_min": 1.30, "odds_max": 1.80,
        "filters": {"surface": {"in": ["Clay"]}, "rank": {"min": 1, "max": 30}}},
    "Tennis - sfavoriti in forma": {
        "sport": "tennis", "market": "h2h", "selection": ["underdog"], "side": "back", "price": "max",
        "odds_min": 2.00, "odds_max": 3.50,
        "filters": {"form10": {"min": 60, "max": None}, "opp_form10": {"min": None, "max": 50}}},
    "Calcio - casa in forma contro ospite in crisi": {
        "sport": "football", "market": "1x2", "selection": ["home"], "side": "back", "price": "max",
        "odds_min": 1.50, "odds_max": 2.60,
        "filters": {"ppg_diff": {"min": 1.0, "max": None}, "home_pos": {"min": 1, "max": 8}}},
    "Calcio - Under 2.5 in partite da pochi gol": {
        "sport": "football", "market": "ou25", "selection": ["under"], "side": "back", "price": "max",
        "odds_min": 1.60, "odds_max": 2.40,
        "filters": {"goals5": {"min": None, "max": 2.2}}},
}

ICON = ROOT / "assets" / "icon.png"
st.set_page_config(page_title="Sports Trading Lab", page_icon=str(ICON) if ICON.exists() else ":material/monitoring:", layout="wide")
if ICON.exists():
    st.logo(str(ICON), size="large")
st.markdown(f"""
<style>
:root {{ --page:{PAGE}; --surface:{SURFACE}; --raised:{RAISED}; --line:{GRID}; --ink:{INK}; --ink2:{INK2}; --muted:{MUTED};
        --accent:{BLUE}; --good:{GOOD}; --bad:{BAD}; --warn:{WARN}; --r:14px; }}
.stApp * {{ transition:none !important; }}
html, body, .stApp {{ font-feature-settings:"tnum" 1, "ss01" 1; -webkit-font-smoothing:antialiased; }}
.stApp {{ background:radial-gradient(1100px 520px at 88% -8%, rgba(57,135,229,.13), transparent 62%),
                     radial-gradient(900px 480px at -6% 4%, rgba(25,158,112,.07), transparent 60%), var(--page); }}
.block-container {{ padding:5rem 2.8rem 4.5rem; max-width:1520px; }}

/* barra di navigazione in alto */
header[data-testid="stHeader"] {{ background:rgba(8,11,17,.94); border-bottom:1px solid var(--line); height:3.6rem; }}
[data-testid="stTopNavLink"] {{ border-radius:10px; padding:.42rem .8rem; color:var(--muted); font-weight:560; }}
[data-testid="stTopNavLink"]:hover {{ background:var(--raised); }}
[data-testid="stTopNavLink"][aria-current="page"] {{ background:linear-gradient(180deg, rgba(57,135,229,.24), rgba(57,135,229,.12));
    box-shadow:inset 0 0 0 1px rgba(57,135,229,.55); }}
[data-testid="stTopNavLink"][aria-current="page"] * {{ color:var(--ink) !important; }}

/* pannello laterale (solo backtester) */
section[data-testid="stSidebar"] {{ background:var(--surface); border-right:1px solid var(--line); }}
section[data-testid="stSidebar"] h3 {{ font-size:.68rem !important; letter-spacing:.14em; text-transform:uppercase; color:var(--accent);
    font-weight:680 !important; margin:1.2rem 0 .3rem !important; padding:1rem 0 0 !important; border-top:1px solid var(--line);
    border-left:0 !important; }}

/* testata di pagina */
.topline {{ display:flex; justify-content:space-between; align-items:center; margin:0 0 .55rem; }}
.brand {{ font-size:.66rem; letter-spacing:.2em; text-transform:uppercase; font-weight:700;
    background:linear-gradient(90deg, {BLUE}, #199e70); -webkit-background-clip:text; background-clip:text; color:transparent; }}
.chip {{ display:inline-flex; gap:8px; align-items:center; font-size:.74rem; color:var(--ink2); background:var(--surface);
    border:1px solid var(--line); border-radius:999px; padding:5px 12px; }}
h1 {{ font-size:2.05rem !important; font-weight:720 !important; letter-spacing:-.03em; padding:0 0 .1rem !important; line-height:1.15 !important; }}
h3 {{ font-size:.78rem !important; font-weight:680 !important; letter-spacing:.12em; text-transform:uppercase; color:var(--ink2);
    margin:1.7rem 0 .6rem !important; padding:.12rem 0 .12rem .7rem !important; border-left:3px solid var(--accent); line-height:1.2 !important; }}
[data-testid="stCaptionContainer"] {{ color:var(--muted); }}

/* schede dei numeri */
.kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(138px,1fr)); gap:14px; margin:.7rem 0 1.1rem; }}
.kpi {{ position:relative; overflow:hidden; background:linear-gradient(160deg, var(--raised) 0%, var(--surface) 70%);
    border:1px solid var(--line); border-radius:var(--r); padding:16px 18px 14px; box-shadow:0 1px 0 rgba(255,255,255,.03) inset, 0 8px 24px rgba(0,0,0,.28); }}
.kpi::before {{ content:""; position:absolute; inset:0 auto 0 0; width:3px; background:var(--tone, transparent); }}
.kpi.good {{ background:linear-gradient(160deg, rgba(12,163,12,.16) 0%, var(--surface) 62%); border-color:rgba(12,163,12,.38); }}
.kpi.bad {{ background:linear-gradient(160deg, rgba(208,59,59,.17) 0%, var(--surface) 62%); border-color:rgba(208,59,59,.40); }}
.kpi .l {{ color:var(--muted); font-size:.64rem; letter-spacing:.13em; text-transform:uppercase; font-weight:650; }}
.kpi .v {{ color:var(--ink); font-size:1.6rem; font-weight:700; letter-spacing:-.03em; line-height:1.4; white-space:nowrap; }}
.kpi .s {{ color:var(--muted); font-size:.74rem; line-height:1.35; }}
.kpi.hero {{ grid-column:span 2; }}
.kpi.hero .v {{ font-size:2.5rem; line-height:1.2; }}
.kpi svg.spark {{ position:absolute; right:0; bottom:0; width:52%; height:52%; opacity:.9; pointer-events:none; }}
@media (max-width:900px) {{ .kpi.hero {{ grid-column:span 1; }} }}

/* controlli, tabelle, grafici */
.check {{ display:flex; gap:12px; align-items:baseline; padding:11px 16px; border:1px solid var(--line); border-radius:12px; margin-bottom:7px;
    background:linear-gradient(160deg, var(--raised), var(--surface)); }}
.check b {{ color:var(--ink); font-weight:620; min-width:240px; display:inline-block; }}
.check span.d {{ color:var(--ink2); font-size:.86rem; }}
.dot {{ width:9px; height:9px; border-radius:50%; display:inline-block; flex:none; position:relative; top:-1px; box-shadow:0 0 0 3px rgba(255,255,255,.05); }}
.tag {{ font-size:.64rem; letter-spacing:.09em; text-transform:uppercase; color:var(--muted); min-width:86px; font-weight:650; }}
[data-testid="stPlotlyChart"] {{ border:1px solid var(--line); border-radius:var(--r); overflow:hidden; background:var(--surface);
    box-shadow:0 8px 24px rgba(0,0,0,.25); }}
[data-testid="stDataFrame"] {{ border:1px solid var(--line); border-radius:12px; overflow:hidden; }}
[data-testid="stExpander"] details {{ border-color:var(--line); border-radius:12px; background:var(--surface); }}
[data-testid="stVerticalBlockBorderWrapper"] {{ border-radius:var(--r); }}
div[data-testid="stAlert"] {{ border-radius:12px; }}
[data-testid="stButtonGroup"] button {{ border-radius:999px !important; padding:.34rem 1.05rem; font-weight:580; }}
[data-testid="stButtonGroup"] {{ gap:6px; }}
[data-testid="stBaseButton-primary"] {{ background:linear-gradient(180deg, #4a93ec, #2f77d2); border:0; font-weight:640; box-shadow:0 6px 18px rgba(57,135,229,.32); }}
[data-testid="stBaseButton-secondary"] {{ border-radius:10px; }}
hr {{ border-color:var(--line) !important; }}
</style>
""", unsafe_allow_html=True)
ss = st.session_state


# --------------------------------------------------------------------------- stato dei widget
def slider_bounds(spec: tuple) -> tuple:
    """Estremi e passo del cursore con un unico tipo numerico (Streamlit non accetta int e float mischiati)."""
    lo, hi, step = spec[4], spec[5], spec[6]
    cast = float if any(isinstance(v, float) for v in (lo, hi, step)) else int
    return cast(lo), cast(hi), cast(step), cast


def queue_cfg(cfg: dict) -> None:
    """Chiede di caricare una configurazione nel pannello del backtester al prossimo giro."""
    ss["_pending_cfg"] = cfg


ENTRY_OPTIONS = {
    "tennis": {"pre": "Pre-partita", "break_made": "Dopo il primo break fatto", "break_suffered": "Dopo il primo break subito",
               "set1_won": "Dopo il primo set vinto", "set1_lost": "Dopo il primo set perso", "press": "Quando va sotto pressione",
               "pressing": "Quando mette sotto pressione"},
    "1x2": {"pre": "Pre-partita", "ht_lead": "All'intervallo in vantaggio", "ht_draw": "All'intervallo sul pari",
            "ht_trail": "All'intervallo in svantaggio"},
    "ou25": {"pre": "Pre-partita", "ht_0": "All'intervallo con 0 gol", "ht_1": "All'intervallo con 1 gol", "ht_2": "All'intervallo con 2 gol"},
}
ENTRY_FILTERS = {"break_made": ("ip_break1", "Fatto"), "break_suffered": ("ip_break1", "Subito"), "set1_won": ("ip_set1", "Vinto"),
                 "set1_lost": ("ip_set1", "Perso"), "ht_lead": ("ip_ht_sel", "In vantaggio"), "ht_draw": ("ip_ht", "Pari"),
                 "ht_trail": ("ip_ht_sel", "In svantaggio"), "ht_0": ("ip_ht_goals", "0"), "ht_1": ("ip_ht_goals", "1"),
                 "ht_2": ("ip_ht_goals", "2"), "press": ("ip_press", "Sì"), "pressing": ("ip_pressing", "Sì")}


EVENT_OPTIONS = {
    "tennis": {"pre": "Pre-partita (entrambi i giocatori)", "break_made": "Su chi fa il primo break",
               "break_suffered": "Su chi subisce il primo break", "set1_won": "Su chi vince il primo set",
               "set1_lost": "Su chi perde il primo set", "press": "Su chi va sotto pressione",
               "pressing": "Su chi mette sotto pressione"},
    "1x2": {"pre": "Pre-partita (tutte le selezioni)", "ht_lead": "Sulla squadra in vantaggio all'intervallo",
            "ht_draw": "Sulle squadre in parità all'intervallo", "ht_trail": "Sulla squadra in svantaggio all'intervallo"},
}


def event_mode(sport: str, market: str, selection) -> bool:
    """Nessun lato fisso (né favorito né sfavorito, né solo casa o solo ospite): si opera su chi fa o subisce l'evento."""
    sel = set(selection or [])
    if sport == "tennis":
        return not sel or {"favourite", "underdog"} <= sel
    return market == "1x2" and (not sel or {"home", "away"} <= sel)


def entry_options(sport: str, market: str, selection=None) -> dict:
    key = "tennis" if sport == "tennis" else market
    if selection is not None and event_mode(sport, market, selection) and key in EVENT_OPTIONS:
        return EVENT_OPTIONS[key]
    return ENTRY_OPTIONS[key]


def entry_from_filters(sport: str, market: str, filters: dict) -> str:
    """Ricava il momento di ingresso dai filtri in partita di una strategia salvata."""
    if (filters.get("ip_ht_sel") or {}).get("in") == ["Pari"]:
        return "ht_draw"
    for key in entry_options(sport, market):
        if key in ENTRY_FILTERS:
            col, val = ENTRY_FILTERS[key]
            if (filters.get(col) or {}).get("in") == [val]:
                return key
    return "pre"


def apply_pending() -> None:
    cfg = ss.pop("_pending_cfg", None)
    if not cfg:
        return
    for k in [k for k in ss.keys() if k.startswith("bt_") and k not in ("bt_bank", "bt_view")]:
        del ss[k]
    sport = cfg.get("sport", "tennis")
    market = cfg.get("market") or bt.MARKETS[sport][0]
    ss["bt_sport"] = sport
    ss[f"bt_{sport}_market"] = market
    ss[f"bt_{sport}_{market}_sel"] = [s for s in (cfg.get("selection") or []) if s in bt.SELECTIONS[market]]
    ss["bt_side"] = cfg.get("side", "back")
    ss["bt_comm"] = float(cfg.get("commission") or 0.0) * 100
    ss["bt_laymode"] = cfg.get("lay_mode", "liability")
    ss[f"bt_{sport}_price"] = cfg.get("price", "avg") if cfg.get("price") in bt.PRICE_BOOKS[sport] else "avg"
    ss["bt_timing"] = cfg.get("price_timing", "open")
    ss["bt_omin"] = float(cfg.get("odds_min") or 1.01)
    ss["bt_omax"] = float(cfg.get("odds_max") or 50.0)
    ss["bt_excl"] = bool(cfg.get("exclude_retirements"))
    ss["bt_live_margin"] = float(cfg.get("live_margin", bt.LIVE_MARGIN))
    ss["bt_live_override_on"] = bool(cfg.get("live_odds_override"))
    if cfg.get("live_odds_override"):
        ss["bt_live_override"] = float(cfg["live_odds_override"])
    ss["bt_lay_spread"] = float(cfg.get("lay_spread", bt.LAY_SPREAD))
    c = co.settings(cfg)
    on = c["mode"] == "tpsl"
    ss["bt_co_method"] = c["method"] if on else "odds"
    for w in ("tp", "sl"):
        ss[f"bt_co_{w}_event"] = c[f"{w}_event"] if on else "off"
        ss[f"bt_co_{w}_quote"] = c[f"{w}_quote"]
        ss[f"_last_{w}_event"] = ss[f"bt_co_{w}_event"]        # la quota caricata vale per l'evento caricato
    for k in ("tp", "sl", "margin_pct", "spread_pct", "decided_pct"):
        ss[f"bt_co_{k}"] = float(c[k])
    ss["bt_co_who"], ss["bt_co_stop_at_bp"] = c["who"], bool(c["stop_at_bp"])
    ss["bt_live_min"], ss["bt_live_max"] = cfg.get("live_odds_min"), cfg.get("live_odds_max")
    ss["bt_excl_years"] = sorted(int(y) for y in cfg.get("exclude_years") or [])
    ss["bt_excl_matches"] = [str(m) for m in cfg.get("exclude_matches") or []]
    ss["bt_odds_adj"] = {str(k): float(v) for k, v in (cfg.get("odds_adjust") or {}).items()}
    ss[f"bt_entry_{sport}_{market}"] = entry_from_filters(sport, market, cfg.get("filters") or {})
    if c.get("legacy_manual"):
        ss["_legacy_note"] = ("Questa strategia era salvata con il vecchio cash out a frequenze, che non esiste più: il cash out è "
                              "ora a quote o a Take Profit / Stop Loss. Sceglilo nel riquadro *Piano di trading* e salva di nuovo la strategia.")
    specs = {f[0]: f for f in bt.FILTERS[sport]}
    for col, rule in (cfg.get("filters") or {}).items():
        spec = specs.get(col)
        if not spec or not rule:
            continue
        key = f"bt_{sport}_f_{col}"
        if spec[3] == "cat":
            ss[key] = list(rule.get("in") or [])
        elif spec[3] == "num":
            ss[key + "_min"], ss[key + "_max"] = rule.get("min"), rule.get("max")
        else:
            lo, hi, step, cast = slider_bounds(spec)
            a = lo if rule.get("min") is None else min(max(rule["min"], lo), hi)
            b = hi if rule.get("max") is None else min(max(rule["max"], lo), hi)
            a, b = round(a / step) * step, round(b / step) * step
            ss[key + "_sl"] = (cast(a), cast(b))


apply_pending()
for _k in list(ss.keys()):  # i widget del backtester mantengono lo stato quando si cambia pagina
    if _k.startswith("bt_"):
        ss[_k] = ss[_k]


# --------------------------------------------------------------------------- dati
def db_ready() -> bool:
    return C.DB_PATH.exists()


def db_version() -> float:
    return C.DB_PATH.stat().st_mtime if db_ready() else 0.0


def ensure_schema() -> None:
    """Se il database viene da una versione precedente, aggiunge le colonne nuove e ricalcola le variabili."""
    if not db_ready() or ss.get("_schema_ok") == db_version():
        return
    try:
        with closing(bt.connect()) as con:
            need = ud.needs_feature_refresh(con)
        if need:
            with st.spinner("Aggiorno il database alla nuova versione (pochi secondi, solo la prima volta)..."):
                with closing(duckdb.connect(str(C.DB_PATH))) as con:
                    ud.init_db(con)
                    ud.refresh_features(con)
                    sc.ensure_events(con)     # collega i match punto per punto al database (solo dalla cache locale)
            st.cache_resource.clear()
            st.cache_data.clear()
    except duckdb.Error as exc:
        st.error(f"Impossibile aprire il database: {exc}. Chiudi altre finestre dell'app e ricarica la pagina.")
        st.stop()
    ss["_schema_ok"] = db_version()


@st.cache_resource(show_spinner="Carico i match dal database...", max_entries=3)
def selections(sport: str, market: str, price: str, timing: str, version: float) -> pd.DataFrame:
    with closing(bt.connect()) as con:
        return bt.load_selections(sport, market, price, timing, con)


@st.cache_data(show_spinner=False)
def db_status(version: float) -> dict:
    with closing(bt.connect()) as con:
        q = lambda s: con.execute(s).df()
        return {
            "tennis": q("""SELECT circuit, count(*) AS match, sum(has_odds::int) AS con_quote,
                                  min(match_date) AS dal, max(match_date) AS al
                           FROM tennis_matches GROUP BY 1 ORDER BY 1"""),
            "tennis_year": q("""SELECT season AS anno, circuit, count(*) AS n FROM tennis_matches GROUP BY 1, 2 ORDER BY 1"""),
            "football": q("""SELECT country AS paese, league AS lega, count(*) AS match, count(DISTINCT season) AS stagioni,
                                    min(match_date) AS dal, max(match_date) AS al,
                                    round(avg((ps_ch IS NOT NULL OR bfe_ch IS NOT NULL)::int) * 100, 1) AS "chiusura_Pinnacle/Exchange_%"
                             FROM football_matches GROUP BY 1, 2 ORDER BY 1, 2"""),
            "last": (con.execute("SELECT value FROM meta WHERE key = 'last_update'").fetchone() or ["mai"])[0],
        }


UPDATE_LOG = C.DATA_DIR / "update.log"


@st.cache_resource
def _updater() -> dict:
    """Stato condiviso dell'aggiornamento in background (uno solo per volta, per tutta l'app)."""
    return {"proc": None, "handled": True, "code": None, "auto_checked_at": 0.0}


def update_running() -> bool:
    proc = _updater()["proc"]
    return proc is not None and proc.poll() is None


def start_update(full: bool = False) -> bool:
    """Avvia l'aggiornamento senza bloccare l'app: lavora su una copia del database e la sostituisce alla fine."""
    up = _updater()
    if update_running():
        return False
    try:
        C.DATA_DIR.mkdir(parents=True, exist_ok=True)
        out = open(UPDATE_LOG, "w", encoding="utf-8")
        cmd = [sys.executable, str(ROOT / "src" / "update_data.py")] + (["--full"] if full else [])
        up["proc"] = subprocess.Popen(cmd, cwd=ROOT, stdout=out, stderr=subprocess.STDOUT)
        up["handled"], up["code"] = False, None
        return True
    except OSError:
        return False


def update_progress() -> tuple[float, str, list[str]]:
    """(avanzamento 0-1, etichetta, ultime righe del log)."""
    try:
        lines = UPDATE_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return 0.0, "", []
    frac, label = 0.0, "avvio..."
    for line in reversed(lines):
        if line.startswith("PROGRESS "):
            try:
                _, part, label = line.split(" ", 2)
                i, n = (int(x) for x in part.split("/"))
                frac = min(i / max(n, 1), 1.0)
            except ValueError:
                pass
            break
    return frac, label, [x for x in lines if x and not x.startswith("PROGRESS ")][-25:]


def collect_update() -> None:
    """Se un aggiornamento e' appena terminato, svuota le cache cosi' l'app legge i dati nuovi."""
    up = _updater()
    proc = up["proc"]
    if proc is None or up["handled"] or proc.poll() is None:
        return
    up["handled"], up["code"] = True, proc.returncode
    selections.clear()
    db_status.clear()
    ss.pop("_schema_ok", None)
    ss.pop("_bt_key", None)
    if proc.returncode == 0:
        st.toast("Dati aggiornati.", icon=":material/check_circle:")
    else:
        st.toast("Aggiornamento dati non riuscito: dettagli nella pagina Dati.", icon=":material/error:")


def last_update_time() -> pd.Timestamp | None:
    if not db_ready():
        return None
    try:
        raw = db_status(db_version())["last"]
        return pd.to_datetime(raw.replace(" UTC", ""), errors="coerce", utc=True)
    except Exception:
        return None


def auto_update() -> None:
    """All'avvio (e poi al massimo una volta l'ora) aggiorna i dati se sono piu' vecchi della soglia scelta."""
    up = _updater()
    if time.time() - up["auto_checked_at"] < 3600 or update_running():
        return
    up["auto_checked_at"] = time.time()
    cfg = fx.load_settings()
    if not cfg.get("auto_update") or not db_ready():
        return
    last = last_update_time()
    age_h = (pd.Timestamp.now(tz="UTC") - last).total_seconds() / 3600 if last is not None and pd.notna(last) else 1e9
    if age_h >= float(cfg.get("auto_update_hours") or 12):
        start_update()


def data_badge() -> None:
    """Riga in cima a ogni pagina: nome dell'app e stato dei dati; durante un aggiornamento si ricarica da sola."""
    def body() -> None:
        up = _updater()
        if update_running():
            frac, _, _ = update_progress()
            chip = f'<span class="dot" style="background:{BLUE}"></span>Aggiornamento dati in corso: {frac * 100:.0f}%'
        else:
            if up["proc"] is not None and not up["handled"]:
                st.rerun()
            last = last_update_time()
            when = last.tz_convert("Europe/Rome").strftime("%d/%m %H:%M") if last is not None and pd.notna(last) else "mai"
            chip = f'<span class="dot" style="background:{GOOD if when != "mai" else MUTED}"></span>Dati aggiornati: {when}'
        st.markdown(f'<div class="topline"><span class="brand">Sports Trading Lab</span><span class="chip status">{chip}</span></div>',
                    unsafe_allow_html=True)

    st.fragment(body, run_every=3 if update_running() else None)()


def need_db() -> bool:
    if not db_ready():
        st.warning("Database non trovato. Apri **Dati** e premi *Aggiorna Database*.")
        return True
    return False


# --------------------------------------------------------------------------- componenti
def header(title: str, subtitle: str | None = None) -> None:
    st.title(title)
    if subtitle:
        st.caption(subtitle)


def spark(values) -> str:
    """Mini-grafico (SVG leggero, senza librerie) da mettere sullo sfondo di una scheda."""
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    if len(v) < 3:
        return ""
    if len(v) > 80:
        v = v[np.linspace(0, len(v) - 1, 80).astype(int)]
    lo, hi = float(v.min()), float(v.max())
    span = (hi - lo) or 1.0
    pts = " ".join(f"{i / (len(v) - 1) * 100:.1f},{36 - (x - lo) / span * 32:.1f}" for i, x in enumerate(v))
    return (f'<svg class="spark" viewBox="0 0 100 40" preserveAspectRatio="none"><polygon points="0,40 {pts} 100,40" fill="{BLUE}" '
            f'fill-opacity=".16"/><polyline points="{pts}" fill="none" stroke="{BLUE}" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>')


def kpis(items: list[tuple], hero_spark=None) -> None:
    """items: (etichetta, valore, nota, tono) con tono 'good' | 'bad' | None. Con `hero_spark` (serie di
    valori) la prima scheda diventa grande e mostra l'andamento sullo sfondo."""
    tones = {"good": GOOD, "bad": BAD}
    cells = []
    for k, (label, value, note, tone) in enumerate(items):
        hero = k == 0 and hero_spark is not None
        cells.append(f'<div class="kpi {tone or ""}{" hero" if hero else ""}" style="--tone:{tones.get(tone, "transparent")}">'
                     f'<div class="l">{label}</div><div class="v">{value}</div><div class="s">{note or "&nbsp;"}</div>'
                     f'{spark(hero_spark) if hero else ""}</div>')
    st.markdown(f'<div class="kpis">{"".join(cells)}</div>', unsafe_allow_html=True)


def sign_tone(v: float) -> str | None:
    return None if v is None or not np.isfinite(v) or v == 0 else ("good" if v > 0 else "bad")


def num_it(v: float, dec: int = 0) -> str:
    return f"{v:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def signed_it(v: float, dec: int = 2) -> str:
    return ("+" if v > 0 else "") + num_it(v, dec)


def fmt(v: float, pattern: str = "{:+.2f}") -> str:
    return "n/d" if v is None or (isinstance(v, float) and not np.isfinite(v)) else pattern.format(v)


def checklist(checks: list[tuple[str, str, str]]) -> None:
    color = {"ok": GOOD, "warn": WARN, "bad": BAD}
    word = {"ok": "Superato", "warn": "Incerto", "bad": "Non superato"}
    html = "".join(
        f'<div class="check"><span class="dot" style="background:{color[s]}"></span><span class="tag">{word[s]}</span>'
        f'<b>{name}</b><span class="d">{detail}</span></div>' for name, s, detail in checks)
    st.markdown(html, unsafe_allow_html=True)


# --------------------------------------------------------------------------- grafici
def base_layout(fig: go.Figure, height: int, title: str | None = None) -> go.Figure:
    fig.update_layout(
        height=height, paper_bgcolor=SURFACE, plot_bgcolor=SURFACE, font=dict(color=INK, size=12, family=FONT),
        margin=dict(l=58, r=22, t=52 if title else 18, b=40), hovermode="x unified",
        title=dict(text=title, font=dict(size=13, color=INK2), x=0.012, y=0.965, yanchor="top") if title else None,
        hoverlabel=dict(bgcolor=RAISED, bordercolor=GRID, font=dict(color=INK, family=FONT)), showlegend=False,
    )
    fig.update_xaxes(gridcolor="rgba(0,0,0,0)", linecolor=GRID, zeroline=False, tickfont=dict(color=MUTED, size=11), ticks="outside",
                     tickcolor=GRID, ticklen=4)
    fig.update_yaxes(gridcolor=GRID, griddash="dot", linecolor="rgba(0,0,0,0)", zerolinecolor=MUTED, zerolinewidth=1,
                     tickfont=dict(color=MUTED, size=11))
    return fig


def thin(trades: pd.DataFrame) -> pd.DataFrame:
    """Riduce i punti disegnati (il grafico resta identico a occhio, il browser resta leggero)."""
    n = len(trades)
    if n <= MAX_CHART_POINTS:
        return trades.assign(_n=np.arange(1, n + 1))
    idx = np.unique(np.append(np.arange(0, n, int(np.ceil(n / MAX_CHART_POINTS))), n - 1))
    return trades.iloc[idx].assign(_n=idx + 1)


def equity_chart(trades: pd.DataFrame, cut: pd.Timestamp | None = None, by_trade: bool = False) -> go.Figure:
    """Curva del profitto. `by_trade`: asse orizzontale = numero di operazione (utile quando molte operazioni
    cadono nello stesso giorno e la curva per data si schiaccerebbe)."""
    t = thin(trades)
    fig = go.Figure(go.Scatter(
        x=t["_n"] if by_trade else t["date"], y=t["equity"], mode="lines", line=dict(color=BLUE, width=2, shape="hv"),
        fill="tozeroy", fillcolor="rgba(57,135,229,0.10)",
        customdata=t["_n"], hovertemplate="%{y:+.2f} u  (operazione n. %{customdata})<extra></extra>"))
    base_layout(fig, 360, "Equity curve - profitto cumulato (unità)")
    if cut is not None:
        fig.add_vline(x=cut, line=dict(color=MUTED, width=1, dash="dot"))
        fig.add_annotation(x=cut, y=1, yref="paper", text="inizio verifica", showarrow=False, xanchor="left",
                           font=dict(color=INK2, size=11), xshift=6)
    fig.update_yaxes(ticksuffix=" u")
    return fig


def drawdown_chart(trades: pd.DataFrame) -> go.Figure:
    eq = trades["equity"].to_numpy()
    t = thin(trades.assign(_dd=eq - np.maximum.accumulate(np.maximum(eq, 0))))
    fig = go.Figure(go.Scatter(x=t["date"], y=t["_dd"], mode="lines", line=dict(color=RED, width=1.5, shape="hv"),
                               fill="tozeroy", fillcolor="rgba(230,103,103,0.18)", hovertemplate="%{y:.2f} u<extra></extra>"))
    base_layout(fig, 210, "Drawdown dal massimo (unità)")
    fig.update_yaxes(ticksuffix=" u")
    return fig


def bars_chart(x, y, hover: list[str], title: str, suffix: str, height: int = 290) -> go.Figure:
    y = np.asarray(y, dtype=float)
    fig = go.Figure(go.Bar(
        x=x, y=y, marker=dict(color=np.where(y >= 0, BLUE, RED), line=dict(color=SURFACE, width=1)),
        customdata=hover, hovertemplate="%{y:+.2f}" + suffix + "<br>%{customdata}<extra></extra>"))
    base_layout(fig, height, title + " - blu positivo, rosso negativo")
    fig.update_xaxes(type="category", tickangle=-30, automargin=True)
    fig.update_yaxes(ticksuffix=suffix)
    fig.update_layout(bargap=0.25)
    return fig


def _rgba(hex_color: str, a: float) -> str:
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{a})"


def fan_chart(results: list[tuple[str, dict, str]]) -> go.Figure:
    fig = make_subplots(rows=1, cols=len(results), subplot_titles=[r[0] for r in results], horizontal_spacing=0.08)
    for i, (_, res, color) in enumerate(results, start=1):
        b = res["bands"]
        for lo, hi, alpha in (("p5", "p95", 0.15), ("p25", "p75", 0.30)):
            fig.add_trace(go.Scatter(x=b["trade"], y=b[hi], mode="lines", line=dict(width=0), hoverinfo="skip"), 1, i)
            fig.add_trace(go.Scatter(x=b["trade"], y=b[lo], mode="lines", line=dict(width=0), fill="tonexty",
                                     fillcolor=_rgba(color, alpha), hoverinfo="skip"), 1, i)
        fig.add_trace(go.Scatter(
            x=b["trade"], y=b["p50"], mode="lines", line=dict(color=color, width=2),
            customdata=np.stack([b["p5"], b["p95"]], axis=1),
            hovertemplate="mediana %{y:,.0f}<br>5°-95°: %{customdata[0]:,.0f} - %{customdata[1]:,.0f}<extra></extra>"), 1, i)
        fig.add_hline(y=res["bankroll"], line=dict(color=MUTED, width=1, dash="dot"), row=1, col=i)
    base_layout(fig, 370)
    fig.update_layout(margin=dict(t=48))
    fig.update_xaxes(title_text="operazioni future", title_font=dict(color=MUTED, size=11))
    return fig


def dd_hist_chart(results: list[tuple[str, dict, str]]) -> go.Figure:
    fig = make_subplots(rows=1, cols=len(results), subplot_titles=[r[0] for r in results], horizontal_spacing=0.08)
    top = max(max(float(np.percentile(r[1]["max_dd_pct"], 99.5)) for r in results), 5.0)
    for i, (_, res, color) in enumerate(results, start=1):
        fig.add_trace(go.Histogram(
            x=res["max_dd_pct"], xbins=dict(start=0, end=top, size=top / 40),
            marker=dict(color=color, line=dict(color=SURFACE, width=1)),
            hovertemplate="drawdown %{x}%<br>%{y} simulazioni<extra></extra>"), 1, i)
        fig.add_vline(x=res["p95_dd_pct"], line=dict(color=INK, width=1, dash="dot"), row=1, col=i,
                      annotation_text=f"95° pct: {res['p95_dd_pct']:.1f}%", annotation_font=dict(color=INK, size=11))
    base_layout(fig, 310)
    fig.update_layout(margin=dict(t=48), hovermode="closest", bargap=0.05)
    fig.update_xaxes(title_text="max drawdown del percorso (%)", title_font=dict(color=MUTED, size=11), ticksuffix="%")
    return fig


def show(fig: go.Figure) -> None:
    st.plotly_chart(fig, width="stretch", theme=None, config={"displayModeBar": False})


# --------------------------------------------------------------------------- pagina: guida
def page_guide() -> None:
    header("Guida rapida", "Come si usa la piattaforma, in cinque passi.")
    a, b = st.columns(2)
    with a:
        st.markdown("""
### 1 · Scegli cosa puntare
Nella pagina **Backtester**, nel pannello a sinistra: sport, mercato, selezione (es. *Favorito*, *1*, *Over 2.5*),
se **punti** (Back) o **banchi** (Lay) e a quale quota entri (media mercato, massimo mercato, Pinnacle...).
Per partire in fretta scegli un **esempio pronto** in cima al pannello.

### 2 · Aggiungi i filtri
I filtri sono raggruppati: torneo o lega, calendario, giocatore/squadra, avversario, confronto.
Ogni cursore lasciato da un estremo all'altro **non filtra**. Sposta solo quelli che ti interessano:
i risultati si aggiornano da soli. *Azzera filtri* riporta tutto al punto di partenza.

### 3 · Leggi il risultato
- **ROI / Yield**: profitto diviso per il totale rischiato. Ogni operazione rischia 1 unità.
- **CLV**: di quanto la tua quota batte la quota di chiusura. Se è positivo con costanza, è il segnale più
  affidabile che la strategia abbia un vantaggio reale.
- **Max Drawdown**: la peggiore discesa dal massimo. Ti dice quanta banca serve per reggerla.
""")
    with b:
        st.markdown("""
### 4 · Verifica che non sia fortuna
- Vista **Analisi**: il risultato scomposto per anno, fascia di quota, torneo, superficie... e l'elenco automatico
  dei segmenti migliori e peggiori, che puoi aggiungere ai filtri con un clic.
- Vista **Robustezza**: sei controlli con esito *Superato / Incerto / Non superato* (campione, significatività,
  intervallo di confidenza, CLV, tenuta sulle operazioni più recenti, costanza negli anni).

Più filtri provi, più è facile trovare per caso una combinazione che ha reso bene in passato. Fidati di una
strategia solo se supera i controlli e ha una logica che sai spiegare.

### 5 · Dal test alla pratica
- **Quando entri** (riquadro *Piano di trading*, in alto nel Backtester): pre-partita oppure dopo il primo break fatto o
  subito, dopo il primo set vinto o perso, quando il giocatore va *sotto pressione* (due turni di servizio tenuti con
  palle break o ai vantaggi), all'intervallo. Le quote scelte restano pre-partita, ma l'operazione entra dopo l'evento alla
  quota live stimata (tarata sui risultati reali; si può filtrare anche per quota live). Se nella *Selezione* non scegli né
  favorito né sfavorito, operi su chi fa o subisce l'evento (es. chi subisce il primo break).
- **Uscita**: due regole separate. *Take Profit* = quando esci in positivo (fa il primo break, fa il controbreak, fa il
  prossimo break, vince il primo o il secondo set); *Stop Loss* = quando esci in negativo (subisce il primo break, il
  controbreak, il prossimo break, perde un set). Vale la prima che scatta. Per ciascuna indichi la quota di uscita (vuota =
  quota stimata di ogni partita) oppure una soglia in % di ROI. La vista *Uscita* dà l'analisi finale e il controllo del
  modello per il tuo piano.
- **Adjustment**: nella vista *Analisi* spunta *escludi* su un anno, oppure in *Operazioni* clicca una partita, guarda il
  punto per punto e premi *Escludi questa partita*: tutte le viste si aggiornano.
- **Oggi**: le partite in programma con classifica, form e precedenti a quote simili, e le strategie che attivano.
- **Rischio**: simula 1.000 futuri possibili e confronta stake fisso e Kelly frazionario.
- **Strategie**: salva le strategie valide, poi scarica con un clic le partite in programma (calcio) e ottieni
  i match che le attivano, con lo stake già calcolato sul tuo bankroll.
- **Registro**: annota le giocate reali (o importale dallo scanner), tieni i saldi dei conti e confronta
  ROI e CLV reali con quelli del backtest.
- **Roadmap 2027**: il piano dell'anno per trimestri, con lo stato di ogni passo.
- **Dati**: il database si aggiorna da solo all'apertura dell'app; puoi anche farlo a mano con *Aggiorna ora*.
""")
    st.info("Limiti dei dati: le quote tennis esistono solo per il circuito ATP (non per i Challenger) e sono una sola "
            "rilevazione per match; le quote pre-match del calcio sono rilevate qualche giorno prima della partita. "
            "Un backtest positivo non garantisce risultati futuri.", icon=":material/info:")


# --------------------------------------------------------------------------- pagina: dati
def update_panel() -> None:
    running = update_running()

    def body() -> None:
        up = _updater()
        if update_running():
            frac, label, _ = update_progress()
            st.progress(frac, text=f"Aggiornamento in corso: {label}")
            st.caption("Puoi continuare a usare l'app: i dati nuovi compaiono appena l'aggiornamento finisce.")
            return
        if up["proc"] is not None and not up["handled"]:
            st.rerun()
        if up["code"] is not None:
            _, _, tail = update_progress()
            news = next((x for x in tail if x.startswith("NOVITA'")), "")
            if up["code"] == 0:
                st.success("Aggiornamento completato" + (": " + news.replace("NOVITA' ", "") if news else "") + ".")
            else:
                st.error("L'aggiornamento ha segnalato problemi: controlla il dettaglio qui sotto.")
            with st.expander("Dettaglio dell'ultimo aggiornamento"):
                st.code("\n".join(tail) or "(nessun output)", language="text")

    with st.container(border=True):
        a, b, c = st.columns([1.2, 1.2, 3])
        if a.button("Aggiorna ora", type="primary", icon=":material/sync:", disabled=running, width="stretch"):
            start_update()
            st.rerun()
        if b.button("Riscarica tutto", icon=":material/cloud_download:", disabled=running, width="stretch",
                    help="Ignora i file già scaricati e ricostruisce l'intero storico. Serve solo se sospetti dati corrotti."):
            start_update(full=True)
            st.rerun()
        cfg = fx.load_settings()
        hours = {6: "ogni 6 ore", 12: "ogni 12 ore", 24: "una volta al giorno"}
        with c:
            x, y = st.columns([1.3, 1])
            auto = x.toggle("Aggiorna da solo all'apertura dell'app", value=bool(cfg.get("auto_update")))
            freq = y.selectbox("Frequenza", list(hours), index=list(hours).index(int(cfg.get("auto_update_hours") or 12))
                               if int(cfg.get("auto_update_hours") or 12) in hours else 1, format_func=hours.get,
                               label_visibility="collapsed", disabled=not auto)
        if auto != bool(cfg.get("auto_update")) or freq != int(cfg.get("auto_update_hours") or 12):
            fx.save_settings({"auto_update": auto, "auto_update_hours": freq})
        st.fragment(body, run_every=1 if running else None)()


def backup_panel() -> None:
    st.subheader("Backup dei tuoi dati")
    cfg = fx.load_settings()
    last = bk.last_backup()
    a, b = st.columns([3, 1.2])
    folder = a.text_input("Seconda cartella per la copia (facoltativa)", value=cfg.get("backup_dir", ""),
                          placeholder="es. ~/Library/Mobile Documents/com~apple~CloudDocs/SportsTradingLab",
                          help="Una cartella fuori dal progetto, per esempio su iCloud Drive o su una chiavetta: lì il backup sopravvive "
                               "anche se cancelli la cartella dell'app.")
    if folder != cfg.get("backup_dir", ""):
        fx.save_settings({"backup_dir": folder.strip()})
    b.markdown("<div style='height:1.75rem'></div>", unsafe_allow_html=True)
    if b.button("Esegui backup ora", icon=":material/backup:", width="stretch"):
        path, msg = bk.run_backup(folder.strip() or None)
        (st.success if path else st.info)(msg)
        last = bk.last_backup()
    st.caption(f"Ultimo backup: {last.name if last else 'nessuno'}. Ne viene fatto uno al giorno in automatico all'apertura dell'app "
               f"(si tengono gli ultimi {bk.KEEP}) nella cartella `data/backup`. Contiene registro delle giocate "
               "(`data/gestionale.duckdb`), strategie (`strategies.json`), roadmap e impostazioni. Gli storici delle partite non "
               "servono: si riscaricano. Per ripristinare, scompatta lo zip nella cartella del progetto.")


def page_data() -> None:
    header("Dati", "Stato del database e aggiornamento degli storici.")
    if db_ready():
        s = db_status(db_version())
        t = s["tennis"].set_index("circuit")
        n = lambda c: int(t["match"].get(c, 0))
        last = last_update_time()
        when = last.tz_convert("Europe/Rome").strftime("%d/%m/%Y %H:%M") if last is not None and pd.notna(last) else "mai"
        kpis([("Match ATP", num_it(n("ATP")), f"{num_it(int(t['con_quote'].get('ATP', 0)))} con quote", None),
              ("Match Challenger", num_it(n("CHALLENGER")), "per form e precedenti", None),
              ("Qualificazioni ATP", num_it(n("ATP_QUALI")), "per form e precedenti", None),
              ("Match Calcio", num_it(int(s["football"]["match"].sum())), f"{len(s['football'])} campionati", None),
              ("Ultimo aggiornamento", when, f"{C.DB_PATH.stat().st_size / 1e6:.0f} MB su disco", None)])
    else:
        st.info("Nessun database presente: premi *Aggiorna ora* per scaricare gli storici (un paio di minuti la prima volta).")
    update_panel()
    backup_panel()
    st.caption("L'aggiornamento scarica solo i file della stagione in corso, in parallelo, e ricarica nel database soltanto quelli "
               "cambiati: di norma dura pochi secondi. Lavora su una copia e la sostituisce alla fine, quindi non blocca l'app.")
    if db_ready():
        s = db_status(db_version())
        st.subheader("Tennis")
        st.dataframe(s["tennis"].rename(columns={"circuit": "circuito"}), hide_index=True, width="stretch")
        st.caption("Le quote (Pinnacle, Bet365, massimo e media mercato) esistono solo per il circuito ATP. "
                   "I Challenger servono a calcolare form, riposo e precedenti dei giocatori.")
        pivot = s["tennis_year"].pivot(index="anno", columns="circuit", values="n").fillna(0).astype(int)
        st.dataframe(pivot, width="stretch")
        st.subheader("Calcio")
        st.dataframe(s["football"], hide_index=True, width="stretch")


# --------------------------------------------------------------------------- pagina: backtester
def _load_preset() -> None:
    name = ss.get("preset_pick")
    if name:
        queue_cfg(PRESETS[name])
        ss["preset_pick"] = None


def describe_cashout(cfg: dict) -> str:
    c = co.settings(cfg)
    if not co.is_active(cfg):
        return ""
    side = cfg.get("side", "back")
    if c["method"] == "scalping":
        parts = ([f"take profit +{c['tp']:g}%"] if c["tp"] > 0 else []) + ([f"stop loss -{c['sl']:g}%"] if c["sl"] > 0 else [])
        return "scalping sul servizio: " + (", ".join(parts) if parts else "uscita a fine game")
    parts = []
    for w, name in (("tp", "take profit"), ("sl", "stop loss")):
        if not co.rule_active(c, w):
            continue
        when = exit_label(c[f"{w}_event"], w, side).lower()
        if c["method"] == "odds":
            q = c[f"{w}_quote"]
            parts.append(f"{name} quando {when} a {q:.2f}" if q else f"{name} quando {when} a quota stimata")
        else:
            parts.append(f"{name} {'+' if w == 'tp' else '-'}{c[w]:g}% quando {when}")
    return "uscita: " + ", ".join(parts)


def _reset_filters(sport: str) -> None:
    for k in [k for k in ss.keys() if k.startswith(f"bt_{sport}_f_") or k in ("bt_omin", "bt_omax", f"bt_{sport}_period", "bt_excl")]:
        del ss[k]


def _filter_widget(sport: str, spec: tuple, sel: pd.DataFrame) -> dict | None:
    col, label, _, kind = spec[:4]
    key = f"bt_{sport}_f_{col}"
    if kind == "cat":
        values = sorted(sel[col].dropna().unique().tolist()) if col in sel.columns else []
        fmt_fn = str
        if col == "round":
            values = [r for r in bt.ROUND_ORDER_CODES if r in values]
            fmt_fn = lambda r: bt.ROUND_LABELS.get(r, r)
        if key in ss:
            ss[key] = [bt.ROUND_ALIASES.get(v, v) if col == "round" else bt.LEVEL_ALIASES.get(v, v) if col == "level" else v
                       for v in ss[key]]
            ss[key] = [v for v in ss[key] if v in values]
        picked = st.multiselect(label, values, key=key, placeholder="tutti", format_func=fmt_fn)
        return {"in": picked} if picked else None
    if kind == "num":
        st.markdown(f"<small>{label}</small>", unsafe_allow_html=True)
        a, b = st.columns(2)
        lo = a.number_input("min", value=None, key=key + "_min", label_visibility="collapsed", placeholder="min")
        hi = b.number_input("max", value=None, key=key + "_max", label_visibility="collapsed", placeholder="max")
        return {"min": lo, "max": hi} if lo is not None or hi is not None else None
    lo, hi, step, cast = slider_bounds(spec)
    if key + "_sl" in ss:
        ss[key + "_sl"] = tuple(cast(v) for v in ss[key + "_sl"])
    ss.setdefault(key + "_sl", (lo, hi))
    a, b = st.slider(label, lo, hi, step=step, key=key + "_sl")
    if a <= lo and b >= hi:
        return None
    return {"min": a if a > lo else None, "max": b if b < hi else None}





def sidebar_config() -> dict:
    sb = st.sidebar
    sb.selectbox("Esempi pronti", list(PRESETS), index=None, key="preset_pick", on_change=_load_preset,
                 placeholder="carica un esempio...", help="Configurazioni di partenza da modificare: non sono consigli di gioco.")
    sb.subheader("1 · Cosa punti")
    ss.setdefault("bt_sport", "tennis")
    sport = sb.radio("Sport", list(SPORT_LABEL), format_func=SPORT_LABEL.get, horizontal=True, key="bt_sport")
    market = sb.selectbox("Mercato", bt.MARKETS[sport], format_func=MARKET_LABEL.get, key=f"bt_{sport}_market")
    opts = bt.SELECTIONS[market]
    ss.setdefault(f"bt_{sport}_{market}_sel", opts[:1])
    selection = sb.multiselect("Selezione", opts, format_func=SEL_LABEL.get, key=f"bt_{sport}_{market}_sel", placeholder="tutte")
    ss.setdefault("bt_side", "back")
    side = sb.radio("Modalità", ["back", "lay"], format_func={"back": "Back (punta)", "lay": "Lay (banca)"}.get,
                    horizontal=True, key="bt_side")
    books = bt.PRICE_BOOKS[sport]
    ss.setdefault(f"bt_{sport}_price", "avg")
    price = sb.selectbox("Quota di ingresso", books, format_func=bt.BOOK_LABELS.get, key=f"bt_{sport}_price")
    timing = "open"
    if sport == "football":
        ss.setdefault("bt_timing", "open")
        timing = sb.radio("Momento della quota", ["open", "close"], horizontal=True, key="bt_timing",
                          format_func={"open": "Pre-match", "close": "Chiusura"}.get)
    ss.setdefault("bt_comm", 0.0)
    commission = sb.number_input("Commissione Exchange sulle vincite (%)", 0.0, 20.0, step=0.5, key="bt_comm")
    lay_mode = "liability"
    if side == "lay":
        ss.setdefault("bt_laymode", "liability")
        lay_mode = sb.radio("Stake del lay", ["liability", "stake"], key="bt_laymode",
                            format_func={"liability": "Responsabilità fissa 1u", "stake": "Puntata banco fissa 1u"}.get)
        ss.setdefault("bt_lay_spread", bt.LAY_SPREAD)
        sb.number_input("Spread lay sull'exchange (%)", 0.0, 10.0, step=0.5, key="bt_lay_spread",
                        help="Si banca alla quota equa (la quota del bookmaker senza il suo margine) peggiorata di questo spread. "
                             "Bancare alla quota back del bookmaker non è possibile e darebbe risultati falsi.")

    sel = selections(sport, market, price, timing, db_version())
    sb.subheader("2 · Quote e periodo")
    c1, c2 = sb.columns(2)
    ss.setdefault("bt_omin", 1.01)
    ss.setdefault("bt_omax", 50.0)
    odds_min = c1.number_input("Quota min", 1.01, 1000.0, step=0.05, key="bt_omin")
    odds_max = c2.number_input("Quota max", 1.01, 1000.0, step=0.05, key="bt_omax")
    dmin, dmax = sel["date"].min().date(), sel["date"].max().date()
    pkey = f"bt_{sport}_period"
    if pkey in ss and not (isinstance(ss[pkey], tuple) and all(dmin <= d <= dmax for d in ss[pkey])):
        del ss[pkey]
    ss.setdefault(pkey, (dmin, dmax))
    period = sb.date_input("Periodo", min_value=dmin, max_value=dmax, key=pkey)
    d_from, d_to = (period if isinstance(period, tuple) and len(period) == 2 else (dmin, dmax))

    sb.subheader("3 · Filtri")
    slot = sb.container()
    filters: dict = {}
    groups: dict[str, list] = {}
    for spec in bt.FILTERS[sport]:
        groups.setdefault(spec[2], []).append(spec)
    for group, specs in groups.items():
        if group == "In partita":          # l'ingresso in partita si sceglie nel riquadro "Piano di trading"
            continue
        with sb.expander(group):
            for spec in specs:
                rule = _filter_widget(sport, spec, sel)
                if rule:
                    filters[spec[0]] = rule
    excl = False
    if sport == "tennis":
        ss.setdefault("bt_excl", False)
        excl = sb.checkbox("Escludi i match finiti per ritiro", key="bt_excl")
    a, b = slot.columns([3, 2])
    a.caption(f"{len(filters)} filtri attivi" if filters else "Nessun filtro attivo")
    b.button("Azzera", on_click=_reset_filters, args=(sport,), icon=":material/restart_alt:", width="stretch")
    with sb.expander("Impostazioni"):
        ss.setdefault("bt_bank", 100.0)
        st.number_input("Banca di riferimento per il drawdown % (unità)", 10.0, 100000.0, step=10.0, key="bt_bank",
                        help="Ogni operazione rischia 1 unità: il drawdown % è calcolato su una banca iniziale di questa dimensione.")
    return {
        "sport": sport, "market": market, "selection": selection, "side": side, "price": price,
        "price_timing": timing, "commission": commission / 100, "lay_mode": lay_mode,
        "odds_min": odds_min, "odds_max": odds_max, "date_from": str(d_from), "date_to": str(d_to),
        "exclude_retirements": bool(excl), "filters": filters,
        "lay_spread": float(ss.get("bt_lay_spread", bt.LAY_SPREAD)),
    }


def describe(cfg: dict) -> str:
    sport = cfg.get("sport", "tennis")
    market = cfg.get("market") or bt.MARKETS[sport][0]
    sel = ", ".join(SEL_LABEL.get(s, s) for s in cfg.get("selection") or []) or "tutte le selezioni"
    parts = [SPORT_LABEL.get(sport, sport), MARKET_LABEL.get(market, market), f"{'Lay' if cfg.get('side') == 'lay' else 'Back'} {sel}",
             f"quota {float(cfg.get('odds_min') or 1.01):.2f}-{float(cfg.get('odds_max') or 50):.2f} ({bt.BOOK_LABELS.get(cfg.get('price', 'avg'), '?')})"]
    if cfg.get("commission"):
        parts.append(f"comm. {cfg['commission'] * 100:.1f}%")
    for col, rule in (cfg.get("filters") or {}).items():
        label = bt.FILTER_LABELS.get(sport, {}).get(col, col)
        if rule.get("in"):
            parts.append(f"{label}: {', '.join(map(str, rule['in']))}")
        else:
            lo = "min" if rule.get("min") is None else f"{rule['min']:g}"
            hi = "max" if rule.get("max") is None else f"{rule['max']:g}"
            parts.append(f"{label}: {lo}..{hi}")
    if co.is_active(cfg):
        parts.append(describe_cashout(cfg))
    if cfg.get("exclude_years"):
        parts.append("anni esclusi: " + ", ".join(str(y) for y in sorted(cfg["exclude_years"])))
    if cfg.get("exclude_matches"):
        parts.append(f"partite escluse: {len(cfg['exclude_matches'])}")
    return "  ·  ".join(parts)


TRADE_COLS = {
    "tennis": ["date", "competition", "surface", "round", "event", "player", "selection", "rank", "opp_rank",
               "form10", "form_surf", "h2h_wins", "h2h_losses"],
    "football": ["date", "league", "event", "selection", "home_pos", "away_pos", "home_ppg5", "away_ppg5"],
}
STAT_COLS = {
    "trades": st.column_config.NumberColumn("operazioni"), "win_rate_pct": st.column_config.NumberColumn("win %", format="%.1f"),
    "profit": st.column_config.NumberColumn("profitto (u)", format="%+.2f"), "roi_pct": st.column_config.NumberColumn("ROI %", format="%+.2f"),
    "avg_odds": st.column_config.NumberColumn("quota media", format="%.2f"), "clv_pct": st.column_config.NumberColumn("CLV %", format="%+.2f"),
    "t_stat": st.column_config.NumberColumn("t-stat", format="%+.2f"),
    "roi_1": st.column_config.NumberColumn("ROI % 1ª metà", format="%+.1f"), "roi_2": st.column_config.NumberColumn("ROI % 2ª metà", format="%+.1f"),
}


def trades_table(trades: pd.DataFrame, sport: str) -> pd.DataFrame:
    cols = TRADE_COLS[sport] + ["score", "pp", "set_no", "game_no", "role", "held", "side", "pre_odds", "book_odds", "odds", "live_prob",
                                "model_odds", "odds_adj", "close_odds", "clv_pct", "won", "risk", "cashout", "exit_point", "exit_odds",
                                "pnl_hold", "pnl", "equity"]
    trades = trades.assign(pp=np.where(trades["_pp"].fillna(False).astype(bool), "sì", "")) if "_pp" in trades.columns else trades
    out = trades[[c for c in cols if c in trades.columns]].copy()
    out["date"] = out["date"].dt.date
    if "cashout" in out.columns:
        out["cashout"] = out["cashout"].map(lambda k: "" if k == "" else co.REASONS.get(k, k))
    out = out.loc[:, ~out.columns.duplicated()]
    return out


def save_form(key: str) -> None:
    last = ss.get("last")
    if not last or last["metrics"]["trades"] == 0:
        st.caption("Esegui prima un backtest con almeno un'operazione.")
        return
    with st.form(key, border=False):
        name = st.text_input("Nome della strategia", placeholder="es. Favoriti terra 1.30-1.80")
        if st.form_submit_button("Salva tra le strategie", icon=":material/save:"):
            if not name.strip():
                st.error("Inserisci un nome.")
            else:
                sv.save_strategy(name.strip(), last["cfg"], last["metrics"])
                st.success(f"Strategia '{name.strip()}' salvata in strategies.json")


def tab_summary(cfg: dict, res: bt.BacktestResult, bank: float) -> None:
    m, trades = res.metrics, res.trades
    kpis([("ROI / Yield", f"{m['roi_pct']:+.2f}%", f"su {num_it(m['staked'])} u rischiate", sign_tone(m["roi_pct"])),
          ("Profitto", f"{m['profit']:+.2f} u", "stake 1 u per operazione", sign_tone(m["profit"])),
          ("CLV medio", fmt(m["clv_pct"], "{:+.2f}%"), "contro la quota di chiusura", sign_tone(m["clv_pct"])),
          ("Max Drawdown", f"-{m['max_dd_units']:.1f} u", f"-{m['max_dd_pct']:.0f}% di una banca da {bank:.0f} u", None),
          ("Win Rate", f"{m['win_rate_pct']:.1f}%",
           f"quota live mediana {trades['odds'].median():.2f}" if res.live else f"quota media {m['avg_odds']:.2f}", None),
          ("Operazioni", num_it(m["trades"]), f"serie negativa max {m['longest_losing_streak']}", None)],
         hero_spark=trades["equity"].to_numpy())
    if not res.live:
        st.info(f"**Info:** ingresso pre-partita (lo scegli in «Quando entri»): ogni operazione entra alla propria quota "
                f"iniziale pre-partita. Quota iniziale mediana del campione: **{trades['odds'].median():.2f}**.", icon=":material/info:")
    if res.live:
        L = res.live
        kpis([("Quota media pre-partita", f"{L['avg_pre_odds']:.2f}", f"probabilità implicita {L['pre_implied_pct']:.1f}%", None),
              (("Quota live di entrata (manuale)", f"{L['override']:.2f}", f"stimata dal modello: {L['est_live_odds']:.2f} (mediana)", None)
               if L.get("override") else
               ("Quota live di entrata (stimata)", f"{L['avg_live_odds']:.2f}", f"mediana, {L['label'].split(' (')[0]}, margine {L['margin_pct']:g}%", None)),
              ("Vittorie reali", f"{L['real_win_pct']:.1f}%", f"IC 95%: {L['ci_lo']:.1f}-{L['ci_hi']:.1f}% su {num_it(L['n'])} partite", None),
              ("Probabilità stimata al momento", f"{L['est_prob_pct']:.1f}%",
               "dentro l'intervallo: scarto spiegato dal campione" if L["ci_lo"] <= L["est_prob_pct"] <= L["ci_hi"]
               else "fuori dall'intervallo: in questo campione la stima sbaglia",
               None if L["ci_lo"] <= L["est_prob_pct"] <= L["ci_hi"] else "bad")])
        st.caption(f":material/schedule: Ingresso **{L['label']}**: i filtri di quota valgono sulle quote pre-partita, ma l'operazione "
                   "si apre al prezzo live stimato in quel momento, quindi ROI e profitto non usano informazioni future. Le vittorie "
                   "sono conteggi esatti; la quota live è una stima tarata sui risultati reali (vedi vista *In partita*).")
        if L.get("override") and abs(L["override"] / L["est_live_odds"] - 1) > 0.10:
            st.warning(f"La quota live manuale ({L['override']:.2f}) è lontana dalla stima del modello ({L['est_live_odds']:.2f}). "
                       "Il ROI risultante vale solo se nella realtà trovi davvero quella quota in quel momento della partita: "
                       "con una quota più alta della stima il risultato è ottimistico.")
        if L.get("override"):
            st.caption(f":material/edit: Quota live impostata a mano a {L['override']:.2f} per tutte le operazioni, al posto della stima "
                       f"del modello (mediana {L['est_live_odds']:.2f}). Le vittorie restano quelle reali.")
    if "cashout" in trades.columns:
        closed = int((trades["cashout"] != "").sum())
        delta = float(trades["pnl"].sum() - trades["pnl_hold"].sum())
        st.caption(f":material/currency_exchange: Uscita anticipata: {num_it(closed)} operazioni chiuse prima della fine "
                   f"({closed / len(trades) * 100:.0f}%), effetto sul profitto {delta:+.2f} u rispetto a tenerle fino alla fine. "
                   "Analisi completa nella vista *Uscita*.")
    if cfg["side"] == "lay":
        st.caption(":material/warning: Lay: il prezzo di banca è la quota back selezionata. Sull'exchange la quota lay reale è più alta "
                   "e bancare a quote da bookmaker non è possibile: usa *Betfair Exchange* dove disponibile, imposta la commissione "
                   "e considera il risultato come limite superiore.")
    if cfg["sport"] == "tennis" and cfg["price"] == "ps":
        st.caption("Tennis: l'unica quota Pinnacle disponibile è quella di chiusura, quindi con ingresso Pinnacle il CLV è 0 per costruzione.")
    elif m["clv_coverage_pct"] < 99 and not res.live:
        st.caption(f"CLV calcolato sul {m['clv_coverage_pct']:.0f}% delle operazioni (quota di chiusura non disponibile per le altre).")
    show(equity_chart(trades))
    show(drawdown_chart(trades))
    mo = res.monthly
    hover = [f"{int(n)} operazioni, yield {y:+.1f}%" for n, y in zip(mo["trades"], mo["yield_pct"])]
    fig = bars_chart(mo["month"], mo["profit"], hover, "Rendimenti mensili (unità)", " u", 270)
    fig.update_xaxes(nticks=14, tickangle=0)
    fig.update_layout(bargap=0.1)
    show(fig)


def full_year_trades(cfg: dict, res: bt.BacktestResult, args: tuple, bank: float) -> pd.DataFrame:
    """Operazioni con anche gli anni esclusi (per poterli rivedere e rimettere nella tabella per anno)."""
    if not cfg.get("exclude_years"):
        return res.trades
    full_cfg = {k: v for k, v in cfg.items() if k != "exclude_years"}
    key = json.dumps(["full", full_cfg, bank, db_version()], sort_keys=True, default=str)
    if ss.get("_full_key") != key:
        scal = co.settings(full_cfg)["method"] == "scalping" and co.is_active(full_cfg)
        ss["_full_res"] = bt.backtest_selections(selections(*args, db_version()), full_cfg, bank,
                                                 service_games(db_version()) if scal else None)
        ss["_full_key"] = key
    return ss["_full_res"].trades


def year_editor(cfg: dict, res: bt.BacktestResult, args: tuple, bank: float) -> None:
    years_ex = sorted(int(y) for y in ss.get("bt_excl_years") or [])
    bd = bt.breakdown(full_year_trades(cfg, res, args, bank), "year")
    if bd.empty:
        return
    bd.insert(0, "escludi", bd["year"].astype(int).isin(years_ex))
    view = bd.rename(columns={"year": "anno"})
    edited = st.data_editor(view, key=f"year_editor_{'_'.join(map(str, years_ex))}", hide_index=True, width="stretch",
                            disabled=[c for c in view.columns if c != "escludi"],
                            column_config={**STAT_COLS, "anno": st.column_config.NumberColumn("anno", format="%d"),
                                           "escludi": st.column_config.CheckboxColumn(
                                               "escludi", help="Togli l'anno dall'analisi: vale per tutte le viste e resta nella "
                                                               "strategia salvata. Togli la spunta per rimetterlo.")})
    new = sorted(int(y) for y in edited.loc[edited["escludi"], "anno"])
    if new != years_ex:
        ss["bt_excl_years"] = new
        st.rerun()
    st.caption(":material/tune: Spunta *escludi* per togliere un anno poco significativo (adjustment): Riepilogo, Robustezza, Uscita e "
               "Operazioni si aggiornano subito. Gli anni esclusi restano in questa tabella con i loro numeri, così puoi "
               "rimetterli. Attenzione: togliere gli anni peggiori dopo averli visti gonfia il risultato, quindi fallo solo con un "
               "motivo (dati incompleti, stagione anomala, regole diverse).")


def tab_analysis(cfg: dict, res: bt.BacktestResult, args: tuple, bank: float) -> None:
    sport = cfg["sport"]
    trades = res.trades
    dims = {"year": "Anno", "odds_bucket": "Fascia di quota"}
    dims.update({f[0]: f[1] for f in bt.FILTERS[sport] if f[3] == "cat" and f[0] in trades.columns and f[0] != "year"
                 and f[0] not in bt.INPLAY.get(sport, set())})
    by = st.selectbox("Scomponi il risultato per", list(dims), format_func=dims.get)
    bd = bt.breakdown(trades, by)
    if not bd.empty:
        top = bd if by in ("year", "odds_bucket", "month", "weekday", "season") else bd.nlargest(25, "trades")
        hover = [f"{int(n)} operazioni, profitto {p:+.1f} u" for n, p in zip(top["trades"], top["profit"])]
        show(bars_chart(top[by].astype(str), top["roi_pct"], hover, f"ROI % per {dims[by].lower()}", "%", 330))
        if by == "year":
            year_editor(cfg, res, args, bank)
        else:
            st.dataframe(bd.rename(columns={by: dims[by].lower()}), hide_index=True, width="stretch", column_config=STAT_COLS)

    st.subheader("Segmenti migliori e peggiori")
    st.caption("Le operazioni attuali scomposte lungo tutte le variabili filtrabili (le numeriche in quattro fasce). "
               "Ordinati per t-stat: in alto dove la strategia rende meglio, in fondo dove perde. "
               "Le due colonne finali mostrano se il risultato regge in entrambe le metà del periodo.")
    c1, c2 = st.columns([1, 3])
    min_n = c1.number_input("Operazioni minime per segmento", 20, 5000, 100, 10)
    if ss.get("_bt_scan", (None,))[0] != int(min_n):
        ss["_bt_scan"] = (int(min_n), bt.segment_scan(trades, sport, int(min_n)))
    scan = ss["_bt_scan"][1]
    if scan.empty:
        st.info("Nessun segmento con abbastanza operazioni: abbassa la soglia.")
        return
    view = pd.concat([scan.head(15), scan.tail(8)]).drop_duplicates(["col", "segmento"]) if len(scan) > 23 else scan
    st.dataframe(view.drop(columns=["col", "rule"]), hide_index=True, width="stretch", column_config=STAT_COLS, height=420)
    labels = [f"{r.dimensione} = {r.segmento}   (ROI {r.roi_pct:+.1f}%, {r.trades} op.)" for r in view.itertuples()]
    pick = c2.selectbox("Aggiungi un segmento ai filtri", range(len(labels)), index=None, format_func=lambda i: labels[i],
                        placeholder="scegli un segmento...")
    if pick is not None and st.button("Applica come filtro", icon=":material/filter_alt:"):
        row = view.iloc[pick]
        new = {**cfg, "filters": {**cfg["filters"], row["col"]: row["rule"]}}
        queue_cfg(new)
        st.rerun()
    st.caption(":material/warning: Scegliere a posteriori il segmento migliore gonfia il risultato: dopo averlo applicato "
               "controlla la vista Robustezza, e diffida dei segmenti che vanno bene in una sola metà del periodo.")


def tab_robustness(cfg: dict, res: bt.BacktestResult, sel_args: tuple, bank: float) -> None:
    m, trades = res.metrics, res.trades
    c1, _ = st.columns([1, 2])
    frac = c1.slider("Quota di operazioni usata come campione di studio (%)", 50, 90, 70, 5,
                     help="Le operazioni più recenti restano fuori e servono da verifica: come se avessi scoperto la strategia a quella data.")
    cut, ins, oos = bt.split_metrics(trades, frac / 100, bank)
    yearly = bt.breakdown(trades, "year")
    conf = bt.roi_confidence(trades)
    checklist(bt.robustness_checks(m, oos, yearly, conf))
    st.caption("I controlli aiutano a distinguere un vantaggio reale da un risultato casuale; non sono una garanzia.")

    st.subheader("Studio contro verifica")
    rows = [("Operazioni", "trades", "{:.0f}"), ("ROI %", "roi_pct", "{:+.2f}"), ("Profitto (u)", "profit", "{:+.2f}"),
            ("Win rate %", "win_rate_pct", "{:.1f}"), ("CLV %", "clv_pct", "{:+.2f}"), ("Max drawdown (u)", "max_dd_units", "{:.1f}"),
            ("t-stat", "t_stat", "{:+.2f}")]
    table = pd.DataFrame({
        "metrica": [r[0] for r in rows],
        "tutto il periodo": [fmt(float(m[r[1]]), r[2]) for r in rows],
        f"studio (primo {frac}%)": [fmt(float(ins[r[1]]), r[2]) for r in rows],
        f"verifica (ultimo {100 - frac}%)": [fmt(float(oos[r[1]]), r[2]) for r in rows],
    })
    st.dataframe(table, hide_index=True, width="stretch")
    show(equity_chart(trades, cut))

    st.subheader("Walk-forward sulle fasce di quota")
    wf_table, wf = bt.walk_forward(trades)
    if wf_table.empty:
        st.caption("Servono almeno 200 operazioni distribuite su più di due anni.")
    else:
        kpis([("ROI scegliendo senza futuro", fmt(wf["roi_pct"], "{:+.2f}%"), f"{num_it(wf['trades'])} operazioni in {wf['years']} anni", sign_tone(wf["roi_pct"])),
              ("ROI giocando tutte le fasce", fmt(wf["naive_roi_pct"], "{:+.2f}%"), "stessi anni, nessuna selezione", sign_tone(wf["naive_roi_pct"])),
              ("Anni in utile", f"{wf['positive_years']} / {wf['years']}", "con le fasce scelte sul passato", None)])
        st.dataframe(wf_table, hide_index=True, width="stretch", column_config={
            "fasce_scelte": "fasce di quota scelte dagli anni precedenti", "roi_pct": st.column_config.NumberColumn("ROI %", format="%+.2f"),
            "profitto": st.column_config.NumberColumn("profitto (u)", format="%+.1f"), "tutte_le_fasce_op": "op. tutte le fasce",
            "tutte_le_fasce_roi_pct": st.column_config.NumberColumn("ROI % tutte le fasce", format="%+.2f")})
        st.caption("Ogni anno si usano solo le fasce di quota che negli anni precedenti erano in utile, poi si misura il risultato "
                   "nell'anno in corso: è ciò che avresti ottenuto senza conoscere il futuro. Se questo ROI è molto più basso di "
                   "quello dell'intero periodo, il range di quote scelto è probabilmente adattato al passato.")

    st.subheader("Anno per anno")
    st.dataframe(yearly.rename(columns={"year": "anno"}), hide_index=True, width="stretch", column_config=STAT_COLS)

    st.subheader("Stessa strategia, quote diverse")
    if st.toggle("Confronta i bookmaker", help="Ricalcola la strategia con la quota di ogni bookmaker. Richiede qualche secondo."):
        sport, market, _, timing = sel_args
        out = []
        for book in bt.PRICE_BOOKS[sport]:
            r = bt.backtest_selections(selections(sport, market, book, timing, db_version()), {**cfg, "price": book}, bank)
            out.append({"quota": bt.BOOK_LABELS[book], **{k: r.metrics[k] for k in ("trades", "roi_pct", "profit", "clv_pct", "avg_odds")}})
        st.dataframe(pd.DataFrame(out), hide_index=True, width="stretch", column_config=STAT_COLS)
        st.caption("Il range di quota si applica alla quota di ciascun bookmaker, quindi il numero di operazioni può cambiare. "
                   "Il *massimo mercato* è il tetto raggiungibile avendo conti su molti bookmaker.")


def tpsl_heatmap(grid: pd.DataFrame, current: tuple[float, float], roi_hold: float, unit: str = "%",
                 xlabel: str = "Take Profit", ylabel: str = "Stop Loss", mark_best: bool = True) -> go.Figure:
    """Mappa di calore del ROI per ogni coppia Take Profit (colonne) / Stop Loss (righe): soglie in % o quote di uscita."""
    z = grid.to_numpy(dtype=float)
    lim = max(float(np.nanmax(np.abs(z))), 1.0)
    lab = (lambda v: "off" if v == 0 else f"{v:g}%") if unit == "%" else (lambda v: "off" if v == 0 else f"{v:.2f}")
    fig = go.Figure(go.Heatmap(
        z=z, x=[lab(v) for v in grid.columns], y=[lab(v) for v in grid.index], zmid=0, zmin=-lim, zmax=lim,
        colorscale=[[0.0, "#b3261e"], [0.35, "#7a2f2b"], [0.5, "#2a3140"], [0.65, "#1f6b2a"], [1.0, "#16a34a"]],
        text=[[f"{v:+.1f}" for v in row] for row in z], texttemplate="%{text}", textfont=dict(size=11, color=INK),
        xgap=2, ygap=2, colorbar=dict(title=dict(text="ROI %", font=dict(color=MUTED, size=11)), tickfont=dict(color=MUTED), ticksuffix="%"),
        hovertemplate="Take Profit %{x}<br>Stop Loss %{y}<br>ROI %{z:+.2f}%<extra></extra>"))
    base_layout(fig, 470, f"ROI (%) per Take Profit e Stop Loss - tenendo tutto fino alla fine: {roi_hold:+.2f}%")
    fig.update_layout(hovermode="closest", margin=dict(l=70, r=20, t=52, b=56))
    fig.update_xaxes(title_text=xlabel, title_font=dict(color=MUTED, size=11), type="category", showgrid=False, ticks="")
    fig.update_yaxes(title_text=ylabel, title_font=dict(color=MUTED, size=11), type="category", showgrid=False)
    if mark_best:
        i, j = np.unravel_index(np.nanargmax(z), z.shape)
        fig.add_shape(type="rect", x0=j - 0.5, x1=j + 0.5, y0=i - 0.5, y1=i + 0.5, line=dict(color=INK, width=2), xref="x", yref="y")
    tp, sl = current
    if tp in list(grid.columns) and sl in list(grid.index):
        ci, cj = list(grid.index).index(sl), list(grid.columns).index(tp)
        fig.add_shape(type="rect", x0=cj - 0.5, x1=cj + 0.5, y0=ci - 0.5, y1=ci + 0.5, line=dict(color=BLUE, width=2, dash="dot"), xref="x", yref="y")
    return fig


def tab_cashout(cfg: dict, res: bt.BacktestResult, sel_args: tuple, bank: float) -> None:
    c = co.settings(cfg)
    trades = res.trades
    if not co.is_active(cfg) or "pnl_hold" not in trades.columns:
        st.info("Nel riquadro **Piano di trading**, in alto, scegli *come esci* e almeno una regola: **Take Profit** (quando esci "
                "in positivo: per esempio a fine primo set se lo vince) e/o **Stop Loss** (quando esci in negativo: per esempio "
                "al controbreak se lo subisce). Qui trovi l'analisi finale rispetto a tenere fino alla fine.")
        return
    scal = c["method"] == "scalping"
    hold = trades.assign(pnl=trades["pnl_hold"])
    hold["equity"] = hold["pnl"].cumsum()
    mh, m = bt.compute_metrics(hold, bank), res.metrics
    closed = int((trades["cashout"] != "").sum())
    kpis([("ROI con uscita", f"{m['roi_pct']:+.2f}%", f"tenendo fino alla fine: {mh['roi_pct']:+.2f}%", sign_tone(m["roi_pct"])),
          ("Effetto sul profitto", f"{m['profit'] - mh['profit']:+.2f} u", "rispetto a tenere fino alla fine", sign_tone(m["profit"] - mh["profit"])),
          ("Uscite anticipate", f"{closed / len(trades) * 100:.0f}%", f"{num_it(closed)} su {num_it(len(trades))}", None),
          ("Max drawdown", f"-{m['max_dd_units']:.1f} u", f"senza: -{mh['max_dd_units']:.1f} u",
           "good" if m["max_dd_units"] < mh["max_dd_units"] else None)])
    if not scal:
        key = json.dumps(["bench", cfg, db_version()], sort_keys=True, default=str)
        if ss.get("_bench_key") != key:
            fair = co.model_benchmark(trades, {**cfg, "cashout": {**c, "margin_pct": 0.0, "tp_quote": None, "sl_quote": None}})
            real = co.model_benchmark(trades, {**cfg, "cashout": {**c, "tp_quote": None, "sl_quote": None}})
            ss["_bench"], ss["_bench_key"] = (fair, real), key
        fair, real = ss["_bench"]
        gap, se = fair["gap"], fair["se"]
        ok = np.isfinite(gap) and abs(gap) <= max(2 * se, 1.0)
        items = [("Controllo del modello", f"{gap:+.1f} ± {se:.1f} punti",
                  f"uscendo alla quota stimata con margine 0: {fair['roi']:+.2f}% contro {mh['roi_pct']:+.2f}% tenendo",
                  "good" if ok else "bad")]
        if c["method"] == "odds" and (c["tp_quote"] or c["sl_quote"]):
            items.append(("ROI con le quote stimate", f"{real['roi']:+.2f}%",
                          f"stesse regole, quota stimata di ogni partita meno il margine {c['margin_pct']:g}%", sign_tone(real["roi"])))
        elif c["method"] == "odds":
            items.append(("ROI con margine 0", f"{fair['roi']:+.2f}%", "stesse uscite alla quota equa stimata", sign_tone(fair["roi"])))
        kpis(items)
        st.caption(":material/verified: *Controllo del modello*: con margine zero, uscire alla quota stimata non dovrebbe né "
                   "guadagnare né perdere rispetto a tenere, se la stima è giusta per **questo** piano. Il ± è l'errore standard "
                   "(con poche partite il risultato tenendo oscilla molto): verde = scarto compatibile con una stima corretta "
                   "(entro due errori standard); rosso = in questo piano il modello sbaglia, di solito su eventi successivi al "
                   "primo set dopo ingressi particolari: fidati più delle tue quote e del risultato tenendo."
                   + (" *ROI con le quote stimate*: lo stesso piano con quote realistiche partita per partita, da confrontare con "
                      "le tue." if c["method"] == "odds" and (c["tp_quote"] or c["sl_quote"]) else ""))
    st.subheader("Analisi finale per momento ed esito")
    summ = co.summary(trades)
    st.dataframe(summ, hide_index=True, width="stretch", column_config={
        "quota_pct": st.column_config.NumberColumn("% operazioni", format="%.1f"),
        "quota_ingresso": st.column_config.NumberColumn("quota ingresso media", format="%.2f"),
        "quota_uscita": st.column_config.NumberColumn("quota uscita media", format="%.2f"),
        "roi_uscita_pct": st.column_config.NumberColumn("ROI all'uscita %", format="%+.1f"),
        "profitto": st.column_config.NumberColumn("profitto ottenuto (u)", format="%+.2f"),
        "profitto_tenendo": st.column_config.NumberColumn("tenendole fino alla fine (u)", format="%+.2f"),
        "differenza": st.column_config.NumberColumn("differenza (u)", format="%+.2f")})
    if not scal:
        side = cfg.get("side", "back")
        lines = []
        for w in ("tp", "sl"):
            ev = c[f"{w}_event"]
            if not co.rule_active(c, w) or ev in ("all", "ht"):
                continue
            est = co.rule_estimate(trades, cfg, w)
            lines.append(f"*{exit_label(ev, w, side).lower()}* in **{num_it(est['n'])}** partite su {num_it(len(trades))}")
        if lines:
            st.caption(":material/fact_check: Esiti reali nel database, partita per partita e dopo il momento dell'ingresso: "
                       + " · ".join(lines) + ". Le altre non hanno quell'evento (o lo hanno prima dell'ingresso) e restano aperte "
                       "fino all'altra regola o alla fine.")

    if scal:
        _scalping_grid(cfg, c, sel_args, bank)
        return
    grid_key = json.dumps(["grid", cfg, db_version()], sort_keys=True, default=str)
    if c["method"] == "odds":
        st.subheader("Mappa quote di uscita: Take Profit × Stop Loss")
        side = cfg.get("side", "back")
        factors = (0.95, 1.0, 1.05, 1.1, 1.15, 1.2, 1.3) if side == "back" else (1.05, 1.0, 0.95, 0.91, 0.87, 0.83, 0.77)

        def axis(w):
            if not co.rule_active(c, w):
                return [0.0]
            est = co.rule_estimate(trades, cfg, w)["est"] if c[f"{w}_event"] not in ("all", "ht") else np.nan
            if not np.isfinite(est):
                return [0.0] + ([c[f"{w}_quote"]] if c[f"{w}_quote"] else [])
            vals = {round(est * f, 2) for f in factors} | ({round(c[f"{w}_quote"], 2)} if c[f"{w}_quote"] else set())
            return [0.0] + sorted(v for v in vals if v > 1.01)
        if ss.get("_grid_key") != grid_key:
            tps, sls = axis("tp"), axis("sl")
            ss["_grid"] = (co.quotes_grid(trades, cfg, tps, sls) if len(tps) * len(sls) > 1 else pd.DataFrame(), mh["roi_pct"])
            ss["_grid_key"] = grid_key
        grid, hold_roi = ss["_grid"]
        if grid.empty:
            st.info("Imposta un evento di Take Profit o di Stop Loss per vedere la mappa.")
        else:
            show(tpsl_heatmap(grid, (c["tp_quote"] or 0.0, c["sl_quote"] or 0.0), hold_roi, unit="quota", mark_best=False,
                              xlabel="Take Profit: quota di uscita" + (f" ({exit_label(c['tp_event'], 'tp', side).lower()})" if co.rule_active(c, 'tp') else ""),
                              ylabel="Stop Loss: quota" + (f" ({exit_label(c['sl_event'], 'sl', side).lower()})" if co.rule_active(c, 'sl') else "")))
            st.caption("Ogni cella è il ROI della strategia uscendo a quelle due quote (off = quella regola spenta). Le quote partono "
                       "da poco meglio della stima del modello e peggiorano fino al 30%, come succede con margine e ritardo nel "
                       "chiudere; bordo blu tratteggiato: le quote impostate. Una quota più favorevole rende sempre di più, quindi la "
                       "mappa non indica una combinazione «migliore»: serve a vedere fino a quale quota uscire conviene rispetto a "
                       "non uscire (riga e colonna *off*).")
    else:
        st.subheader("Mappa Take Profit × Stop Loss")
        if ss.get("_grid_key") != grid_key:
            tps = (0, 10, 20, 30, 40, 50, 75, 100, 150) if co.rule_active({**c, "tp": 1.0}, "tp") else (0,)
            sls = (0, 10, 20, 30, 40, 50, 60, 70, 80, 90) if co.rule_active({**c, "sl": 1.0}, "sl") else (0,)
            ss["_grid"] = (co.pct_grid(trades, cfg, tps, sls) if len(tps) * len(sls) > 1 else pd.DataFrame(), mh["roi_pct"])
            ss["_grid_key"] = grid_key
        grid, hold_roi = ss["_grid"]
        if grid.empty:
            st.info("Imposta un evento di Take Profit o di Stop Loss per vedere la mappa.")
        else:
            show(tpsl_heatmap(grid, (c["tp"], c["sl"]), hold_roi))
            best = np.unravel_index(np.nanargmax(grid.to_numpy()), grid.shape)
            st.caption(f"Ogni cella è il ROI della strategia con quella coppia di soglie (off = soglia disattivata). Bordo bianco: la "
                       f"combinazione migliore nello storico (Take Profit {grid.columns[best[1]]:g}%, Stop Loss {grid.index[best[0]]:g}%); "
                       "bordo blu tratteggiato: le soglie impostate ora. La migliore è scelta guardando il passato: prima di usarla, "
                       "controlla che regga nella vista *Robustezza*.")
    st.caption(":material/info: Esiti reali dal database (break, controbreak, set, intervallo); il ROI di ogni operazione è calcolato "
               "dalla sua quota di ingresso (pre-partita, live stimata o inserita a mano) alla quota di uscita: back = ingresso/uscita − 1, "
               "lay = (1 − ingresso/uscita)/(ingresso − 1), commissione sui profitti. Le quote stimate vengono da modelli tarati sui "
               "risultati reali (allenati sul 2019-2022, verificati sul 2023+).")


def _scalping_grid(cfg: dict, c: dict, sel_args: tuple, bank: float) -> None:
    st.subheader("Mappa Take Profit × Stop Loss")
    key = json.dumps([cfg, db_version()], sort_keys=True, default=str)
    if ss.get("_grid_key") != key:
        with st.spinner("Provo tutte le combinazioni di soglie..."):
            base = bt.backtest_selections(selections(*sel_args, db_version()), {**cfg, "cashout": {"mode": "off"}}, bank)
            g, _ = sc.strategy_games(service_games(db_version()), base.trades, c["who"])
            meta, vals, final = sc.scalp_paths(g, side=cfg.get("side", "back"), spread_pct=c["spread_pct"],
                                               commission=float(cfg.get("commission") or 0.0), skip_games=int(c["skip_games"]),
                                               decided_pct=c["decided_pct"], stop_at_bp=bool(c["stop_at_bp"]))
            risk = np.ones(len(final))
            tps, sls = (0, 1, 2, 3, 4, 5, 7.5, 10, 15, 20), (0, 2, 4, 6, 8, 10, 15, 20, 30, 50)
            ss["_typical"] = sc.typical_moves(g, side=cfg.get("side", "back"), spread_pct=c["spread_pct"],
                                              commission=float(cfg.get("commission") or 0.0), skip_games=int(c["skip_games"]),
                                              decided_pct=c["decided_pct"])
            hold_roi = float((final * risk).sum() / risk.sum() * 100) if len(risk) else float("nan")
            ss["_grid"] = (co.tpsl_grid(vals, final, risk, tps, sls), hold_roi) if len(risk) else (pd.DataFrame(), hold_roi)
        ss["_grid_key"] = key
    grid, hold_roi = ss["_grid"]
    if grid.empty:
        st.info("Nessuna operazione su cui provare le soglie.")
    else:
        show(tpsl_heatmap(grid, (c["tp"], c["sl"]), hold_roi))
        best = np.unravel_index(np.nanargmax(grid.to_numpy()), grid.shape)
        st.caption(f"Ogni cella è il ROI con quella coppia di soglie (off = soglia disattivata). Bordo bianco: la migliore nello "
                   f"storico (Take Profit {grid.columns[best[1]]:g}%, Stop Loss {grid.index[best[0]]:g}%); bordo blu: quelle impostate.")
    if len(ss.get("_typical", [])):
        st.subheader("Valori tipici di un game (servizio tenuto o break)")
        st.dataframe(ss["_typical"], hide_index=True, width="stretch", column_config={
            "game": st.column_config.NumberColumn(format="%d"), "tenuti_pct": st.column_config.NumberColumn("servizio tenuto %", format="%.1f"),
            "guadagno_tenuto_pct": st.column_config.NumberColumn("guadagno se tiene (mediana %)", format="%+.2f"),
            "perdita_break_pct": st.column_config.NumberColumn("perdita se subisce il break (mediana %)", format="%+.2f"),
            "rendimento_medio_pct": st.column_config.NumberColumn("rendimento medio a fine game %", format="%+.2f")})
        st.caption("Rendimento del game tenuto fino alla fine, per superficie e per ruolo di chi serve: il servizio tenuto vale "
                   "molto meno del break, e sulla terra il guadagno è più alto e la perdita più bassa. I valori vengono dai game "
                   "reali punto per punto, con le quote stimate dal modello tarato.")
    st.caption(":material/info: Nello scalping gli esiti dei punti sono reali e le quote sono stimate a ogni punto; si esce al "
               "prezzo del punto, che può superare la soglia (come sul mercato vero, dove la quota salta da un punto all'altro).")


@st.cache_data(show_spinner=False, max_entries=64)
def match_record(sport: str, match_id: str, version: float) -> dict:
    table = "tennis_matches" if sport == "tennis" else "football_matches"
    with closing(bt.connect()) as con:
        df = con.execute(f"SELECT * FROM {table} WHERE match_id = ?", [match_id]).df()
    return df.iloc[0].to_dict() if len(df) else {}


@st.cache_data(show_spinner=False, max_entries=64)
def match_games(match_id: str, version: float) -> pd.DataFrame:
    with closing(bt.connect()) as con:
        if "tennis_service_games" not in {r[0] for r in con.execute("SHOW TABLES").fetchall()}:
            return pd.DataFrame()
        return con.execute("SELECT * FROM tennis_service_games WHERE db_match_id = ? ORDER BY set_no, game_no", [match_id]).df()


def tennis_detail(trade: dict, rec: dict, mid: str) -> None:
    me, opp = str(trade.get("player", "")), str(trade.get("opponent", ""))
    won = bool(trade.get("won"))
    if rec:
        x, y = ("w", "l") if won else ("l", "w")
        num = lambda k: rec.get(k) if rec.get(k) == rec.get(k) else None

        def pct(a, b):
            return f"{num(a) / num(b) * 100:.0f}%" if num(a) is not None and num(b) else "n/d"

        def stat(p):
            return {"Ranking": f"{num(f'{'winner' if p == 'w' else 'loser'}_rank'):.0f}" if num(f"{'winner' if p == 'w' else 'loser'}_rank") else "n/d",
                    "Ace": f"{num(f'{p}_ace'):.0f}" if num(f"{p}_ace") is not None else "n/d",
                    "Doppi falli": f"{num(f'{p}_df'):.0f}" if num(f"{p}_df") is not None else "n/d",
                    "Prime in campo": pct(f"{p}_1stIn", f"{p}_svpt"),
                    "Punti vinti con la prima": pct(f"{p}_1stWon", f"{p}_1stIn"),
                    "Punti vinti con la seconda": (f"{num(f'{p}_2ndWon') / (num(f'{p}_svpt') - num(f'{p}_1stIn')) * 100:.0f}%"
                                                   if num(f"{p}_2ndWon") is not None and num(f"{p}_svpt") and num(f"{p}_1stIn") is not None
                                                   and num(f"{p}_svpt") > num(f"{p}_1stIn") else "n/d"),
                    "Palle break salvate": (f"{num(f'{p}_bpSaved'):.0f} su {num(f'{p}_bpFaced'):.0f}"
                                            if num(f"{p}_bpFaced") is not None and num(f"{p}_bpSaved") is not None else "n/d"),
                    "Game al servizio": f"{num(f'{p}_SvGms'):.0f}" if num(f"{p}_SvGms") is not None else "n/d",
                    "Quota pre-partita (media)": f"{num(f'avg_{p}'):.2f}" if num(f"avg_{p}") else "n/d",
                    "Quota pre-partita (Pinnacle)": f"{num(f'ps_{p}'):.2f}" if num(f"ps_{p}") else "n/d"}
        sm, so = stat(x), stat(y)
        st.dataframe(pd.DataFrame({"statistica": list(sm), me: list(sm.values()), opp: list(so.values())}),
                     hide_index=True, width="stretch")
    games = match_games(mid, db_version())
    if games.empty:
        st.info("Punto per punto non disponibile: il Match Charting Project registra circa un match su cinque, soprattutto nei "
                "tornei maggiori. Qui sopra restano risultato e statistiche ufficiali.", icon=":material/info:")
        return
    key = bt._norm_name(me)
    fb, cb = trade.get("_fb_game"), trade.get("_cb_game")
    press_t = trade.get("_press_t")
    rows = []
    for r in games.itertuples():
        mine = bt._norm_name(r.server) == key
        held = bool(r.held)
        p_srv = float(r.v_hold if held else r.v_break)
        p_me = p_srv if mine else 1 - p_srv
        a, b = (r.srv_games, r.ret_games) if mine else (r.ret_games, r.srv_games)
        pts, bp, deuce = sc.point_scores(r.seq)
        tag = []
        if r.set_no == 1 and fb == fb and r.game_no == fb:
            tag.append("primo break")
        if r.set_no == 1 and cb == cb and r.game_no == cb:
            tag.append("controbreak")
        if press_t == press_t and press_t is not None and r.set_no * 100 + r.game_no == press_t:
            tag.append(f"{me.split()[-1]} sotto pressione")
        rows.append({"set": int(r.set_no), "game": int(r.game_no), "prima del game": f"{int(a)}-{int(b)}",
                     "serve": me if mine else opp, "punti (di chi serve)": pts,
                     "esito": "tenuto" if held else ("break subito" if mine else "break fatto"),
                     "palle break": bp, "vantaggi": "sì" if deuce else "", "evento": ", ".join(tag),
                     "prob_me": p_me * 100, "quota_me": 1 / p_me if p_me > 0 else np.nan})
    gd = pd.DataFrame(rows)
    x = [f"S{s} G{g}" for s, g in zip(gd["set"], gd["game"])]
    fig = go.Figure(go.Scatter(x=x, y=gd["prob_me"], mode="lines+markers", line=dict(color=BLUE, width=2),
                               marker=dict(size=7, color=np.where(gd["esito"] == "break fatto", GOOD,
                                                                  np.where(gd["esito"] == "break subito", BAD, MUTED))),
                               hovertemplate="%{x}<br>probabilità %{y:.1f}%<extra></extra>"))
    base_layout(fig, 300, f"Probabilità di vittoria di {me} dopo ogni game (modello a punti)")
    fig.update_yaxes(range=[0, 100], ticksuffix="%")
    show(fig)
    st.dataframe(gd, hide_index=True, width="stretch", height=380, column_config={
        "prob_me": st.column_config.NumberColumn(f"prob. {me.split()[-1]} %", format="%.1f"),
        "quota_me": st.column_config.NumberColumn(f"quota stimata {me.split()[-1]}", format="%.2f"),
        "palle break": st.column_config.NumberColumn(format="%d")})
    st.caption("Punti dal punto di vista di chi serve (V = vantaggio). Pallino verde = break fatto, rosso = break subito. La "
               "probabilità e la quota dopo ogni game vengono dal modello a punti indipendenti (non dalle quote reali, che il "
               "database non ha in partita). I tie-break e i game registrati male non sono nel Match Charting Project.")


def football_detail(trade: dict, rec: dict) -> None:
    if not rec:
        return
    num = lambda k: rec.get(k) if rec.get(k) == rec.get(k) else None
    rows = []
    for lab, k in (("Gol", "fthg"), ("Gol nel primo tempo", "hthg"), ("Tiri", "home_shots"), ("Tiri in porta", "home_sot"),
                   ("Angoli", "home_corners"), ("Falli", "home_fouls"), ("Ammonizioni", "home_yellow"), ("Espulsioni", "home_red"),
                   ("xG", "home_xg")):
        ka = k.replace("home_", "away_").replace("fthg", "ftag").replace("hthg", "htag")
        h, a = num(k), num(ka)
        if h is None and a is None:
            continue
        f = (lambda v: f"{v:.2f}") if k == "home_xg" else (lambda v: f"{v:.0f}")
        rows.append({"": lab, str(rec.get("home_team")): f(h) if h is not None else "n/d", str(rec.get("away_team")): f(a) if a is not None else "n/d"})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    odds = []
    for book, lab in (("avg", "media"), ("max", "massimo"), ("ps", "Pinnacle"), ("b365", "Bet365")):
        vals = [num(f"{book}_{o}") for o in ("h", "d", "a", "o25", "u25")]
        if any(v for v in vals):
            odds.append({"quote": lab, "1": vals[0], "X": vals[1], "2": vals[2], "Over 2.5": vals[3], "Under 2.5": vals[4]})
    if odds:
        st.dataframe(pd.DataFrame(odds), hide_index=True, width="stretch", column_config={
            c: st.column_config.NumberColumn(format="%.2f") for c in ("1", "X", "2", "Over 2.5", "Under 2.5")})
    st.caption("Nel calcio il database ha risultato, statistiche e quote pre-partita: non esiste un minuto per minuto.")


@st.dialog("Scheda partita", width="large")
def match_dialog(trade: dict, cfg: dict) -> None:
    sport = cfg["sport"]
    mid = str(trade.get("match_id", "")).split("|")[0]
    rec = match_record(sport, mid, db_version())
    st.markdown(f"### {trade.get('event', '')}")
    when = pd.Timestamp(trade.get("date")).strftime("%d/%m/%Y") if trade.get("date") is not None else ""
    meta = [when, str(trade.get("competition") or trade.get("league") or ""), str(trade.get("round") or ""),
            str(trade.get("surface") or ""), f"al meglio dei {int(trade['best_of'])} set" if trade.get("best_of") == trade.get("best_of") and trade.get("best_of") else ""]
    st.caption(" · ".join(m for m in meta if m and m != "nan"))
    who = trade.get("player") or SEL_LABEL.get(trade.get("selection"), trade.get("selection"))
    side = "Banca" if trade.get("side") == "lay" else "Punta"
    exit_txt = trade.get("exit_point") or "fine partita"
    reason = co.REASONS.get(trade.get("cashout") or "", "")
    pnl, hold = float(trade.get("pnl") or 0.0), trade.get("pnl_hold")
    kpis([("Risultato", str(trade.get("score") or "n/d"), f"dal punto di vista di {who}", None),
          ("Ingresso", f"{float(trade.get('odds') or 0):.2f}", f"{side} {who}" + (f" · pre-partita {float(trade['pre_odds']):.2f}"
                                                                             if trade.get("pre_odds") == trade.get("pre_odds") and trade.get("pre_odds") else ""), None),
          ("Uscita", reason or "fine partita", exit_txt if reason else "posizione tenuta", None),
          ("Profitto", f"{pnl:+.2f} u", (f"tenendo: {float(hold):+.2f} u" if hold == hold and hold is not None else "stake 1 u"), sign_tone(pnl))])
    if sport == "tennis":
        tennis_detail(trade, rec, mid)
    else:
        football_detail(trade, rec)
    st.divider()
    key = f"{mid}|{trade.get('player') if trade.get('player') is not None else trade.get('selection')}"
    adj = dict(ss.get("bt_odds_adj") or {})
    model = trade.get("model_odds")
    model = float(model) if model is not None and model == model else float(trade.get("odds") or 0)
    if "|" not in str(trade.get("match_id", "")):           # non per lo scalping (operazioni game per game)
        st.markdown("**Correggi la quota di ingresso** (adjustment)")
        q1, q2, q3 = st.columns([1, 1, 2])
        newq = q1.number_input("Quota corretta", min_value=1.01, max_value=1000.0, value=float(min(max(adj.get(key, model), 1.01), 1000.0)), step=0.01,
                               format="%.2f", key=f"adjq_{key}",
                               help="Per esempio la quota che c'era davvero in quel momento. Sostituisce quella stimata in tutta "
                                    "l'analisi: profitto, uscite, quota live mediana.")
        if q2.button("Applica", icon=":material/edit:", width="stretch"):
            adj[key] = round(float(newq), 2)
            ss["bt_odds_adj"] = adj
            st.rerun()
        if key in adj:
            if q2.button("Torna alla stimata", icon=":material/undo:", width="stretch"):
                adj.pop(key, None)
                ss["bt_odds_adj"] = adj
                st.rerun()
            q3.caption(f"Quota stimata dal modello: **{model:.2f}**, corretta a mano: **{adj[key]:.2f}**.")
        else:
            live_entry = trade.get("pre_odds") == trade.get("pre_odds") and bool(trade.get("pre_odds"))
            q3.caption(f"Quota attuale: **{model:.2f}** (" + ("stimata dal modello al momento dell'ingresso" if live_entry
                                                             else "pre-partita") + ").")
    c1, c2 = st.columns([1, 2])
    if c1.button("Escludi questa partita", icon=":material/block:", type="primary", width="stretch"):
        ss["bt_excl_matches"] = sorted(set(ss.get("bt_excl_matches") or []) | {mid})
        st.rerun()
    c2.caption("Adjustment: la partita viene tolta dal backtest in tutte le viste e dalla strategia salvata. Puoi rimetterla dal "
               "riquadro *Adjustment* sopra i risultati.")


def tab_trades(cfg: dict, trades: pd.DataFrame) -> None:
    table = trades_table(trades, cfg["sport"])
    shown = table.tail(MAX_TABLE_ROWS)
    if len(table) > MAX_TABLE_ROWS:
        st.caption(f"Mostrate le ultime {num_it(MAX_TABLE_ROWS)} operazioni su {num_it(len(table))}; l'esportazione le contiene tutte.")
    st.caption(":material/touch_app: Clicca una riga per aprire la scheda della partita: risultato, statistiche e, quando registrato, "
               "il punto per punto game per game con la quota stimata. Dalla scheda puoi escludere la partita (adjustment).")
    n_ex = len(ss.get("bt_excl_matches") or []) * 1000 + len(ss.get("bt_odds_adj") or {})
    event = st.dataframe(shown, hide_index=True, width="stretch", height=460, on_select="rerun", selection_mode="single-row",
                         key=f"trades_tbl_{n_ex}_{len(trades)}", column_config={
        "odds": st.column_config.NumberColumn("quota", format="%.2f"), "close_odds": st.column_config.NumberColumn("chiusura", format="%.2f"),
        "clv_pct": st.column_config.NumberColumn("clv %", format="%.2f"), "pnl": st.column_config.NumberColumn(format="%.3f"),
        "risk": st.column_config.NumberColumn(format="%.2f"), "equity": st.column_config.NumberColumn(format="%.2f"),
        "pre_odds": st.column_config.NumberColumn("quota pre-partita", format="%.2f"),
        "live_prob": st.column_config.NumberColumn("prob. live stimata", format="%.3f"),
        "pnl_hold": st.column_config.NumberColumn("pnl tenendo", format="%.3f"),
        "exit_odds": st.column_config.NumberColumn("quota uscita", format="%.2f"),
        "score": st.column_config.TextColumn("risultato"), "pp": st.column_config.TextColumn("punto per punto"),
        "model_odds": st.column_config.NumberColumn("quota stimata", format="%.2f"),
        "odds_adj": st.column_config.CheckboxColumn("quota corretta"),
        "form10": st.column_config.NumberColumn(format="%.0f"), "form_surf": st.column_config.NumberColumn(format="%.0f"),
        "home_ppg5": st.column_config.NumberColumn(format="%.1f"), "away_ppg5": st.column_config.NumberColumn(format="%.1f"),
    })
    rows = list(getattr(getattr(event, "selection", None), "rows", None) or [])
    if rows:
        idx = shown.index[rows[0]]
        trade = trades.loc[idx].to_dict()
        token = (n_ex, int(idx), ss.get("_bt_key"))
        if ss.get("_dlg_token") != token:
            ss["_dlg_token"] = token
            match_dialog(trade, cfg)
        elif st.button("Riapri la scheda della partita selezionata", icon=":material/open_in_new:"):
            match_dialog(trade, cfg)
    st.download_button("Esporta tutte le operazioni (CSV)", table.to_csv(index=False).encode("utf-8"),
                       file_name=f"operazioni_{cfg['sport']}_{date.today():%Y%m%d}.csv", mime="text/csv", icon=":material/download:")


VIEWS = ["Riepilogo", "In partita", "Analisi", "Robustezza", "Uscita", "Operazioni", "Salva"]


def tab_inplay(cfg: dict, sel: pd.DataFrame) -> None:
    """Statistiche condizionate: quante partite vince la selezione in ogni situazione di gioco."""
    sport = cfg["sport"]
    base_cfg = {**cfg, "filters": {c: r for c, r in (cfg.get("filters") or {}).items() if c not in bt.INPLAY.get(sport, set())}}
    base = bt.apply_filters(sel, base_cfg)
    st.caption("Le operazioni della strategia **senza** i filtri in partita, divise per quello che succede durante il match. "
               "*Vince %* e l'intervallo al 95% sono conteggi esatti dallo storico. *Prob. live stimata* è quella che sconta la "
               "quota nel momento dell'evento (stima tarata sui risultati reali); se *vince %* sta stabilmente sopra, l'ingresso "
               "in quella situazione ha avuto valore. Per bancare: la selezione perde nel 100% meno *vince %*.")
    if base.empty:
        st.info("Nessuna operazione con i filtri attuali.")
        return
    key = json.dumps([base_cfg, db_version()], sort_keys=True, default=str)
    if ss.get("_ip_key") != key:
        with st.spinner("Calcolo le statistiche per situazione di gioco..."):
            ss["_ip"] = bt.conditional_stats(base, sport, cfg.get("side", "back"), float(cfg.get("live_margin", bt.LIVE_MARGIN)))
        ss["_ip_key"] = key
    stats = ss["_ip"]
    cols = {"partite": st.column_config.NumberColumn(format="%d"), "vinte": st.column_config.NumberColumn(format="%d"),
            "vince_pct": st.column_config.NumberColumn("vince %", format="%.1f"),
            "ic_basso": st.column_config.NumberColumn("IC 95% da", format="%.1f"), "ic_alto": st.column_config.NumberColumn("IC 95% a", format="%.1f"),
            "implicita_pre_pct": st.column_config.NumberColumn("prob. pre-partita %", format="%.1f"),
            "stimata_live_pct": st.column_config.NumberColumn("prob. live stimata %", format="%.1f"),
            "quota_pre_media": st.column_config.NumberColumn("quota pre-partita media", format="%.2f"),
            "quota_live_mediana": st.column_config.NumberColumn("quota live di entrata (mediana)", format="%.2f")}
    for title, table in stats.items():
        st.subheader(title)
        st.dataframe(table, hide_index=True, width="stretch", column_config=cols)
    if sport == "tennis":
        cov = int(base["ip_break1"].notna().sum()) if "ip_break1" in base.columns else 0
        st.caption(f"*Primo break*, *controbreak* e *sotto pressione*: disponibili per {num_it(cov)} operazioni su {num_it(len(base))}, "
                   "quelle dei match registrati punto per punto dal Match Charting Project (soprattutto tornei maggiori). Il primo "
                   "set è disponibile per tutti i match completati; un ritiro a metà primo set lascia il dato vuoto. Per *sotto "
                   "pressione = No* e *controbreak = Nessuno* non c'è un momento d'ingresso, quindi niente quota live.")
    else:
        st.caption("La squadra che segna per prima non è disponibile: la fonte non riporta i minuti dei gol e ricavarla solo "
                   "dalle partite in cui è deducibile falserebbe il campione. Il risultato all'intervallo è invece esatto.")
    st.caption("Per usare una situazione come strategia, sceglila in *Quando entri* nel riquadro Piano di trading.")


def exit_events(cfg: dict, method: str) -> list[str]:
    """Eventi di uscita proponibili: nel calcio solo gli esiti dei gruppi delle selezioni scelte."""
    sport = cfg["sport"]
    evs = co.valid_events(sport, method)
    if sport == "football" and method == "odds":
        sel = set(cfg.get("selection") or [])
        groups = (["goals"] if cfg["market"] == "ou25" else
                  (["team"] if not sel or sel - {"draw"} else []) + (["draw"] if not sel or "draw" in sel else []))
        keep = {"ht_" + k for g in groups for k in co.HT_GROUPS[g]}
        evs = [e for e in evs if e == "off" or e in keep]
    return evs


def exit_label(event: str, which: str, side: str) -> str:
    if event == "off":
        return "Nessuno"
    if event == "all":
        return "In ogni momento (break, controbreak, set)"
    if event == "ht":
        return "All'intervallo"
    lab = co.rule_label(event, which, side)
    return lab[0].upper() + lab[1:]


def exit_rule(cfg: dict, which: str, method: str, entry_cfg: dict) -> dict:
    """Una regola di uscita (Take Profit o Stop Loss): evento e quota (o soglia in %)."""
    side = cfg.get("side", "back")
    evs = exit_events(cfg, method)
    key = f"bt_co_{which}_event"
    ss.setdefault(key, "off")
    if ss[key] not in evs:
        ss[key] = "off"
    title = ("**Take Profit** · esci in positivo quando la selezione…" if which == "tp" else
             "**Stop Loss** · esci in negativo quando la selezione…")
    ev = st.segmented_control(title, evs, format_func=lambda e: exit_label(e, which, side), key=key) or "off"
    last = f"_last_{which}_event"
    if ss.get(last) not in (None, ev):
        ss[f"bt_co_{which}_quote"] = None           # quota pensata per un altro evento: si riparte da "stimata"
    ss[last] = ev
    out = {f"{which}_event": ev}
    if ev == "off":
        return out
    a, b = st.columns([1, 3])
    if method == "odds":
        qk = f"bt_co_{which}_quote"
        ss.setdefault(qk, None)
        q = a.number_input("Quota di uscita", min_value=1.01, max_value=1000.0, step=0.05, format="%.2f", key=qk,
                           placeholder="stimata",
                           help="Quota della selezione a cui chiudi quando succede l'evento. Il ROI si calcola dalla quota di "
                                "ingresso di ogni partita. Vuota: ogni partita esce alla quota stimata per lei in quel momento "
                                "(modello tarato, meno il margine), realistica anche con quote di ingresso molto diverse.")
        out[f"{which}_quote"] = float(q) if q else None
    else:
        ss.setdefault(f"bt_co_{which}", 30.0 if which == "tp" else 50.0)
        out[which] = float(a.number_input("Soglia (% di ROI)", 0.0, 500.0 if which == "tp" else 100.0, step=5.0, key=f"bt_co_{which}",
                                          help=("Esci se nel momento dell'evento la posizione vale almeno questo guadagno sull'unità "
                                                "rischiata. 0 = regola spenta.") if which == "tp" else
                                               ("Esci se nel momento dell'evento la posizione perde almeno questa parte dell'unità "
                                                "(25 = -0,25 u). 0 = regola spenta.")))
    what = {"break1": "Il primo break del primo set.",
            "cbreak": "Il controbreak è il break di chi aveva subito il primo break, in qualsiasi game successivo del primo set "
                      "(anche l'ultimo).",
            "nbreak": "Il prossimo break è il primo break dopo l'ingresso, in qualsiasi set."}
    if not co.entry_valid(entry_cfg, ev):
        b.warning("Questo evento non arriva dopo l'ingresso: con questo ingresso la regola non scatta mai.", icon=":material/block:")
    elif ev in co.POINT_DATA:
        b.caption(what[ev] + " Esiste solo nei match registrati punto per punto (circa uno su cinque, soprattutto tornei "
                  "maggiori): con questa regola il backtest usa solo quei match.")
    elif ev == "all":
        b.caption("Controlla la soglia al primo break, al controbreak (solo match punto per punto), a fine primo e secondo set, "
                  "nell'ordine in cui avvengono.")
    return out


def entry_cashout_panel(cfg: dict) -> tuple[dict, object]:
    """Riquadro principale: su chi e quando si entra e come si esce (Take Profit e Stop Loss separati).
    Ritorna la configurazione e un segnaposto per l'anteprima delle uscite (riempito dopo il backtest)."""
    sport, market = cfg["sport"], cfg["market"]
    events = event_mode(sport, market, cfg.get("selection"))
    opts = entry_options(sport, market, cfg.get("selection"))
    preview = None
    with st.container(border=True):
        st.markdown("#### Piano di trading: ingresso e uscita")
        ekey = f"bt_entry_{sport}_{market}"
        ss.setdefault(ekey, "pre")
        if ss[ekey] not in opts:
            ss[ekey] = "pre"
        entry = st.segmented_control("Su chi e quando entri" if events else "Quando entri", list(opts), format_func=opts.get,
                                     key=ekey) or "pre"
        if events and entry != "pre":
            st.caption(":material/swap_horiz: Nessun lato fisso nella *Selezione*: in ogni partita operi sul "
                       + ("giocatore" if sport == "tennis" else "la squadra") + " che vive l'evento scelto. Il range di quota del "
                       "pannello vale sulla sua quota pre-partita; *Punta* o *Banca* restano quelli scelti a sinistra.")
        if entry == "pressing":
            st.caption(":material/compress: *Mette sotto pressione*: l'avversario tiene due turni di servizio consecutivi dello "
                       "stesso set solo dopo palle break o vantaggi, cioè il giocatore spinge ma non riesce a breakkare. Si entra "
                       "alla fine del secondo turno, alla quota live stimata (stessa taratura di *sotto pressione*, vista dal lato "
                       "di chi spinge). Solo match registrati punto per punto.")
        if entry == "press":
            st.caption(":material/compress: *Sotto pressione*: il giocatore tiene due turni di servizio consecutivi dello stesso set "
                       "affrontando palle break o arrivando ai vantaggi (40-40) in entrambi. Si entra alla fine del secondo, alla "
                       "quota live stimata (tarata sui risultati reali: il favorito sotto pressione vale meno di quanto dica il "
                       "punteggio). Solo match registrati punto per punto.")
        filters = {k: v for k, v in cfg["filters"].items() if k not in bt.INPLAY.get(sport, set())}
        if entry in ENTRY_FILTERS:
            col, val = ENTRY_FILTERS[entry]
            if entry == "ht_draw" and events:
                col = "ip_ht_sel"                          # solo le due squadre, non il pareggio
            filters[col] = {"in": [val]}
        entry_cfg = {**cfg, "filters": filters}
        methods = {k: v for k, v in co.METHODS.items() if sport == "tennis" or k != "scalping"}
        ss.setdefault("bt_co_method", "odds")
        if ss["bt_co_method"] not in methods:
            ss["bt_co_method"] = "odds"
        method = st.segmented_control("Come esci", list(methods), format_func=methods.get, key="bt_co_method") or "odds"
        for k, v in (("tp", 30.0), ("sl", 50.0), ("margin_pct", 5.0), ("spread_pct", 0.5), ("decided_pct", 5.0)):
            ss.setdefault(f"bt_co_{k}", v)
        ss.setdefault("bt_co_who", "both")
        ss.setdefault("bt_co_stop_at_bp", False)
        out = {"method": method}
        if method == "scalping":
            c = st.columns([1, 1, 1, 1.3])
            out["tp"] = c[0].number_input("Take Profit (% di ROI)", 0.0, 500.0, step=0.5, key="bt_co_tp",
                                          help="Guadagno sull'importo rischiato a cui chiudi il game. 0 = disattivo.")
            out["sl"] = c[1].number_input("Stop Loss (% di ROI)", 0.0, 100.0, step=0.5, key="bt_co_sl",
                                          help="Perdita sull'importo rischiato a cui chiudi il game. 0 = disattivo.")
            out["spread_pct"] = c[2].number_input("Spread per lato (%)", 0.0, 5.0, step=0.1, key="bt_co_spread_pct",
                                                  help="Quanto peggio della quota teorica entri ed esci a ogni operazione.")
            out["who"] = c[3].radio("Servizio di", ["both", "player", "opponent"], horizontal=True, key="bt_co_who",
                                    format_func={"both": "Entrambi", "player": "Giocatore scelto", "opponent": "Avversario"}.get)
            d = st.columns([1.2, 1.2, 3])
            out["decided_pct"] = d[0].number_input("Salta se il match è deciso (%)", 1.0, 25.0, step=1.0, key="bt_co_decided_pct",
                                                   help="Non entra nei game in cui chi serve ha meno di questa probabilità di vincere il match, "
                                                        "o più di 100 meno questa: a match deciso non ci sono quote utili.")
            out["stop_at_bp"] = d[1].checkbox("Esci alla prima palla break", key="bt_co_stop_at_bp")
            d[2].caption("Un'operazione per ogni game di servizio dei match della strategia, entrando a 0-0 su chi serve. Esclusi "
                         "i primi due game della partita e i game a match ormai deciso. Si esce al Take Profit o allo Stop Loss "
                         "punto per punto (al prezzo del punto: può superare la soglia), altrimenti a fine game. Valori "
                         "tipici nella vista *Uscita*.")
            out["mode"] = "tpsl"
        else:
            out.update(exit_rule(cfg, "tp", method, entry_cfg))
            out.update(exit_rule(cfg, "sl", method, entry_cfg))
            active = [w for w in ("tp", "sl") if out.get(f"{w}_event", "off") != "off"]
            out["mode"] = "tpsl" if active else "off"
            needs_margin = method == "pct" or any(not out.get(f"{w}_quote") for w in active)
            if active:
                m1, m2 = st.columns([1, 3])
                if needs_margin:
                    out["margin_pct"] = m1.number_input("Margine all'uscita (%)", 0.0, 30.0, step=0.5, key="bt_co_margin_pct",
                                                        help="Quanto è peggiore la quota di uscita rispetto a quella equa stimata: "
                                                             "1-2% su un exchange, 5-10% con il cash out di un bookmaker.")
                m2.caption("Vale la regola che scatta per prima dopo l'ingresso; se nessuna scatta, la posizione resta aperta "
                           "fino alla fine. " + ("Quota vuota = quota stimata di ogni partita in quel momento, meno il margine."
                                                 if method == "odds" else
                                                 "Con le percentuali si esce al valore stimato nel momento dell'evento, che può "
                                                 "andare oltre la soglia."))
                preview = st.empty()
        live = {}
        if entry != "pre":
            with st.expander("Quota live di ingresso"):
                a, b, c2 = st.columns(3)
                ss.setdefault("bt_live_margin", bt.LIVE_MARGIN)
                live["live_margin"] = a.number_input("Margine sulle quote in partita (%)", 0.0, 15.0, step=0.5, key="bt_live_margin",
                                                     help="Quanto la quota live disponibile è peggiore di quella equa stimata. Su Betfair 1-3%, su un bookmaker 4-8%.")
                ss.setdefault("bt_live_override_on", False)
                on = b.toggle("Modifica manualmente quota live stimata", key="bt_live_override_on",
                              help="Sostituisce la quota live stimata con una quota fissa scelta da te, uguale per tutte le operazioni.")
                ss.setdefault("bt_live_override", 1.50)
                c2.number_input("Quota live di entrata", 1.01, 1000.0, step=0.05, key="bt_live_override", disabled=not on)
                live["live_odds_override"] = float(ss["bt_live_override"]) if on else None
                d1, d2, d3 = st.columns([1, 1, 2])
                ss.setdefault("bt_live_min", None)
                ss.setdefault("bt_live_max", None)
                live["live_odds_min"] = d1.number_input("Quota live min", 1.01, 1000.0, step=0.05, key="bt_live_min", placeholder="nessuna",
                                                        help="Entra solo se la quota live stimata in quel momento è almeno questa.")
                live["live_odds_max"] = d2.number_input("Quota live max", 1.01, 1000.0, step=0.05, key="bt_live_max", placeholder="nessuna",
                                                        help="Entra solo se la quota live stimata in quel momento è al massimo questa.")
                d3.caption("Il filtro usa la quota stimata nel momento dell'ingresso, nota in quel momento: non usa informazioni "
                           "future. Il range di quota del pannello laterale resta sulla quota pre-partita.")
    return {**cfg, "filters": filters, "cashout": out, **live}, preview


def exit_preview(slot, cfg: dict, trades: pd.DataFrame) -> None:
    """Sotto le regole: per ognuna quante partite vedono l'evento, la quota stimata dal modello e il ROI che ne risulta."""
    c = co.settings(cfg)
    if slot is None or trades.empty or c["method"] == "scalping" or "cashout" not in trades.columns:
        return
    side = cfg.get("side", "back")
    comm = float(cfg.get("commission") or 0.0)
    rows, warn = [], []
    for w in ("tp", "sl"):
        if not co.rule_active(c, w):
            continue
        ev = c[f"{w}_event"]
        est = co.rule_estimate(trades, cfg, w) if ev not in ("all", "ht") else {"n": np.nan, "est": np.nan, "entry": np.nan, "unreal_pct": np.nan}
        sel = trades["cashout"] == w
        real = float(trades.loc[sel, "pnl"].sum() / trades.loc[sel, "risk"].sum() * 100) if sel.any() else np.nan
        row = {"regola": "Take Profit" if w == "tp" else "Stop Loss", "quando": exit_label(ev, w, side),
               "partite con l'evento": est["n"], "uscite": int(sel.sum()),
               "quota di ingresso (mediana)": est["entry"], "quota stimata (mediana)": est["est"]}
        if c["method"] == "odds":
            q = c[f"{w}_quote"]
            row["quota di uscita"] = f"{q:.2f}" if q else "stimata"
            if q and est["unreal_pct"] == est["unreal_pct"] and est["unreal_pct"] > 20:
                warn.append(f"*{row['quando'].lower()}* a {q:.2f}: in {est['unreal_pct']:.0f}% delle partite rende oltre 10 punti "
                            "di ROI più della quota stimata per quella partita")
        else:
            row["soglia %"] = (c["tp"] if w == "tp" else -c["sl"])
        row["ROI medio all'uscita %"] = real
        rows.append(row)
    if not rows:
        return
    with slot.container():
        if warn:
            st.warning("Quote poco realistiche per una parte delle partite: " + "; ".join(warn) + ". Con quote di ingresso diverse "
                       "tra loro una quota fissa non vale per tutte: lascia la quota vuota (stimata per ogni partita) o restringi il "
                       "range di quota.", icon=":material/warning:")
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", column_config={
            "partite con l'evento": st.column_config.NumberColumn(format="%d"), "uscite": st.column_config.NumberColumn(format="%d"),
            "quota di ingresso (mediana)": st.column_config.NumberColumn(format="%.2f"),
            "quota stimata (mediana)": st.column_config.NumberColumn(format="%.2f"),
            "soglia %": st.column_config.NumberColumn(format="%+.0f"),
            "ROI medio all'uscita %": st.column_config.NumberColumn(format="%+.1f")})
        st.caption("*Partite con l'evento*: quelle in cui l'evento avviene davvero dopo l'ingresso, con l'esito richiesto (dato reale). "
                   "*Uscite*: quelle in cui questa regola è scattata per prima. *Quota stimata*: dove arriva in media la quota in quel "
                   "momento secondo il modello tarato (quota equa, senza margine).")


def with_adjustments(cfg: dict) -> dict:
    """Aggiunge alla strategia gli adjustment (anni e partite esclusi a mano), validi in tutte le viste."""
    years = sorted(int(y) for y in ss.get("bt_excl_years") or [])
    matches = [str(m) for m in ss.get("bt_excl_matches") or []]
    adj = {str(k): round(float(v), 2) for k, v in (ss.get("bt_odds_adj") or {}).items()}
    out = {k: v for k, v in cfg.items() if k not in ("exclude_years", "exclude_matches", "odds_adjust")}
    if years:
        out["exclude_years"] = years
    if matches:
        out["exclude_matches"] = matches
    if adj:
        out["odds_adjust"] = dict(sorted(adj.items()))
    return out


def adjustment_panel(cfg: dict, sel: pd.DataFrame) -> None:
    """Riquadro degli adjustment: anni e partite tolti dall'analisi, con ripristino."""
    years = sorted(int(y) for y in ss.get("bt_excl_years") or [])
    matches = [str(m) for m in ss.get("bt_excl_matches") or []]
    adj = dict(ss.get("bt_odds_adj") or {})
    label = "Adjustment: togli anni o partite, correggi le quote"
    if years or matches or adj:
        label = (f"Adjustment attivo · anni esclusi: {', '.join(map(str, years)) or 'nessuno'} · "
                 f"partite escluse: {len(matches)} · quote corrette: {len(adj)}")
    with st.expander(label, icon=":material/tune:", expanded=False):
        all_years = sorted(int(y) for y in sel["date"].dt.year.dropna().unique())
        a, b = st.columns([2, 1])
        new_years = a.multiselect("Anni da escludere", all_years, default=[y for y in years if y in all_years],
                                  placeholder="nessuno", key=f"adj_years_{'_'.join(map(str, years))}",
                                  help="Le operazioni di questi anni vengono tolte da tutte le viste (Riepilogo, Analisi, Robustezza, "
                                       "Uscita, Operazioni) e restano escluse anche nella strategia salvata. Puoi farlo anche "
                                       "dalla tabella per anno nella vista Analisi.")
        if sorted(new_years) != years:
            ss["bt_excl_years"] = sorted(new_years)
            st.rerun()
        if b.button("Ripristina tutto", icon=":material/restart_alt:", disabled=not (years or matches or adj), width="stretch"):
            ss["bt_excl_years"], ss["bt_excl_matches"], ss["bt_odds_adj"] = [], [], {}
            st.rerun()
        if adj:
            names = sel.assign(_k=bt.trade_key(sel)).drop_duplicates("_k").set_index("_k")
            fmt_a = lambda k: (f"{names.at[k, 'date']:%d/%m/%Y} · {names.at[k, 'event']} · "
                               f"{names.at[k, 'player'] if 'player' in names.columns else names.at[k, 'selection']} → {adj[k]:.2f}"
                               if k in names.index else f"{k} → {adj[k]:.2f}")
            keep_a = st.multiselect("Quote corrette a mano (togli la spunta per tornare alla quota stimata)", list(adj),
                                    default=list(adj), format_func=fmt_a, key=f"adj_odds_{len(adj)}")
            if len(keep_a) != len(adj):
                ss["bt_odds_adj"] = {k: adj[k] for k in keep_a}
                st.rerun()
        if matches:
            names = sel.drop_duplicates("match_id").set_index("match_id")
            fmt = lambda m: (f"{names.at[m, 'date']:%d/%m/%Y} · {names.at[m, 'event']}" if m in names.index else m)
            keep = st.multiselect("Partite escluse (togli la spunta per rimetterle)", matches, default=matches, format_func=fmt,
                                  key=f"adj_matches_{len(matches)}")
            if len(keep) != len(matches):
                ss["bt_excl_matches"] = keep
                st.rerun()
        else:
            st.caption("Per escludere una singola partita: vista *Operazioni*, clicca la riga e nella scheda della partita premi "
                       "*Escludi questa partita*.")


def page_backtester() -> None:
    header("Backtester")
    if need_db():
        return
    cfg, preview = entry_cashout_panel(sidebar_config())
    cfg = with_adjustments(cfg)
    args = (cfg["sport"], cfg["market"], cfg["price"], cfg["price_timing"])
    bank = float(ss.get("bt_bank", 100.0))
    key = json.dumps([cfg, bank, db_version()], sort_keys=True, default=str)
    scal = co.settings(cfg)["method"] == "scalping" and co.is_active(cfg)
    if ss.get("_bt_key") != key:  # ricalcola solo quando cambia la strategia, non a ogni cambio di vista
        ss["_bt_res"] = bt.backtest_selections(selections(*args, db_version()), cfg, bank, service_games(db_version()) if scal else None)
        ss["_bt_key"] = key
        ss.pop("_bt_scan", None)
    res = ss["_bt_res"]
    exit_preview(preview, cfg, res.trades)
    ss["last"] = {"cfg": cfg, "metrics": res.metrics, "trades": res.trades}
    if ss.get("_legacy_note"):
        st.info(ss.pop("_legacy_note"), icon=":material/info:")
    adjustment_panel(cfg, selections(*args, db_version()))
    st.caption(describe(cfg))
    if res.metrics["trades"] == 0:
        if scal:
            st.warning("Nessun game di servizio disponibile: lo scalping usa i match registrati punto per punto (circa uno su "
                       "cinque, soprattutto tornei maggiori). Se non li hai ancora scaricati: pagina **Dati**, *Aggiorna ora*.")
        else:
            st.warning("Nessun match soddisfa i filtri selezionati: allarga i criteri nel pannello laterale o premi *Azzera*.")
        return
    if ss.get("bt_view") not in VIEWS:
        ss["bt_view"] = "Uscita" if ss.get("bt_view") == "Cash out" else VIEWS[0]
    # un secondo clic sulla vista attiva la deseleziona: in quel caso resta aperta l'ultima vista scelta
    view = st.segmented_control("Vista", VIEWS, key="bt_view", label_visibility="collapsed") or ss.get("_bt_view_last", VIEWS[0])
    ss["_bt_view_last"] = view
    if view == "Riepilogo":
        tab_summary(cfg, res, bank)
    elif view == "Analisi":
        tab_analysis(cfg, res, args, bank)
    elif view == "Robustezza":
        tab_robustness(cfg, res, args, bank)
    elif view == "In partita":
        tab_inplay(cfg, selections(*args, db_version()))
    elif view == "Uscita":
        tab_cashout(cfg, res, args, bank)
    elif view == "Operazioni":
        tab_trades(cfg, res.trades)
    else:
        st.caption("Salva la strategia per ritrovarla nella pagina Strategie e usarla nello scanner delle partite in programma.")
        save_form("save_bt")


# --------------------------------------------------------------------------- pagina: oggi
@st.cache_resource(show_spinner="Preparo lo storico per i confronti...", max_entries=2)
def history(book: str, version: float) -> an.History:
    return an.History(selections("football", "1x2", book, "open", version), selections("football", "ou25", book, "open", version))


ODDS_API_STEPS = """**Come ottenere la chiave gratuita (2 minuti, una volta sola)**

1. Apri **the-odds-api.com** e premi *Get API Key* (o *Sign up*).
2. Scegli il piano **Starter / Free** (500 crediti al mese, nessuna carta di credito) e inserisci la tua email.
3. Ti arriva un'email con la **chiave**: una stringa di lettere e numeri.
4. Copiala e incollala nel campo *Chiave di The Odds API* qui accanto, poi premi il pulsante per scaricare.

La chiave resta salvata sul tuo Mac (in `data/settings.json`) e non va reinserita. Ogni torneo ATP scaricato consuma
1 credito: con 500 crediti puoi aggiornare le quote più volte al giorno. Quando i crediti finiscono, l'app lo dice e si
riparte il mese successivo."""


def today_tennis() -> None:
    cfg = fx.load_settings()
    a, b, c = st.columns([2.2, 1.2, 1.2])
    api_key = a.text_input("Chiave di The Odds API", value=cfg.get("odds_api_key", ""), type="password", key="today_key",
                           help="Gratuita (500 crediti al mese) registrandosi su the-odds-api.com. Ogni torneo scaricato costa 1 credito.")
    book = b.selectbox("Quote", ["avg", "max", "ps"], format_func=bt.BOOK_LABELS.get, key="today_tbook")
    c.markdown("<div style='height:1.75rem'></div>", unsafe_allow_html=True)
    if c.button("Scarica i match", type="primary", icon=":material/download:", width="stretch"):
        if api_key.strip() != cfg.get("odds_api_key", ""):
            fx.save_settings({"odds_api_key": api_key.strip()})
        with st.spinner("Scarico le quote ATP e calcolo le variabili..."):
            odds, msg = fx.fetch_tennis_odds(api_key, book)
            board = pd.DataFrame()
            if len(odds):
                with closing(bt.connect()) as con:
                    board = an.tennis_board(odds, con, sv.load_vault())
        ss["today_tennis"] = (board, msg, pd.Timestamp.now().strftime("%d/%m %H:%M"))
    with st.expander("Non hai ancora la chiave? Ecco cosa fare"):
        st.markdown(ODDS_API_STEPS)
    st.caption("Per il tennis non esiste una fonte gratuita di quote senza registrazione (ne ho cercate, comprese quelle di "
               "tipo moneyway): serve questa chiave personale gratuita. Ranking, form, precedenti e modello Elo vengono dallo storico.")
    if "today_tennis" not in ss:
        return
    board, msg, when = ss["today_tennis"]
    if msg:
        (st.caption if len(board) else st.warning)(msg)
    if board.empty:
        return
    kpis([("Match", str(len(board)), f"aggiornati alle {when}", None),
          ("Con segnale", str(int((board["segnali"] != "").sum())), "strategie salvate attivate", None),
          ("Modello oltre la quota", str(int((board["scarto modello"].abs() >= 5).sum())), "scarto di almeno 5 punti, in un senso o nell'altro", None)])
    st.dataframe(board, hide_index=True, width="stretch", height=460, column_config={
        "quota 1": st.column_config.NumberColumn(format="%.2f"), "quota 2": st.column_config.NumberColumn(format="%.2f"),
        "rank 1": st.column_config.NumberColumn(format="%.0f"), "rank 2": st.column_config.NumberColumn(format="%.0f"),
        "form 1": st.column_config.NumberColumn("form 1 %", format="%.0f"), "form 2": st.column_config.NumberColumn("form 2 %", format="%.0f"),
        "implicita 1 %": st.column_config.NumberColumn(format="%.1f"), "modello 1 %": st.column_config.NumberColumn(format="%.1f"),
        "scarto modello": st.column_config.NumberColumn("scarto modello (punti %)", format="%+.1f"), "segnali": "strategie attivate"})
    st.caption("**Scarto modello**: probabilità Elo del giocatore 1 meno quella implicita nella quota. Nello storico il mercato è più "
               "preciso del modello: uno scarto ampio segnala più spesso un'informazione che il modello non ha (infortunio, "
               "stanchezza) che un'occasione.")


def page_today() -> None:
    header("Oggi", "Le partite in programma analizzate con lo storico: variabili pre-partita, precedenti a quote simili, segnali delle tue strategie.")
    if need_db():
        return
    sport = st.segmented_control("Sport", ["football", "tennis"], format_func=SPORT_LABEL.get, default="football",
                                 label_visibility="collapsed", key="today_sport") or "football"
    if sport == "tennis":
        today_tennis()
        return
    a, b, c, d = st.columns([1.3, 1.1, 1.2, 3])
    book = a.selectbox("Quote", ["avg", "max", "b365", "bfe"], format_func=bt.BOOK_LABELS.get, key="today_book")
    min_n = b.number_input("Precedenti minimi", 50, 2000, 150, 50, key="today_min",
                           help="Sotto questa numerosità lo scarto storico non viene considerato.")
    c.markdown("<div style='height:1.75rem'></div>", unsafe_allow_html=True)
    refresh = c.button("Aggiorna partite", icon=":material/refresh:", width="stretch")
    vault = sv.load_vault()
    key = json.dumps([book, int(min_n), db_version(), sorted(vault)], default=str)
    if refresh or "today" not in ss or ss["today"]["key"] != key:
        with st.spinner("Scarico e analizzo le partite in programma..."):
            fixtures, msg = fx.fetch_football_fixtures(book)
            board, detail = pd.DataFrame(), pd.DataFrame()
            external, ext_msg = fx.fetch_clubelo() if len(fixtures) else (pd.DataFrame(), "")
            if len(fixtures):
                with closing(bt.connect()) as con:
                    board, detail = an.daily_board(fixtures, con, history(book, db_version()), vault, int(min_n), external)
        ss["today"] = {"key": key, "board": board, "detail": detail, "fixtures": fixtures, "msg": msg, "ext": len(external), "ext_msg": ext_msg,
                       "when": pd.Timestamp.now().strftime("%d/%m %H:%M")}
    t = ss["today"]
    if t["msg"]:
        st.warning(t["msg"])
    board, detail = t["board"], t["detail"]
    if board.empty:
        st.info("Nessuna partita in programma disponibile in questo momento. La fonte (football-data.co.uk) pubblica le partite "
                "dei prossimi giorni: riprova più tardi con *Aggiorna partite*.")
        return
    covered_ext = int(detail.loc[detail["selection"] == "home", "clubelo_pct"].notna().sum()) if "clubelo_pct" in detail else 0
    d.caption(f"{len(board)} partite, aggiornate alle {t['when']}. Fonte: football-data.co.uk. Incrocio esterno ClubElo: "
              + (f"{covered_ext} partite abbinate." if t.get("ext") else "non disponibile ora (" + (t.get("ext_msg") or "nessun dato") + ")"))
    f1, f2, f3 = st.columns([2, 2, 1.4])
    days = sorted(board["data"].dropna().unique())
    day = f1.multiselect("Giorno", days, placeholder="tutti")
    leagues = sorted(board["lega"].dropna().unique())
    covered = [x for x in leagues if x in {v[1] for v in C.FOOTBALL_LEAGUES.values()}]
    league = f2.multiselect("Lega", leagues, placeholder="tutte")
    only = f3.radio("Mostra", ["Tutte", "Con segnale", "Scarto di almeno 3 punti"], horizontal=False, label_visibility="collapsed")
    view = board
    if day:
        view = view[view["data"].isin(day)]
    if league:
        view = view[view["lega"].isin(league)]
    if only == "Con segnale":
        view = view[view["segnali"] != ""]
    elif only == "Scarto di almeno 3 punti":
        view = view[view["scarto_pct"] >= 3]
    kpis([("Partite", str(len(view)), f"{len(covered)} leghe coperte dallo storico", None),
          ("Con segnale", str(int((view["segnali"] != "").sum())), f"{len(vault)} strategie salvate", None),
          ("Scarto di almeno 3 punti", str(int((view["scarto_pct"] >= 3).sum())), f"con almeno {int(min_n)} precedenti", None)])
    st.dataframe(view.drop(columns=["row"]).sort_values(["data", "ora"], kind="stable"), hide_index=True, width="stretch", height=380, column_config={
        "1": st.column_config.NumberColumn(format="%.2f"), "X": st.column_config.NumberColumn(format="%.2f"),
        "2": st.column_config.NumberColumn(format="%.2f"), "over": st.column_config.NumberColumn("over 2.5", format="%.2f"),
        "pos_casa": st.column_config.NumberColumn("pos. casa", format="%.0f"), "pos_ospite": st.column_config.NumberColumn("pos. ospite", format="%.0f"),
        "form_casa": st.column_config.NumberColumn("punti/partita casa", format="%.1f"),
        "form_ospite": st.column_config.NumberColumn("punti/partita ospite", format="%.1f"),
        "gol_recenti": st.column_config.NumberColumn("gol recenti", format="%.1f"), "esito_migliore": "esito migliore",
        "scarto_pct": st.column_config.NumberColumn("scarto storico (punti %)", format="%+.1f"),
        "precedenti": st.column_config.NumberColumn(format="%.0f"), "segnali": "strategie attivate",
        "incrocio": st.column_config.TextColumn("incrocio modelli", help="Esito su cui il modello Elo interno e ClubElo vedono entrambi "
                                                "almeno 3 punti di probabilità in più della quota (scarti interno / esterno).")})
    st.caption("**Incrocio modelli**: esito 1X2 su cui il modello Elo interno e la fonte esterna ClubElo concordano nel dare più "
               "probabilità di quanta ne implichi la quota. Nello storico, seguire il solo modello contro la quota non ha reso: "
               "usalo come secondo parere su una strategia già validata, non come segnale a sé.")
    st.caption("**Scarto storico**: frequenza con cui quell'esito si è verificato nei precedenti a quota simile (±7%, stessa lega quando "
               "i precedenti bastano) meno la probabilità implicita nella quota senza margine. Uno scarto positivo è un indizio, non un "
               "vantaggio: il mercato è efficiente e scarti di pochi punti rientrano nel caso. Conta quando coincide con una tua "
               "strategia validata.")
    if view.empty:
        return
    st.subheader("Scheda partita")
    labels = {int(r.row): f"{r.data} {r.ora or ''}  {r.partita}  ({r.lega})" for r in view.itertuples()}
    row = st.selectbox("Partita", list(labels), format_func=labels.get, label_visibility="collapsed")
    m = board[board["row"] == row].iloc[0]
    dm = detail[detail["row"] == row]
    kpis([(f"Esito {r.esito}", f"{r.quota:.2f}" if pd.notna(r.quota) else "n/d",
           (f"storica {r.storica_pct:.0f}% contro {r.implicita_pct:.0f}% ({r.scarto_pct:+.1f}), {int(r.precedenti)} prec."
            if r.precedenti >= min_n and pd.notna(r.scarto_pct) else "precedenti insufficienti"),
           sign_tone(r.scarto_pct) if r.precedenti >= min_n else None) for r in dm.itertuples()])
    if m["segnali"]:
        st.success(f"Attiva le strategie: {m['segnali']}", icon=":material/bolt:")
    home, away = t["fixtures"].loc[row, "home_team"], t["fixtures"].loc[row, "away_team"]
    with closing(bt.connect()) as con:
        feats = sv.football_upload_selections(t["fixtures"].loc[[row]], "1x2", con).iloc[0]
        last_home, last_away = an.team_recent(con, home), an.team_recent(con, away)
    rows = [("Posizione in classifica", "pos", "{:.0f}"), ("Punti a partita in stagione", "sppg", "{:.2f}"),
            ("Punti a partita, ultime 5", "ppg5", "{:.1f}"), ("Punti a partita, ultime 5 in casa / fuori", "venue_ppg5", "{:.1f}"),
            ("Gol fatti, media ultime 5", "gf5", "{:.1f}"), ("Gol subiti, media ultime 5", "ga5", "{:.1f}"),
            ("Tiri in porta fatti, media ultime 5", "sot5", "{:.1f}"), ("Tiri in porta subiti, media ultime 5", "sota5", "{:.1f}"),
            ("Calci d'angolo, media ultime 5", "corners5", "{:.1f}"), ("Giorni di riposo", "rest_days", "{:.0f}")]
    x, y, z = st.columns([2.2, 1.4, 1.4])
    x.dataframe(pd.DataFrame({"variabile": [r[0] for r in rows], home: [fmt(float(feats[f"home_{r[1]}"]), r[2]) for r in rows],
                              away: [fmt(float(feats[f"away_{r[1]}"]), r[2]) for r in rows]}), hide_index=True, width="stretch")
    y.caption(f"Ultime partite: {home}")
    y.dataframe(last_home, hide_index=True, width="stretch")
    z.caption(f"Ultime partite: {away}")
    z.dataframe(last_away, hide_index=True, width="stretch")
    st.dataframe(dm.drop(columns=["row", "selection"]), hide_index=True, width="stretch", column_config={
        "quota": st.column_config.NumberColumn(format="%.2f"), "implicita_pct": st.column_config.NumberColumn("prob. implicita %", format="%.1f"),
        "modello_pct": st.column_config.NumberColumn("modello Elo %", format="%.1f"),
        "clubelo_pct": st.column_config.NumberColumn("ClubElo %", format="%.1f"),
        "storica_pct": st.column_config.NumberColumn("frequenza storica %", format="%.1f"),
        "scarto_pct": st.column_config.NumberColumn("scarto (punti %)", format="%+.1f"),
        "roi_storico_pct": st.column_config.NumberColumn("ROI storico a quote simili %", format="%+.1f"), "ambito": "precedenti da"})


# --------------------------------------------------------------------------- pagina: scalping
@st.cache_resource(show_spinner="Carico i game di servizio...", max_entries=1)
def service_games(version: float) -> pd.DataFrame:
    with closing(bt.connect()) as con:
        return sc.load_games(con)


def page_scalping() -> None:
    header("Scalping sul servizio", "Punti chi serve a un dato punteggio e chiudi dopo pochi punti: simulazione su game reali punto per punto.")
    if need_db():
        return
    games = service_games(db_version())
    if games.empty:
        st.info("I dati punto per punto non sono ancora stati scaricati. Vai nella pagina **Dati** e premi *Aggiorna ora*: "
                "la prima volta scarica circa 90 MB dal Match Charting Project (qualche minuto).")
        return
    tennis_vault = {n: e for n, e in sv.load_vault().items() if e.get("config", {}).get("sport") == "tennis"}
    with st.container(border=True):
        c = st.columns([2.2, 1.8, 2])
        strat = c[0].selectbox("Partite di una strategia salvata", list(tennis_vault), index=None, placeholder="nessuna: tutti i match",
                               help="Limita la simulazione ai match in cui la strategia avrebbe operato.")
        who = c[1].radio("Chi serve", ["player", "opponent", "both"], horizontal=True, disabled=strat is None,
                         format_func={"player": "Il giocatore scelto", "opponent": "L'avversario", "both": "Entrambi"}.get)
        after = c[2].checkbox("Solo game dopo l'ingresso della strategia", value=False, disabled=strat is None,
                              help="Per strategie con filtri in partita: solo i game giocati dopo il primo break o dopo il primo set.")
        if strat is not None:
            scfg = tennis_vault[strat]["config"]
            res = bt.backtest_selections(selections("tennis", "h2h", scfg.get("price", "avg"), "open", db_version()), scfg)
            games, info = sc.strategy_games(games, res.trades, who)
            point = bt.entry_point(scfg)
            if after and point and len(games):
                fbg = res.trades.drop_duplicates("match_id").set_index("match_id")["_fb_game"] if "_fb_game" in res.trades else pd.Series(dtype=float)
                cut = games["db_match_id"].map(fbg) if point == "break1" else pd.Series(np.nan, index=games.index)
                games = games[(games["set_no"] >= 2) | (cut.notna() & (games["game_no"] > cut))]
            st.caption(f"Strategia **{strat}**: {num_it(info['strategy_matches'])} match nel backtest, {num_it(info['with_points'])} con dati "
                       f"punto per punto ({info['with_points'] / max(info['strategy_matches'], 1) * 100:.0f}%)"
                       + (f", {info['unmatched_names']} esclusi per nomi non abbinati" if info["unmatched_names"] else "")
                       + f". Game selezionati: {num_it(len(games))}. " + describe(scfg))
            if games.empty:
                st.warning("Nessun game disponibile per questa strategia: il Match Charting Project copre circa un match ATP su cinque, "
                           "soprattutto nei tornei maggiori.")
                return
    with st.container(border=True):
        c = st.columns([1.1, 1.5, 1, 1.4, 1, 1])
        entry = c[0].selectbox("Ingresso al punteggio", sc.ENTRY_SCORES, help="Punteggio del game dal punto di vista di chi serve (battitore-risposta).")
        mode = c[1].radio("Uscita", ["points", "game"], horizontal=True, format_func={"points": "Dopo N punti", "game": "A fine game"}.get)
        n_pts = c[2].number_input("N punti", 1, 6, 2, 1, disabled=mode != "points")
        stop = c[3].checkbox("Esci alla prima palla break", value=False, help="Chiude in perdita appena chi risponde è a un punto dal break.")
        spread = c[4].number_input("Spread %", 0.0, 5.0, 0.5, 0.1, help="Quanto peggio della quota teorica entri ed esci: un tick vale circa 0,5-1%.")
        comm = c[5].number_input("Commissione %", 0.0, 20.0, 2.0, 0.5)
        c = st.columns([1.6, 1.4, 2, 1.4])
        surf = c[0].multiselect("Superficie", sorted(games["surface"].unique()), placeholder="tutte")
        sets = c[1].multiselect("Set", sorted(int(x) for x in games["set_no"].unique()), placeholder="tutti")
        lo, hi = c[2].slider("Probabilità pre-match di chi serve (%)", 0, 100, (0, 100), 5)
        real = c[3].checkbox("Solo match con quote reali", value=False, help="Esclude i match dove il prezzo di partenza viene dall'Elo.")
    filters = {"surface": surf, "set_no": sets, "pre_prob": (lo / 100 if lo > 0 else None, hi / 100 if hi < 100 else None),
               "price_source": ["quote"] if real else None}
    exit_points = int(n_pts) if mode == "points" else None
    key = json.dumps([entry, exit_points, stop, spread, comm, filters, db_version(), strat, who, after], default=str)
    if ss.get("_sc_key") != key:
        ss["_sc"] = sc.simulate(games, entry, exit_points, stop, spread, comm / 100, filters)
        ss["_sc_key"] = key
    t = ss["_sc"]
    if t.empty:
        st.warning("Nessun game soddisfa i criteri scelti.")
        return
    m = bt.compute_metrics(t, 100.0)
    r = t["pnl"].to_numpy()
    kpis([("Rendimento per operazione", f"{r.mean() * 100:+.3f}%", f"profitto totale {m['profit']:+.1f} u su {num_it(m['trades'])} operazioni", sign_tone(r.mean())),
          ("Operazioni vinte", f"{m['win_rate_pct']:.1f}%", f"t-stat {fmt(m['t_stat'])}", None),
          ("Vincita media", f"{r[r > 0].mean() * 100:+.2f}%" if (r > 0).any() else "n/d", "sulle operazioni chiuse in utile", None),
          ("Perdita media", f"{r[r < 0].mean() * 100:.2f}%" if (r < 0).any() else "n/d", "sulle operazioni chiuse in perdita", None),
          ("Max drawdown", f"-{m['max_dd_units']:.1f} u", f"serie negativa max {m['longest_losing_streak']}", None)],
         hero_spark=t["equity"].to_numpy())
    st.caption(f"{num_it(games['match_id'].nunique())} match e {num_it(len(games))} game di servizio dal {games['match_date'].min():%Y}. "
               "Ogni operazione punta 1 unità su chi serve nel mercato vincente match; il rendimento è la variazione della quota tra "
               "ingresso e uscita, meno spread e commissione.")
    show(equity_chart(t).update_layout(title_text="Profitto cumulato (unità puntate)"))
    st.subheader("Per superficie")
    by = t.groupby("surface")["pnl"].agg(operazioni="size", roi_pct=lambda x: x.mean() * 100, vinte_pct=lambda x: (x > 0).mean() * 100,
                                         vincita_media_pct=lambda x: x[x > 0].mean() * 100, perdita_media_pct=lambda x: x[x < 0].mean() * 100).reset_index()
    pct = lambda label: st.column_config.NumberColumn(label, format="%+.2f")
    cols = {"surface": "superficie", "roi_pct": st.column_config.NumberColumn("rendimento per operazione %", format="%+.3f"),
            "vinte_pct": st.column_config.NumberColumn("vinte %", format="%.1f"), "vincita_media_pct": pct("vincita media %"),
            "perdita_media_pct": pct("perdita media %"), "t_stat": st.column_config.NumberColumn("t-stat", format="%+.1f")}
    st.dataframe(by, hide_index=True, width="stretch", column_config=cols)
    st.dataframe(t.groupby("exit")["pnl"].agg(operazioni="size", roi_pct=lambda x: x.mean() * 100).reset_index().rename(columns={"exit": "uscita"}),
                 hide_index=True, width="stretch", column_config=cols)

    st.subheader("Dove rende: tutti i punteggi di ingresso")
    if st.toggle("Confronta tutti i punteggi di ingresso con la stessa regola di uscita", help="Qualche secondo di calcolo."):
        gkey = json.dumps([exit_points, stop, spread, comm, filters, db_version()], default=str)
        if ss.get("_scg_key") != gkey:
            with st.spinner("Simulo ogni punteggio di ingresso..."):
                ss["_scg"] = sc.entry_grid(games, exit_points, stop, spread, comm / 100, filters)
            ss["_scg_key"] = gkey
        st.dataframe(ss["_scg"].sort_values("roi_pct", ascending=False), hide_index=True, width="stretch", height=420, column_config=cols)
    st.subheader("Punti reali contro modello")
    stats = sc.state_stats(games if not surf else games[games["surface"].isin(surf)])
    st.dataframe(stats, hide_index=True, width="stretch", height=380, column_config={
        "game": st.column_config.NumberColumn("game passati dal punteggio"), "tenuti_pct": st.column_config.NumberColumn("servizio tenuto %", format="%.1f"),
        "modello_pct": st.column_config.NumberColumn("previsto dal modello %", format="%.1f"),
        "scarto": st.column_config.NumberColumn("scarto (punti %)", format="%+.1f")})
    st.caption("Da ogni punteggio: quante volte chi serve ha davvero tenuto il servizio contro quanto prevede il modello. Scarto negativo: "
               "da lì chi serve fa peggio del previsto, quindi puntarlo è sfavorevole; positivo: fa meglio.")
    st.caption(":material/warning: Gli esiti dei punti sono reali, le quote no: in mancanza di quote in diretta gratuite, il prezzo a ogni "
               "punteggio viene da un modello tarato sulla quota pre-match e sulla quota di servizi tenuti di ogni superficie. La "
               "simulazione mostra quindi dove i punti reali si scostano dal modello e quanto pesano spread e commissione, non come "
               "si muoveva il mercato vero né se c'era liquidità. Rendimenti di pochi decimi di punto per operazione sono dello stesso "
               "ordine dello spread.")
    with st.expander("Operazioni"):
        view = t[["date", "tournament", "surface", "event", "set_no", "game_no", "odds", "exit_odds", "points", "exit", "held", "pnl"]].copy()
        view["date"] = view["date"].dt.date
        st.dataframe(view.tail(MAX_TABLE_ROWS), hide_index=True, width="stretch", height=360, column_config={
            "odds": st.column_config.NumberColumn("quota ingresso", format="%.3f"), "exit_odds": st.column_config.NumberColumn("quota uscita", format="%.3f"),
            "pnl": st.column_config.NumberColumn("rendimento", format="%+.4f"), "held": "servizio tenuto", "exit": "uscita", "points": "punti"})
        st.download_button("Esporta tutte le operazioni (CSV)", view.to_csv(index=False).encode("utf-8"),
                           f"scalping_{date.today():%Y%m%d}.csv", "text/csv", icon=":material/download:")


# --------------------------------------------------------------------------- pagina: quote reali (Betfair)
def page_live() -> None:
    header("Quote reali", "Cash out simulato sui prezzi veri in corso di partita, dai dati storici di Betfair Exchange.")
    if ss.get("_flash"):
        st.success(ss.pop("_flash"))
    with st.expander("Importa i dati di Betfair", expanded=not bf.DB_PATH.exists()):
        st.markdown("1. Vai su **historicdata.betfair.com** con il tuo account Betfair, sezione *My Data*.\n"
                    "2. Scegli lo sport (*Tennis* o *Soccer*), il piano **BASIC** (gratuito), il periodo e il mercato **Match Odds**.\n"
                    "3. Scarica l'archivio `.tar` e indica qui sotto dove l'hai salvato (va bene anche una cartella con più archivi).")
        a, b = st.columns([4, 1.2])
        where = a.text_input("File o cartella sul Mac", placeholder="es. ~/Downloads/data.tar", label_visibility="collapsed")
        if b.button("Importa", type="primary", icon=":material/upload:", width="stretch"):
            target = Path(where).expanduser() if where.strip() else None
            if target is None or not target.exists():
                st.warning("Percorso non trovato: controlla il nome del file o della cartella.")
            else:
                with st.spinner("Leggo i mercati (può richiedere qualche minuto per archivi grandi)..."):
                    stats = bf.import_path(target)
                _flash(f"{stats['imported']} mercati importati, {stats['skipped']} già presenti, {stats['ignored']} ignorati "
                       f"(non Match Odds, non conclusi o senza fase in-play) su {stats['files']} file.")
        st.caption("Il piano BASIC riporta l'ultimo prezzo scambiato circa una volta al minuto: basta per il cash out, non per "
                   "lo scalping punto per punto. I dati importati restano in `data/betfair.duckdb`.")
    runners, prices = bf.load()
    if runners.empty:
        st.info("Nessun dato importato. Finché non importi i dati di Betfair, il cash out del Backtester usa il modello "
                "stimato (intervallo / primo set); qui potrai verificarlo sui prezzi reali.")
        return
    with st.container(border=True):
        c = st.columns([1.3, 1.6, 1, 1, 1])
        sport = c[0].multiselect("Sport", sorted(runners["sport"].unique()), format_func=SPORT_LABEL.get, placeholder="tutti")
        role = c[1].multiselect("Selezione", ["favourite", "underdog", "draw"], default=["favourite"],
                                format_func={"favourite": "Favorito", "underdog": "Sfavorito", "draw": "Pareggio"}.get)
        side = c[2].radio("Modalità", ["back", "lay"], format_func={"back": "Back", "lay": "Lay"}.get, horizontal=True)
        omin = c[3].number_input("Quota min", 1.01, 1000.0, 1.01, 0.05)
        omax = c[4].number_input("Quota max", 1.01, 1000.0, 50.0, 0.05)
        c = st.columns([1.2, 1, 1.2, 1, 1.4, 1, 1])
        use_tp = c[0].checkbox("Take Profit", value=True, help="Chiude in utile alla prima rilevazione in cui il cash out vale almeno la soglia.")
        tp = c[1].number_input("soglia +%", 1.0, 500.0, 30.0, 5.0, disabled=not use_tp)
        use_sl = c[2].checkbox("Stop Loss", value=False, help="Chiude in perdita alla prima rilevazione in cui il cash out scende sotto la soglia.")
        sl = c[3].number_input("soglia -%", 1.0, 99.0, 50.0, 5.0, disabled=not use_sl)
        minutes = c[4].number_input("Esci dopo minuti (0 = mai)", 0, 300, 0, 5)
        spread = c[5].number_input("Spread %", 0.0, 5.0, 0.5, 0.1)
        comm = c[6].number_input("Commissione %", 0.0, 20.0, 2.0, 0.5)
    filters = {"sport": sport, "role": role, "odds_min": omin, "odds_max": omax}
    t = bf.simulate(runners, prices, side, tp if use_tp else None, sl if use_sl else None, minutes or None, spread, comm / 100, filters)
    if t.empty:
        st.warning("Nessuna selezione soddisfa i criteri.")
        return
    hold = t.assign(pnl=t["pnl_hold"])
    hold["equity"] = hold["pnl"].cumsum()
    m, mh = bt.compute_metrics(t, 100.0), bt.compute_metrics(hold, 100.0)
    closed = int((t["cashout"] != "").sum())
    kpis([("ROI con cash out", f"{m['roi_pct']:+.2f}%", f"tenendo fino alla fine: {mh['roi_pct']:+.2f}%", sign_tone(m["roi_pct"])),
          ("Effetto sul profitto", f"{m['profit'] - mh['profit']:+.2f} u", f"su {num_it(m['trades'])} operazioni", sign_tone(m["profit"] - mh["profit"])),
          ("Chiuse in anticipo", f"{closed / len(t) * 100:.0f}%", f"{num_it(closed)} operazioni", None),
          ("Max drawdown", f"-{m['max_dd_units']:.1f} u", f"tenendo: -{mh['max_dd_units']:.1f} u", "good" if m["max_dd_units"] < mh["max_dd_units"] else None),
          ("Mercati importati", num_it(runners["market_id"].nunique()), f"{runners['n_prices'].mean():.0f} prezzi in media per selezione", None)],
         hero_spark=t["equity"].to_numpy())
    show(equity_chart(t, by_trade=True).update_layout(title_text="Profitto cumulato con cash out (unità), per operazione"))
    labels = {"": "Tenuta fino alla fine", "tp": "Take profit", "sl": "Stop loss", "time": "Chiusa a tempo"}
    by = t.groupby("cashout").agg(operazioni=("pnl", "size"), profitto=("pnl", "sum"), tenendole=("pnl_hold", "sum"),
                                  minuto_medio=("exit_minute", "mean")).reset_index()
    by["cashout"] = by["cashout"].map(labels)
    st.subheader("Per tipo di chiusura")
    st.dataframe(by.rename(columns={"cashout": "esito"}), hide_index=True, width="stretch", column_config={
        "profitto": st.column_config.NumberColumn("profitto (u)", format="%+.2f"), "tenendole": st.column_config.NumberColumn("tenendole (u)", format="%+.2f"),
        "minuto_medio": st.column_config.NumberColumn("minuto medio di chiusura", format="%.0f")})
    st.subheader("Confronto delle soglie")
    if st.toggle("Prova tutte le combinazioni di incasso e taglio", help="Qualche secondo di calcolo."):
        grid = bf.threshold_grid(runners, prices, side, spread, comm / 100, filters)
        st.dataframe(grid.sort_values("roi_pct", ascending=False), hide_index=True, width="stretch", height=420, column_config={
            "incassa_da": "take profit da", "taglia_da": "stop loss da", "chiuse_pct": st.column_config.NumberColumn("chiuse %", format="%.0f"),
            "roi_pct": st.column_config.NumberColumn("ROI %", format="%+.2f"), "profit": st.column_config.NumberColumn("profitto (u)", format="%+.1f"),
            "max_dd": st.column_config.NumberColumn("max drawdown (u)", format="%.1f")})
    st.caption("Ingresso all'ultimo prezzo scambiato prima dell'inizio; il cash out scatta alla prima rilevazione in cui il suo "
               "valore supera la soglia. Con una rilevazione al minuto il prezzo di uscita reale può essere stato migliore o "
               "peggiore di quello registrato. Pochi mercati importati danno risultati molto variabili: servono centinaia di "
               "partite prima di trarre conclusioni.")


# --------------------------------------------------------------------------- pagina: rischio
def page_risk() -> None:
    header("Rischio", "Simulazione Monte Carlo del bankroll sull'ultima strategia testata.")
    last = ss.get("last")
    if not last or last["metrics"]["trades"] == 0:
        st.info("Esegui prima un backtest nella pagina **Backtester**: la simulazione usa i suoi risultati.")
        return
    m, trades = last["metrics"], last["trades"]
    st.caption(describe(last["cfg"]))
    st.caption(f"Base storica: {num_it(m['trades'])} operazioni, ROI {m['roi_pct']:+.2f}%, win rate {m['win_rate_pct']:.1f}%.")
    if m["trades"] < 100:
        st.warning("Meno di 100 operazioni storiche: la simulazione è poco affidabile.")

    with st.container(border=True):
        c = st.columns(6)
        n_trades = c[0].radio("Operazioni future", [500, 1000], horizontal=True)
        bankroll = c[1].number_input("Bankroll iniziale", 10.0, 1e9, 1000.0, 100.0)
        stake_pct = c[2].number_input("Stake fisso (% banca iniziale)", 0.1, 25.0, 1.0, 0.1)
        kf = c[3].selectbox("Frazione di Kelly", [0.125, 0.25, 0.5, 1.0], index=1,
                            format_func={0.125: "1/8 Kelly", 0.25: "1/4 Kelly", 0.5: "1/2 Kelly", 1.0: "Kelly pieno"}.get)
        cap = c[4].number_input("Tetto stake Kelly (%)", 0.5, 50.0, 5.0, 0.5)
        ruin = c[5].number_input("Soglia di rovina (% banca)", 0.0, 90.0, 10.0, 5.0,
                                 help="Il percorso è considerato rovinato quando il bankroll scende a questa percentuale del valore iniziale.")

    r, b = risk.trade_returns(trades)
    common = dict(n_sims=1000, n_trades=int(n_trades), bankroll=bankroll, ruin_pct=ruin, seed=42)
    flat = risk.simulate(r, b, "flat", stake_pct=stake_pct, **common)
    kelly = risk.simulate(r, b, "kelly", kelly_fraction=kf, kelly_cap_pct=cap, **common)
    if m["roi_pct"] <= 0:
        st.warning("La strategia ha ROI storico non positivo: Kelly non punta (stake 0) e lo stake fisso perde in media.")

    kpis([("Risk of Ruin - stake fisso", f"{flat['risk_of_ruin_pct']:.1f}%", f"stake {stake_pct:.1f}% della banca iniziale", None),
          ("Risk of Ruin - Kelly", f"{kelly['risk_of_ruin_pct']:.1f}%", f"stake medio {kelly['mean_stake_pct']:.2f}% della banca", None),
          ("Mediana finale - stake fisso", num_it(flat["median_final"]), f"in utile nel {flat['prob_profit_pct']:.0f}% dei casi",
           sign_tone(flat["median_final"] - bankroll)),
          ("Mediana finale - Kelly", num_it(kelly["median_final"]), f"in utile nel {kelly['prob_profit_pct']:.0f}% dei casi",
           sign_tone(kelly["median_final"] - bankroll))])
    pct = lambda v: f"{v:.1f}%"
    summary = pd.DataFrame({
        "metrica": ["Risk of Ruin", "Probabilità di chiudere in utile", "Bankroll finale mediano", "Bankroll finale 5° percentile",
                    "Bankroll finale 95° percentile", "Max drawdown mediano", "Max drawdown 95° percentile"],
        "stake fisso": [pct(flat["risk_of_ruin_pct"]), pct(flat["prob_profit_pct"]), num_it(flat["median_final"]), num_it(flat["p5_final"]),
                        num_it(flat["p95_final"]), pct(flat["median_dd_pct"]), pct(flat["p95_dd_pct"])],
        "Kelly frazionario": [pct(kelly["risk_of_ruin_pct"]), pct(kelly["prob_profit_pct"]), num_it(kelly["median_final"]),
                              num_it(kelly["p5_final"]), num_it(kelly["p95_final"]), pct(kelly["median_dd_pct"]), pct(kelly["p95_dd_pct"])],
    })
    st.dataframe(summary, hide_index=True, width="stretch")

    panels = [(f"Stake fisso {stake_pct:.1f}%", flat, BLUE), (f"Kelly x{kf:g}", kelly, ORANGE)]
    st.subheader("Evoluzione del bankroll - 1.000 simulazioni")
    st.caption("Linea: mediana. Fascia scura: 25°-75° percentile. Fascia chiara: 5°-95° percentile. Tratteggio: bankroll iniziale.")
    show(fan_chart(panels))
    st.subheader("Distribuzione del max drawdown atteso")
    show(dd_hist_chart(panels))
    st.caption("Metodo: bootstrap con reinserimento delle operazioni storiche (1.000 percorsi, seme fisso). "
               "Kelly stima il vantaggio con il ROI storico: se quel ROI è sovrastimato, Kelly punta troppo. "
               "I rendimenti passati non garantiscono quelli futuri.")


# --------------------------------------------------------------------------- pagina: strategie
MONITOR_STATE = {"in linea": "ok", "utile ma CLV negativo": "warn", "poche operazioni recenti": "warn",
                 "al massimo drawdown": "bad", "in calo": "bad", "nessuna operazione": "warn"}


def strategy_monitor_panel(vault: dict) -> None:
    st.subheader("Monitor delle strategie")
    st.caption("Riesegue ogni strategia salvata sui dati aggiornati e confronta gli ultimi 12 mesi e 90 giorni con lo storico: "
               "serve ad accorgersi quando un vantaggio si sta spegnendo.")
    key = json.dumps([vault, db_version()], sort_keys=True, default=str)
    if st.button("Analizza le strategie", icon=":material/monitoring:") or ss.get("_mon_key") == key:
        if ss.get("_mon_key") != key:
            with st.spinner("Rieseguo le strategie..."):
                ss["_mon"] = an.strategy_monitor(vault, lambda sp, m, p, t: selections(sp, m, p, t, db_version()),
                                                 games=service_games(db_version()))
            ss["_mon_key"] = key
        table, pool = ss["_mon"]
        if table.empty:
            st.info("Nessuna strategia analizzabile.")
            return
        checklist([(r["strategia"], MONITOR_STATE.get(r["stato"], "warn"),
                    r["stato"].capitalize() + (f" - ultimi 12 mesi: ROI {r['roi_365_pct']:+.1f}% su {int(r['n_365'])} operazioni, "
                                               f"CLV {fmt(r['clv_365_pct'], '{:+.1f}')}%" if r.get("operazioni") else ""))
                   for _, r in table.iterrows()])
        st.dataframe(table.drop(columns=["stato"]), hide_index=True, width="stretch", column_config={
            "roi_pct": st.column_config.NumberColumn("ROI % storico", format="%+.2f"),
            "roi_365_pct": st.column_config.NumberColumn("ROI % 12 mesi", format="%+.2f"), "n_365": "op. 12 mesi",
            "roi_90_pct": st.column_config.NumberColumn("ROI % 90 giorni", format="%+.2f"), "n_90": "op. 90 giorni",
            "clv_365_pct": st.column_config.NumberColumn("CLV % 12 mesi", format="%+.2f"),
            "dd_attuale": st.column_config.NumberColumn("drawdown attuale (u)", format="%.1f"),
            "dd_massimo": st.column_config.NumberColumn("drawdown massimo (u)", format="%.1f"), "ultima": "ultima operazione"})
        if len(pool) and pool["strategia"].nunique() > 1:
            st.markdown("**Portafoglio: tutte le strategie insieme**")
            pm = bt.compute_metrics(pool.assign(ev_close_pct=np.nan), 100.0)
            kpis([("ROI complessivo", f"{pm['roi_pct']:+.2f}%", f"{num_it(pm['trades'])} operazioni", sign_tone(pm["roi_pct"])),
                  ("Profitto", f"{pm['profit']:+.1f} u", "1 u per operazione", sign_tone(pm["profit"])),
                  ("Max drawdown", f"-{pm['max_dd_units']:.1f} u", "delle strategie sommate", None)])
            show(equity_chart(pool).update_layout(title_text="Profitto cumulato del portafoglio (unità)"))
            corr = an.monthly_correlation(pool)
            if not corr.empty:
                st.caption("Correlazione dei profitti mensili: valori vicini a 1 indicano strategie che salgono e scendono insieme "
                           "(non diversificano), vicini a 0 strategie indipendenti.")
                st.dataframe(corr, width="stretch")
        st.caption("ROI recente negativo su poche operazioni è normale oscillazione; il segnale da prendere sul serio è un CLV che "
                   "diventa negativo o un drawdown vicino al massimo storico.")


def alerts_panel() -> None:
    with st.expander("Avvisi su Telegram quando scatta un segnale"):
        cfg = fx.load_settings()
        st.caption("Gratis, tre passi: 1) su Telegram cerca **@BotFather**, scrivi `/newbot` e copia il token che ricevi; "
                   "2) apri la chat con il tuo nuovo bot e scrivi un messaggio qualsiasi; 3) incolla il token qui e premi *Trova il mio ID*.")
        a, b, c = st.columns([3, 1.4, 1.2])
        token = a.text_input("Token del bot", value=cfg.get("telegram_token", ""), type="password")
        chat = b.text_input("ID della chat", value=cfg.get("telegram_chat_id", ""))
        bank = c.number_input("Bankroll per lo stake", 1.0, 1e9, float(cfg.get("alert_bankroll") or 1000.0), 100.0)
        if (token.strip(), chat.strip(), bank) != (cfg.get("telegram_token", ""), cfg.get("telegram_chat_id", ""), float(cfg.get("alert_bankroll") or 1000.0)):
            fx.save_settings({"telegram_token": token.strip(), "telegram_chat_id": chat.strip(), "alert_bankroll": bank})
        x, y, z, _ = st.columns([1.2, 1.2, 1.6, 2])
        if x.button("Trova il mio ID", width="stretch"):
            found, msg = notify.find_chat_id(token)
            if found:
                fx.save_settings({"telegram_chat_id": found})
                st.success(f"ID trovato e salvato: {found}. Ricarica la pagina per vederlo nel campo.")
            else:
                st.warning(msg)
        if y.button("Invia una prova", width="stretch"):
            ok, msg = notify.send(token, chat, "Sports Trading Lab: avvisi configurati correttamente.")
            (st.success if ok else st.warning)(msg)
        if z.button("Controlla i segnali ora", icon=":material/notifications:", width="stretch"):
            with st.spinner("Controllo le partite in programma..."):
                st.info(notify.run_alerts())
        st.caption("Per ricevere gli avvisi anche ad app chiusa, esegui una volta nel Terminale, dalla cartella del progetto: "
                   "`bash attiva_avvisi_mac.sh` (controllo ogni 3 ore; `bash attiva_avvisi_mac.sh off` per disattivare). "
                   "Ogni segnale viene inviato una sola volta. Il token resta solo sul tuo Mac, in `data/settings.json`.")


def page_vault() -> None:
    header("Strategie", "Archivio delle strategie salvate e scanner delle quote del giorno.")
    vault = sv.load_vault()
    st.subheader("Strategie salvate")
    if vault:
        rows = [{"nome": n, "sport": SPORT_LABEL.get(e.get("config", {}).get("sport"), "?"), "regole": describe(e.get("config", {})),
                 "operazioni": e.get("metrics", {}).get("trades"), "ROI %": e.get("metrics", {}).get("roi_pct"),
                 "CLV %": e.get("metrics", {}).get("clv_pct"), "max DD (u)": e.get("metrics", {}).get("max_dd_units"),
                 "salvata il": e.get("saved_at")} for n, e in vault.items()]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", column_config={
            "ROI %": st.column_config.NumberColumn(format="%+.2f"), "CLV %": st.column_config.NumberColumn(format="%+.2f"),
            "max DD (u)": st.column_config.NumberColumn(format="%.1f")})
        a, b, c = st.columns([3, 1, 1])
        chosen = a.selectbox("Strategia", list(vault), index=None, placeholder="scegli una strategia...", label_visibility="collapsed")
        if b.button("Apri nel Backtester", disabled=chosen is None, icon=":material/open_in_new:", width="stretch"):
            queue_cfg(vault[chosen].get("config", {}))
            ss["bt_view"] = VIEWS[0]
            st.switch_page(PAGES["backtester"])
        if c.button("Elimina", disabled=chosen is None, icon=":material/delete:", width="stretch"):
            sv.delete_strategy(chosen)
            st.rerun()
    else:
        st.info("Nessuna strategia salvata. Testa una strategia nel Backtester e salvala dalla vista *Salva*.")
    with st.expander("Salva l'ultima strategia testata", expanded=False):
        if ss.get("last"):
            st.caption(describe(ss["last"]["cfg"]))
        save_form("save_vault")
    if vault and db_ready():
        strategy_monitor_panel(vault)
    alerts_panel()

    st.subheader("Scanner delle partite in programma")
    sources = {"football": "Calcio - partite in programma", "tennis": "Tennis - quote ATP", "csv": "File CSV"}
    source = st.segmented_control("Sorgente", list(sources), format_func=sources.get, default="football",
                                  label_visibility="collapsed") or "football"
    df = None
    if source == "football":
        a, b, c = st.columns([1.4, 1.2, 3])
        book = a.selectbox("Quote da usare", ["avg", "max", "b365", "bfe"], format_func=bt.BOOK_LABELS.get)
        a.caption("Fonte: football-data.co.uk, gratuita e senza registrazione.")
        b.markdown("<div style='height:1.75rem'></div>", unsafe_allow_html=True)
        if b.button("Scarica le partite", type="primary", icon=":material/download:", width="stretch"):
            with st.spinner("Scarico le partite in programma..."):
                got, msg = fx.fetch_football_fixtures(book)
            ss["scan_football"] = (got, msg, book, pd.Timestamp.now().strftime("%d/%m %H:%M"))
        if "scan_football" in ss:
            got, msg, used, when = ss["scan_football"]
            if msg:
                st.warning(msg)
            if len(got):
                df = got
                covered = int(got["div"].isin(C.FOOTBALL_LEAGUES).sum())
                c.caption(f"{len(got)} partite scaricate alle {when} (quote: {bt.BOOK_LABELS[used]}), di cui {covered} nei campionati "
                          "presenti nel database. Classifica, form, tiri e riposo vengono calcolati dallo storico.")
    elif source == "tennis":
        cfg = fx.load_settings()
        a, b, c = st.columns([2, 1.2, 1.2])
        api_key = a.text_input("Chiave di The Odds API", value=cfg.get("odds_api_key", ""), type="password",
                               help="Piano gratuito: 500 crediti al mese, registrazione su the-odds-api.com. Ogni torneo scaricato costa 1 credito.")
        book = b.selectbox("Quote da usare", ["avg", "max", "ps"], format_func=bt.BOOK_LABELS.get)
        c.markdown("<div style='height:1.75rem'></div>", unsafe_allow_html=True)
        if c.button("Scarica le quote", type="primary", icon=":material/download:", width="stretch"):
            if api_key != cfg.get("odds_api_key", ""):
                fx.save_settings({"odds_api_key": api_key.strip()})
            with st.spinner("Scarico le quote ATP..."):
                got, msg = fx.fetch_tennis_odds(api_key, book)
            ss["scan_tennis"] = (got, msg, pd.Timestamp.now().strftime("%d/%m %H:%M"))
        with st.expander("Non hai ancora la chiave? Ecco cosa fare"):
            st.markdown(ODDS_API_STEPS)
        st.caption("Per il tennis non esiste una fonte gratuita senza registrazione: questa richiede una chiave personale gratuita. "
                   "In alternativa usa un file CSV.")
        if "scan_tennis" in ss:
            got, msg, when = ss["scan_tennis"]
            if msg:
                (st.caption if len(got) else st.warning)(msg)
            if len(got):
                df = got
    else:
        upload = st.file_uploader("CSV con le quote del giorno", type=["csv"])
        t1, t2, _ = st.columns([1, 1, 3])
        t1.download_button("Modello CSV tennis", sv.TENNIS_TEMPLATE.to_csv(index=False).encode(), "quote_tennis_modello.csv", "text/csv")
        t2.download_button("Modello CSV calcio", sv.FOOTBALL_TEMPLATE.to_csv(index=False).encode(), "quote_calcio_modello.csv", "text/csv")
        st.caption("Colonne minime - tennis: player1, player2, odds1, odds2; calcio: home_team, away_team, odds_home, odds_draw, odds_away. "
                   "Le altre variabili vengono calcolate dallo storico se i nomi coincidono con quelli del database.")
        if upload is not None:
            try:
                df = pd.read_csv(upload, sep=None, engine="python")
            except Exception as exc:  # file malformato
                st.error(f"Impossibile leggere il CSV: {exc}")
                return
    c = st.columns([1, 1, 1, 2])
    bankroll = c[0].number_input("Bankroll", 1.0, 1e9, 1000.0, 100.0)
    method = c[1].radio("Staking", ["flat", "kelly"], format_func={"flat": "Stake fisso", "kelly": "Kelly frazionario"}.get)
    if method == "flat":
        flat_pct, kf, cap = c[2].number_input("Stake (% banca)", 0.1, 25.0, 1.0, 0.1), 0.25, 5.0
    else:
        kf = c[2].selectbox("Frazione", [0.125, 0.25, 0.5], index=1, format_func=lambda x: f"{x:g} Kelly")
        flat_pct, cap = 1.0, c[2].number_input("Tetto (%)", 0.5, 25.0, 5.0, 0.5)
    if df is None:
        return
    if not vault:
        st.warning("Salva almeno una strategia per poter generare segnali.")
        return
    try:
        if db_ready():
            with closing(bt.connect()) as con:
                signals, notes = sv.scan(df, vault, bankroll, con, method, flat_pct, kf, cap)
        else:
            signals, notes = sv.scan(df, vault, bankroll, None, method, flat_pct, kf, cap)
    except Exception as exc:  # contenuto del CSV non interpretabile
        st.error(f"Il file non è nel formato atteso ({exc}). Confrontalo con il modello scaricabile qui sopra.")
        return
    for n in dict.fromkeys(notes):
        st.caption(":material/info: " + n)
    kpis([("Match nel file", str(len(df)), None, None), ("Segnali attivi", str(len(signals)), None, None),
          ("Esposizione totale", num_it(signals["importo_rischio"].sum(), 2) if len(signals) else "0", "somma degli importi a rischio", None)])
    if signals.empty:
        st.info("Nessun match del file attiva le strategie salvate.")
    else:
        st.dataframe(signals, hide_index=True, width="stretch", column_config={
            "quota": st.column_config.NumberColumn(format="%.2f"),
            "importo_rischio": st.column_config.NumberColumn("importo a rischio", format="%.2f"),
            "puntata_exchange": st.column_config.NumberColumn("puntata da inserire", format="%.2f"),
            "condizione_in_partita": "condizione da verificare in partita",
            "quota_live_stimata": st.column_config.NumberColumn("quota live attesa", format="%.2f")})
        st.caption("Back: importo a rischio = puntata. Lay: importo a rischio = responsabilità; la puntata da inserire sull'exchange è responsabilità / (quota - 1).")
        st.download_button("Esporta segnali (CSV)", signals.to_csv(index=False).encode("utf-8"),
                           f"segnali_{date.today():%Y%m%d}.csv", "text/csv", icon=":material/download:")
        with lg.open_ledger() as con:
            names = lg.accounts(con)["conto"].tolist()
        if names:
            a, b = st.columns([2, 2])
            target = a.selectbox("Conto su cui registrare i segnali", names, label_visibility="collapsed")
            if b.button(f"Registra i {len(signals)} segnali nel Registro come giocate aperte", icon=":material/playlist_add:"):
                with lg.open_ledger() as con:
                    n = lg.add_signals(con, signals, target, sv.detect_sport(df) or "", vault)
                st.success(f"{n} giocate aperte aggiunte al Registro sul conto {target}.")
        else:
            st.caption("Per registrare i segnali come giocate, crea prima un conto nella pagina Registro.")
    with st.expander("File caricato (righe con segnale evidenziate)"):
        hits = set(signals["evento"]) if len(signals) else set()
        norm = df.rename(columns=lambda c: str(c).strip().lower().replace(" ", "_"))
        if "player1" in norm and "player2" in norm:
            p1, p2 = norm["player1"].astype(str).str.strip(), norm["player2"].astype(str).str.strip()
            flag = (p1 + " vs " + p2).isin(hits) | (p2 + " vs " + p1).isin(hits)
        elif "home_team" in norm and "away_team" in norm:
            flag = (norm["home_team"].astype(str).str.strip() + " - " + norm["away_team"].astype(str).str.strip()).isin(hits)
        else:
            flag = pd.Series(False, index=df.index)
        flag = flag.to_numpy()
        styled = df.reset_index(drop=True).style.apply(
            lambda row: ["background-color: rgba(57,135,229,0.28)" if flag[row.name] else ""] * len(row), axis=1)
        st.dataframe(styled, hide_index=True, width="stretch")


# --------------------------------------------------------------------------- pagina: registro
STATUS_LABEL = {"open": "Aperta", "won": "Vinta", "lost": "Persa", "void": "Rimborsata", "cashout": "Cash out"}
SIDE_LABEL = {"back": "Punta", "lay": "Banca"}


def _flash(msg: str) -> None:
    ss["_flash"] = msg
    st.rerun()


def _bet_label(r) -> str:
    return f"#{r.id}  {r.placed_date:%d/%m/%Y}  {r.event} - {r.selection} @ {r.odds:.2f}  ({r.stake:.2f} su {r.account})"


def _bets_view(df: pd.DataFrame) -> pd.DataFrame:
    v = df[["id", "placed_date", "sport", "event", "selection", "side", "odds", "stake", "account", "strategy",
            "status", "close_odds", "clv_pct", "pnl", "note"]].copy()
    v["placed_date"] = v["placed_date"].dt.date
    v["side"] = v["side"].map(SIDE_LABEL)
    v["status"] = v["status"].map(STATUS_LABEL)
    return v.rename(columns={"placed_date": "data", "event": "evento", "selection": "selezione", "side": "lato",
                             "odds": "quota", "stake": "a rischio", "account": "conto", "strategy": "strategia",
                             "status": "esito", "close_odds": "chiusura", "clv_pct": "clv %", "pnl": "profitto", "note": "nota"})


BET_COLS = {
    "quota": st.column_config.NumberColumn(format="%.2f"), "a rischio": st.column_config.NumberColumn(format="%.2f"),
    "chiusura": st.column_config.NumberColumn(format="%.2f"), "clv %": st.column_config.NumberColumn(format="%+.2f"),
    "profitto": st.column_config.NumberColumn(format="%+.2f"),
}


def ledger_accounts_tab(acc: pd.DataFrame, mov: pd.DataFrame) -> None:
    if len(acc):
        st.dataframe(acc, hide_index=True, width="stretch", column_config={
            c: st.column_config.NumberColumn(format="%.2f") for c in ("depositi", "prelievi", "profitto", "saldo", "in_gioco", "disponibile")})
        st.caption("Saldo = depositi - prelievi + profitto delle giocate chiuse. Disponibile = saldo meno l'importo a rischio delle giocate aperte.")
    a, b = st.columns(2)
    with a, st.form("new_account", clear_on_submit=True):
        st.markdown("**Nuovo conto**")
        name = st.text_input("Nome", placeholder="es. Betfair Exchange, Bet365...")
        kind = st.selectbox("Tipo", ["Bookmaker", "Exchange", "Altro"])
        opening = st.number_input("Saldo iniziale", 0.0, 1e9, 0.0, 50.0)
        if st.form_submit_button("Crea conto", icon=":material/add:"):
            try:
                with lg.open_ledger() as con:
                    lg.add_account(con, name, kind, opening)
                _flash(f"Conto '{name.strip()}' creato.")
            except ValueError as exc:
                st.error(str(exc).capitalize())
    with b, st.form("new_move", clear_on_submit=True):
        st.markdown("**Deposito o prelievo**")
        names = acc["conto"].tolist()
        account = st.selectbox("Conto", names, index=0 if names else None, placeholder="crea prima un conto")
        c1, c2, c3 = st.columns(3)
        kind = c1.selectbox("Tipo", ["deposit", "withdrawal"], format_func={"deposit": "Deposito", "withdrawal": "Prelievo"}.get)
        amount = c2.number_input("Importo", 0.0, 1e9, 0.0, 50.0)
        when = c3.date_input("Data", date.today())
        note = st.text_input("Nota", placeholder="facoltativa")
        if st.form_submit_button("Registra movimento", icon=":material/payments:"):
            try:
                with lg.open_ledger() as con:
                    lg.add_movement(con, account or "", kind, amount, when, note)
                _flash("Movimento registrato.")
            except ValueError as exc:
                st.error(str(exc).capitalize())
    if len(mov):
        st.subheader("Movimenti")
        st.dataframe(mov.drop(columns="id"), hide_index=True, width="stretch",
                     column_config={"importo": st.column_config.NumberColumn(format="%.2f")})
        with st.expander("Correggi un errore"):
            labels = {int(r.id): f"{r.data}  {r.tipo} {r.importo:.2f} su {r.conto}" for r in mov.itertuples()}
            c1, c2 = st.columns([3, 1])
            mid = c1.selectbox("Movimento da eliminare", list(labels), index=None, format_func=labels.get, label_visibility="collapsed",
                               placeholder="movimento da eliminare...")
            if c2.button("Elimina movimento", disabled=mid is None, width="stretch"):
                with lg.open_ledger() as con:
                    lg.delete_movement(con, mid)
                _flash("Movimento eliminato.")
            empty = acc[(acc["giocate"] == 0) & (acc["depositi"] == 0) & (acc["prelievi"] == 0)]["conto"].tolist()
            if empty:
                c1, c2 = st.columns([3, 1])
                victim = c1.selectbox("Conto vuoto da eliminare", empty, index=None, label_visibility="collapsed", placeholder="conto vuoto da eliminare...")
                if c2.button("Elimina conto", disabled=victim is None, width="stretch"):
                    with lg.open_ledger() as con:
                        lg.delete_account(con, victim)
                    _flash("Conto eliminato.")


def ledger_bets_tab(acc: pd.DataFrame, allb: pd.DataFrame, vault: dict) -> None:
    names = acc["conto"].tolist()
    with st.expander("Nuova giocata", expanded=allb.empty), st.form("new_bet", clear_on_submit=True):
        c = st.columns([1, 1, 3, 2])
        when = c[0].date_input("Data", date.today())
        sport = c[1].selectbox("Sport", ["tennis", "football", "altro"], format_func={"tennis": "Tennis", "football": "Calcio", "altro": "Altro"}.get)
        event = c[2].text_input("Evento", placeholder="es. Sinner vs Alcaraz, Inter - Milan")
        selection = c[3].text_input("Selezione", placeholder="es. Sinner, 1, Over 2.5")
        c = st.columns([1, 1, 1, 1, 2])
        side = c[0].selectbox("Lato", ["back", "lay"], format_func=SIDE_LABEL.get)
        odds = c[1].number_input("Quota", 1.01, 1000.0, 2.00, 0.01)
        stake = c[2].number_input("Importo a rischio", 0.0, 1e9, 10.0, 1.0, help="Puntata per un back; responsabilità per un lay.")
        comm = c[3].number_input("Commissione %", 0.0, 20.0, 0.0, 0.5)
        account = c[4].selectbox("Conto", names)
        c = st.columns([2, 1, 1, 2])
        strategy = c[0].selectbox("Strategia", list(vault), index=None, placeholder="nessuna", accept_new_options=True)
        status = c[1].selectbox("Esito", [r for r in lg.RESULTS if r != "cashout"], format_func=STATUS_LABEL.get,
                                help="Per un cash out registra la giocata come aperta e chiudila qui sotto indicando l'importo.")
        close = c[2].number_input("Quota di chiusura", 1.0, 1000.0, None, 0.01, placeholder="facoltativa", help="Serve a calcolare il CLV.")
        note = c[3].text_input("Nota", placeholder="facoltativa")
        if st.form_submit_button("Registra giocata", type="primary", icon=":material/add:"):
            try:
                with lg.open_ledger() as con:
                    lg.add_bet(con, placed_date=when, sport=sport, event=event, selection=selection, side=side, odds=odds,
                               stake=stake, account=account or "", strategy=strategy or "", commission=comm / 100, status=status,
                               close_odds=close, note=note)
                _flash("Giocata registrata.")
            except ValueError as exc:
                st.error(str(exc).capitalize())

    with st.expander("Calcolatore di copertura: quanto vale chiudere adesso"):
        c = st.columns(5)
        h_side = c[0].selectbox("Posizione", ["back", "lay"], format_func={"back": "Ho puntato", "lay": "Ho bancato"}.get, key="h_side")
        h_odds = c[1].number_input("Quota presa", 1.01, 1000.0, 2.50, 0.01, key="h_odds")
        h_stake = c[2].number_input("Puntata" if h_side == "back" else "Puntata del banco", 0.01, 1e9, 10.0, 1.0, key="h_stake")
        h_live = c[3].number_input("Quota attuale", 1.01, 1000.0, 1.80, 0.01, key="h_live")
        h_comm = c[4].number_input("Commissione %", 0.0, 20.0, 0.0, 0.5, key="h_comm")
        h = an.hedge(h_side, h_odds, h_stake, h_live, h_comm / 100)
        kpis([("Per chiudere", f"{h['hedge_side']} {num_it(h['hedge_stake'], 2)}", f"a quota {h_live:.2f}", None),
              ("Risultato bloccato", signed_it(h["profit"]), "uguale qualunque sia l'esito", sign_tone(h["profit"])),
              ("Sul rischio iniziale", f"{h['profit_pct']:+.1f}%", f"rischio {num_it(h['risk'], 2)}", sign_tone(h["profit_pct"]))])
        st.caption("Copri la posizione con la giocata opposta alla quota attuale. Il *risultato bloccato* è l'importo da inserire "
                   "quando chiudi la giocata come *Cash out* qui sotto.")

    opened = allb[allb["status"] == "open"]
    st.subheader(f"Giocate aperte ({len(opened)})")
    if opened.empty:
        st.caption("Nessuna giocata in attesa di esito.")
    else:
        st.dataframe(_bets_view(opened).drop(columns=["esito", "profitto", "clv %", "chiusura"]), hide_index=True, width="stretch", column_config=BET_COLS)
        labels = {int(r.id): _bet_label(r) for r in opened.itertuples()}
        c = st.columns([4, 3, 1, 1])
        bid = c[0].selectbox("Giocata da chiudere", list(labels), format_func=labels.get)
        result = c[1].radio("Esito", ["won", "lost", "void", "cashout"], format_func=STATUS_LABEL.get, horizontal=True)
        close = c[2].number_input("Chiusura", 1.0, 1000.0, None, 0.01, placeholder="facolt.", key="settle_close")
        cash = None
        if result == "cashout":
            at_risk = float(opened.loc[opened["id"] == bid, "stake"].iloc[0])
            cash = c[0].number_input("Profitto o perdita incassati con il cash out", -at_risk, 1e9, 0.0, 1.0, key="settle_cash",
                                     help="Importo ricevuto meno importo a rischio, al netto delle commissioni: positivo se hai "
                                          f"chiuso in profitto, negativo se in perdita (al massimo -{at_risk:.2f}).")
        c[3].markdown("<div style='height:1.75rem'></div>", unsafe_allow_html=True)
        if c[3].button("Chiudi", type="primary", width="stretch"):
            try:
                with lg.open_ledger() as con:
                    lg.settle_bet(con, bid, result, close, cash)
                _flash(f"Giocata #{bid} chiusa: {STATUS_LABEL[result].lower()}.")
            except ValueError as exc:
                st.error(str(exc).capitalize())

    st.subheader("Storico")
    closed = allb[allb["status"] != "open"]
    if closed.empty:
        st.caption("Nessuna giocata chiusa.")
    else:
        st.dataframe(_bets_view(closed).iloc[::-1].head(MAX_TABLE_ROWS), hide_index=True, width="stretch",
                     height=360 if len(closed) > 9 else "auto", column_config=BET_COLS)
    if not allb.empty:
        c = st.columns([1, 3, 1, 1])
        c[0].download_button("Esporta (CSV)", _bets_view(allb).to_csv(index=False).encode("utf-8"),
                             f"registro_{date.today():%Y%m%d}.csv", "text/csv", icon=":material/download:")
        labels = {int(r.id): _bet_label(r) for r in allb.iloc[::-1].itertuples()}
        fix = c[1].selectbox("Giocata da correggere", list(labels), index=None, format_func=labels.get, label_visibility="collapsed",
                             placeholder="correggi una giocata: riaprila o eliminala...")
        if c[2].button("Riapri", disabled=fix is None, width="stretch"):
            with lg.open_ledger() as con:
                lg.settle_bet(con, fix, "open")
            _flash(f"Giocata #{fix} riaperta.")
        if c[3].button("Elimina", disabled=fix is None, width="stretch"):
            with lg.open_ledger() as con:
                lg.delete_bet(con, fix)
            _flash(f"Giocata #{fix} eliminata.")


def ledger_dashboard_tab(allb: pd.DataFrame, vault: dict) -> None:
    trades = lg.settled_trades(allb)
    if trades.empty:
        st.info("Qui compariranno andamento e confronto con il backtest appena avrai chiuso le prime giocate.")
        return
    show(equity_chart(trades.assign(equity=trades["equity"])).update_layout(title_text="Profitto reale cumulato"))
    st.subheader("Reale contro backtest, per strategia")
    st.dataframe(lg.by_strategy(allb, vault), hide_index=True, width="stretch", column_config={
        "profitto": st.column_config.NumberColumn(format="%+.2f"), "rischiato": st.column_config.NumberColumn(format="%.2f"),
        "roi_pct": st.column_config.NumberColumn("ROI % reale", format="%+.2f"), "clv_pct": st.column_config.NumberColumn("CLV % reale", format="%+.2f"),
        "roi_backtest_pct": st.column_config.NumberColumn("ROI % backtest", format="%+.2f"),
        "clv_backtest_pct": st.column_config.NumberColumn("CLV % backtest", format="%+.2f")})
    st.caption("Con poche giocate il ROI reale oscilla molto: il CLV converge prima ed è il confronto più utile nelle prime settimane. "
               "Le colonne backtest si riempiono quando il nome della strategia coincide con una strategia salvata.")
    mo = bt.monthly_returns(trades)
    if len(mo) > 1:
        hover = [f"{int(n)} giocate, yield {y:+.1f}%" for n, y in zip(mo["trades"], mo["yield_pct"])]
        fig = bars_chart(mo["month"], mo["profit"], hover, "Profitto reale per mese", "", 260)
        fig.update_xaxes(tickangle=0)
        show(fig)


def page_ledger() -> None:
    header("Registro", "Le tue giocate reali, i saldi dei conti e il confronto con il backtest.")
    if ss.get("_flash"):
        st.success(ss.pop("_flash"))
    try:
        with lg.open_ledger() as con:
            acc, allb, mov, tot = lg.accounts(con), lg.bets(con), lg.movements(con), lg.totals(con)
    except duckdb.Error as exc:
        st.error(f"Impossibile aprire il registro: {exc}. Chiudi altre finestre dell'app e ricarica la pagina.")
        return
    vault = sv.load_vault()
    if acc.empty:
        st.info("Per iniziare crea un conto per ogni bookmaker o exchange che usi, con il saldo attuale. "
                "Poi registra le giocate: a mano dalla scheda Giocate, oppure dai segnali dello scanner nella pagina Strategie.")
        ledger_accounts_tab(acc, mov)
        return
    m = bt.compute_metrics(lg.settled_trades(allb), max(tot["versato"], 1.0))
    n_open = int((allb["status"] == "open").sum())
    kpis([("Bankroll totale", num_it(tot["saldo"], 2), f"versato netto {num_it(tot['versato'], 2)}", None),
          ("Disponibile", num_it(tot["saldo"] - tot["in_gioco"], 2), f"{num_it(tot['in_gioco'], 2)} in gioco su {n_open} aperte", None),
          ("Profitto", signed_it(tot["profitto"]), f"{m['trades']} giocate chiuse", sign_tone(tot["profitto"])),
          ("ROI reale", f"{m['roi_pct']:+.2f}%" if m["trades"] else "n/d", f"win rate {m['win_rate_pct']:.0f}%" if m["trades"] else "", sign_tone(m["roi_pct"]) if m["trades"] else None),
          ("CLV medio", fmt(m["clv_pct"], "{:+.2f}%"), "sulle giocate con quota di chiusura", sign_tone(m["clv_pct"])),
          ("Max drawdown", signed_it(-m["max_dd_units"]) if m["max_dd_units"] else "0,00", f"{m['max_dd_pct']:.0f}% del versato", None)])
    t1, t2, t3 = st.tabs(["Giocate", "Andamento", "Conti"])
    with t1:
        ledger_bets_tab(acc, allb, vault)
    with t2:
        ledger_dashboard_tab(allb, vault)
    with t3:
        ledger_accounts_tab(acc, mov)


# --------------------------------------------------------------------------- pagina: roadmap
def _rm_save_status(item_id: str) -> None:
    rm.update_item(item_id, status=ss[f"rm_s_{item_id}"])


def _rm_save_note(item_id: str) -> None:
    rm.update_item(item_id, note=ss[f"rm_n_{item_id}"])


def page_roadmap() -> None:
    header("Roadmap 2027", "Il piano dell'anno, trimestre per trimestre: cosa aggiungere alla piattaforma e come usarla per decidere.")
    if ss.get("_flash"):
        st.success(ss.pop("_flash"))
    all_items = rm.items()
    prog = rm.progress(all_items)
    done, total = prog["all"]
    doing = sum(i["status"] == "doing" for i in all_items)
    kpis([("Avanzamento", f"{done} / {total}", f"{done / total * 100:.0f}% delle voci completate", "good" if done else None),
          ("In corso", str(doing), "voci avviate", None)]
         + [(f"{q} · {rm.QUARTERS[q][1]}", f"{prog[q][0]} / {prog[q][1]}", rm.QUARTERS[q][0], "good" if prog[q][1] and prog[q][0] == prog[q][1] else None)
            for q in rm.QUARTERS])
    c1, c2 = st.columns([2, 3])
    track = c1.radio("Mostra", ["Tutto"] + rm.TRACKS, horizontal=True, label_visibility="collapsed")
    c2.caption("**Piattaforma**: funzioni da aggiungere allo strumento.  **Metodo**: cosa fare tu, con lo strumento, per arrivare a "
               "puntare solo su ciò che i dati confermano. Aggiorna lo stato di ogni voce: viene salvato subito.")
    color = {"todo": MUTED, "doing": BLUE, "done": GOOD}
    for q, (months, name, goal) in rm.QUARTERS.items():
        q_items = [i for i in all_items if i["quarter"] == q and track in ("Tutto", i["track"])]
        st.subheader(f"{q} · {name}")
        st.caption(f"{months} - {goal}")
        d, t = prog[q]
        st.progress(d / t if t else 0.0, text=f"{d} di {t} completate")
        for it in q_items:
            with st.container(border=True):
                a, b = st.columns([5, 2])
                tag = f'<span class="tag" style="min-width:0;border:1px solid {GRID};border-radius:10px;padding:1px 8px;margin-left:8px">{it["track"]}</span>'
                a.markdown(f'<span class="dot" style="background:{color[it["status"]]};margin-right:8px"></span>'
                           f'<b>{it["title"]}</b>{tag}', unsafe_allow_html=True)
                a.caption(it["description"])
                if it["needs"]:
                    a.caption(f":material/link: {it['needs']}")
                ss.setdefault(f"rm_s_{it['id']}", it["status"])
                ss.setdefault(f"rm_n_{it['id']}", it["note"])
                b.selectbox("Stato", rm.STATUSES, format_func=rm.STATUS_LABEL.get, key=f"rm_s_{it['id']}",
                            on_change=_rm_save_status, args=(it["id"],), label_visibility="collapsed")
                b.text_input("Nota", key=f"rm_n_{it['id']}", on_change=_rm_save_note, args=(it["id"],),
                             label_visibility="collapsed", placeholder="nota personale...")
                if it["custom"] and b.button("Elimina voce", key=f"rm_d_{it['id']}", width="stretch"):
                    rm.delete_item(it["id"])
                    _flash("Voce eliminata.")
    with st.expander("Aggiungi una voce tua"), st.form("rm_add", clear_on_submit=True):
        c = st.columns([1, 1, 3])
        quarter = c[0].selectbox("Trimestre", list(rm.QUARTERS))
        tr = c[1].selectbox("Binario", rm.TRACKS, index=1)
        title = c[2].text_input("Titolo")
        desc = st.text_input("Descrizione", placeholder="facoltativa")
        if st.form_submit_button("Aggiungi", icon=":material/add:"):
            try:
                rm.add_item(quarter, tr, title, desc)
                _flash("Voce aggiunta alla roadmap.")
            except ValueError as exc:
                st.error(str(exc).capitalize())
    st.caption("La roadmap è un piano di lavoro, non una previsione di guadagno: nessun passo garantisce un profitto, e ogni "
               "passaggio a stake reali va deciso sui risultati misurati nel Registro. Le voci con il simbolo del collegamento "
               "dipendono da servizi o dati esterni ancora da scegliere.")


# --------------------------------------------------------------------------- navigazione
collect_update()
ensure_schema()
auto_update()
if not ss.get("_backup_checked"):     # un backup al giorno dei dati personali, senza interrompere l'uso
    ss["_backup_checked"] = True
    try:
        bk.backup_if_due(fx.load_settings().get("backup_dir") or None)
    except OSError:
        pass
PAGES = {
    "backtester": st.Page(page_backtester, title="Backtester", icon=":material/query_stats:", url_path="backtester", default=True),
    "today": st.Page(page_today, title="Oggi", icon=":material/today:", url_path="oggi"),
    "scalping": st.Page(page_scalping, title="Scalping", icon=":material/sports_tennis:", url_path="scalping"),
    "live": st.Page(page_live, title="Quote reali", icon=":material/show_chart:", url_path="quote-reali"),
    "risk": st.Page(page_risk, title="Rischio", icon=":material/casino:", url_path="risk"),
    "vault": st.Page(page_vault, title="Strategie", icon=":material/bookmark:", url_path="vault"),
    "ledger": st.Page(page_ledger, title="Registro", icon=":material/receipt_long:", url_path="registro"),
    "roadmap": st.Page(page_roadmap, title="Roadmap 2027", icon=":material/flag:", url_path="roadmap"),
    "data": st.Page(page_data, title="Dati", icon=":material/database:", url_path="data"),
    "guide": st.Page(page_guide, title="Guida", icon=":material/help:", url_path="guide"),
}
_nav = st.navigation(list(PAGES.values()), position="top")
data_badge()
_nav.run()
