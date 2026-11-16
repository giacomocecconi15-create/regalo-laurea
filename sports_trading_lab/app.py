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
from src import backtester as bt  # noqa: E402
from src import config as C  # noqa: E402
from src import fixtures as fx  # noqa: E402
from src import ledger as lg  # noqa: E402
from src import risk  # noqa: E402
from src import roadmap as rm  # noqa: E402
from src import strategies as sv  # noqa: E402
from src import update_data as ud  # noqa: E402

# palette (tema scuro): superfici, inchiostri e colori di serie verificati per daltonismo
PAGE, SURFACE, GRID, INK, INK2, MUTED = "#0d0d0d", "#1a1a19", "#2c2c2a", "#f0efec", "#c3c2b7", "#898781"
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
:root {{ --line:{GRID}; --surface:{SURFACE}; --ink:{INK}; --ink2:{INK2}; --muted:{MUTED}; --accent:{BLUE}; }}
.stApp * {{ transition:none !important; }}
html, body, .stApp {{ font-feature-settings:"tnum" 1; -webkit-font-smoothing:antialiased; }}
.block-container {{ padding:2.4rem 2.6rem 4rem; max-width:1480px; }}
header[data-testid="stHeader"] {{ background:transparent; }}
section[data-testid="stSidebar"] {{ border-right:1px solid var(--line); }}
section[data-testid="stSidebar"] [data-testid="stSidebarNav"] a {{ border-radius:8px; }}
section[data-testid="stSidebar"] h3 {{ font-size:.7rem !important; letter-spacing:.12em; text-transform:uppercase; color:var(--muted);
    font-weight:600 !important; margin:1.1rem 0 .2rem; padding-top:.9rem; border-top:1px solid var(--line); }}
h1 {{ font-size:1.75rem !important; font-weight:680 !important; letter-spacing:-.022em; padding:0 0 .15rem !important; }}
h3 {{ font-size:1.02rem !important; font-weight:620 !important; letter-spacing:-.01em; margin-top:1.1rem !important; }}
.brand {{ font-size:.68rem; letter-spacing:.16em; text-transform:uppercase; color:var(--accent); font-weight:650; }}
.kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(158px,1fr)); gap:12px; margin:.5rem 0 .9rem; }}
.kpi {{ position:relative; background:linear-gradient(180deg,#1e1e1d 0%,#181817 100%); border:1px solid var(--line); border-radius:12px;
    padding:14px 16px 12px; }}
.kpi::before {{ content:""; position:absolute; left:16px; right:16px; top:-1px; height:2px; border-radius:0 0 2px 2px; background:var(--tone,transparent); }}
.kpi .l {{ color:var(--muted); font-size:.66rem; letter-spacing:.1em; text-transform:uppercase; font-weight:600; }}
.kpi .v {{ color:var(--ink); font-size:1.5rem; font-weight:640; letter-spacing:-.02em; line-height:1.45; white-space:nowrap; }}
.kpi .s {{ color:var(--muted); font-size:.74rem; line-height:1.3; }}
.check {{ display:flex; gap:12px; align-items:baseline; padding:9px 14px; border:1px solid var(--line); border-radius:10px; margin-bottom:6px; background:var(--surface); }}
.check b {{ color:var(--ink); font-weight:600; min-width:240px; display:inline-block; }}
.check span.d {{ color:var(--ink2); font-size:.86rem; }}
.dot {{ width:9px; height:9px; border-radius:50%; display:inline-block; flex:none; position:relative; top:-1px; }}
.tag {{ font-size:.66rem; letter-spacing:.07em; text-transform:uppercase; color:var(--muted); min-width:82px; font-weight:600; }}
.status {{ display:flex; gap:8px; align-items:center; color:var(--muted); font-size:.76rem; padding:.8rem 0 0; margin-top:1rem; border-top:1px solid var(--line); }}
[data-testid="stPlotlyChart"] {{ border:1px solid var(--line); border-radius:12px; overflow:hidden; background:var(--surface); }}
[data-testid="stDataFrame"] {{ border:1px solid var(--line); border-radius:10px; overflow:hidden; }}
[data-testid="stExpander"] details {{ border-color:var(--line); border-radius:10px; }}
[data-testid="stVerticalBlockBorderWrapper"] {{ border-radius:12px; }}
[data-testid="stCaptionContainer"] {{ color:var(--muted); }}
div[data-testid="stAlert"] {{ border-radius:10px; }}
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
    """Riga di stato in fondo al pannello laterale; mentre l'aggiornamento gira si ricarica da sola."""
    def body() -> None:
        up = _updater()
        if update_running():
            frac, label, _ = update_progress()
            st.markdown(f'<div class="status"><span class="dot" style="background:{BLUE}"></span>Aggiornamento dati... {frac * 100:.0f}%</div>',
                        unsafe_allow_html=True)
            return
        if up["proc"] is not None and not up["handled"]:
            st.rerun()
        last = last_update_time()
        when = last.tz_convert("Europe/Rome").strftime("%d/%m %H:%M") if last is not None and pd.notna(last) else "mai"
        st.markdown(f'<div class="status"><span class="dot" style="background:{GOOD if when != "mai" else MUTED}"></span>Dati aggiornati: {when}</div>',
                    unsafe_allow_html=True)

    with st.sidebar:
        st.fragment(body, run_every=3 if update_running() else None)()


def need_db() -> bool:
    if not db_ready():
        st.warning("Database non trovato. Apri **Dati** e premi *Aggiorna Database*.")
        return True
    return False


# --------------------------------------------------------------------------- componenti
def header(title: str, subtitle: str | None = None) -> None:
    st.markdown('<div class="brand">Sports Trading Lab</div>', unsafe_allow_html=True)
    st.title(title)
    if subtitle:
        st.caption(subtitle)


def kpis(items: list[tuple]) -> None:
    """items: (etichetta, valore, nota, tono) con tono 'good' | 'bad' | None."""
    tones = {"good": GOOD, "bad": BAD}
    cells = "".join(
        f'<div class="kpi" style="--tone:{tones.get(tone, GRID)}"><div class="l">{label}</div>'
        f'<div class="v">{value}</div><div class="s">{note or "&nbsp;"}</div></div>'
        for label, value, note, tone in items)
    st.markdown(f'<div class="kpis">{cells}</div>', unsafe_allow_html=True)


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
        margin=dict(l=56, r=20, t=46 if title else 16, b=40), hovermode="x unified",
        title=dict(text=title, font=dict(size=14), x=0.01) if title else None,
        hoverlabel=dict(bgcolor=PAGE, bordercolor=GRID, font=dict(color=INK, family=FONT)), showlegend=False,
    )
    fig.update_xaxes(gridcolor=GRID, linecolor=GRID, zeroline=False, tickfont=dict(color=MUTED))
    fig.update_yaxes(gridcolor=GRID, linecolor=GRID, zerolinecolor=MUTED, zerolinewidth=1, tickfont=dict(color=MUTED))
    return fig


def thin(trades: pd.DataFrame) -> pd.DataFrame:
    """Riduce i punti disegnati (il grafico resta identico a occhio, il browser resta leggero)."""
    n = len(trades)
    if n <= MAX_CHART_POINTS:
        return trades.assign(_n=np.arange(1, n + 1))
    idx = np.unique(np.append(np.arange(0, n, int(np.ceil(n / MAX_CHART_POINTS))), n - 1))
    return trades.iloc[idx].assign(_n=idx + 1)


def equity_chart(trades: pd.DataFrame, cut: pd.Timestamp | None = None) -> go.Figure:
    t = thin(trades)
    fig = go.Figure(go.Scatter(
        x=t["date"], y=t["equity"], mode="lines", line=dict(color=BLUE, width=2, shape="hv"),
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
                               fill="tozeroy", fillcolor="rgba(230,103,103,0.25)", hovertemplate="%{y:.2f} u<extra></extra>"))
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


def _reset_filters(sport: str) -> None:
    for k in [k for k in ss.keys() if k.startswith(f"bt_{sport}_f_") or k in ("bt_omin", "bt_omax", f"bt_{sport}_period", "bt_excl")]:
        del ss[k]


def _filter_widget(sport: str, spec: tuple, sel: pd.DataFrame) -> dict | None:
    col, label, _, kind = spec[:4]
    key = f"bt_{sport}_f_{col}"
    if kind == "cat":
        values = sorted(sel[col].dropna().unique().tolist()) if col in sel.columns else []
        if key in ss:
            ss[key] = [v for v in ss[key] if v in values]
        picked = st.multiselect(label, values, key=key, placeholder="tutti")
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
    cols = TRADE_COLS[sport] + ["side", "odds", "close_odds", "clv_pct", "won", "risk", "pnl", "equity"]
    out = trades[[c for c in cols if c in trades.columns]].copy()
    out["date"] = out["date"].dt.date
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
          ("Win Rate", f"{m['win_rate_pct']:.1f}%", f"quota media {m['avg_odds']:.2f}", None),
          ("Operazioni", num_it(m["trades"]), f"serie negativa max {m['longest_losing_streak']}", None)])
    if cfg["side"] == "lay":
        st.caption(":material/warning: Lay: il prezzo di banca è la quota back selezionata. Sull'exchange la quota lay reale è più alta "
                   "e bancare a quote da bookmaker non è possibile: usa *Betfair Exchange* dove disponibile, imposta la commissione "
                   "e considera il risultato come limite superiore.")
    if cfg["sport"] == "tennis" and cfg["price"] == "ps":
        st.caption("Tennis: l'unica quota Pinnacle disponibile è quella di chiusura, quindi con ingresso Pinnacle il CLV è 0 per costruzione.")
    elif m["clv_coverage_pct"] < 99:
        st.caption(f"CLV calcolato sul {m['clv_coverage_pct']:.0f}% delle operazioni (quota di chiusura non disponibile per le altre).")
    show(equity_chart(trades))
    show(drawdown_chart(trades))
    mo = res.monthly
    hover = [f"{int(n)} operazioni, yield {y:+.1f}%" for n, y in zip(mo["trades"], mo["yield_pct"])]
    fig = bars_chart(mo["month"], mo["profit"], hover, "Rendimenti mensili (unità)", " u", 270)
    fig.update_xaxes(nticks=14, tickangle=0)
    fig.update_layout(bargap=0.1)
    show(fig)


def tab_analysis(cfg: dict, trades: pd.DataFrame) -> None:
    sport = cfg["sport"]
    dims = {"year": "Anno", "odds_bucket": "Fascia di quota"}
    dims.update({f[0]: f[1] for f in bt.FILTERS[sport] if f[3] == "cat" and f[0] in trades.columns and f[0] != "year"})
    by = st.selectbox("Scomponi il risultato per", list(dims), format_func=dims.get)
    bd = bt.breakdown(trades, by)
    if not bd.empty:
        top = bd if by in ("year", "odds_bucket", "month", "weekday", "season") else bd.nlargest(25, "trades")
        hover = [f"{int(n)} operazioni, profitto {p:+.1f} u" for n, p in zip(top["trades"], top["profit"])]
        show(bars_chart(top[by].astype(str), top["roi_pct"], hover, f"ROI % per {dims[by].lower()}", "%", 330))
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


def tab_trades(cfg: dict, trades: pd.DataFrame) -> None:
    table = trades_table(trades, cfg["sport"])
    shown = table.tail(MAX_TABLE_ROWS)
    if len(table) > MAX_TABLE_ROWS:
        st.caption(f"Mostrate le ultime {num_it(MAX_TABLE_ROWS)} operazioni su {num_it(len(table))}; l'esportazione le contiene tutte.")
    st.dataframe(shown, hide_index=True, width="stretch", height=460, column_config={
        "odds": st.column_config.NumberColumn("quota", format="%.2f"), "close_odds": st.column_config.NumberColumn("chiusura", format="%.2f"),
        "clv_pct": st.column_config.NumberColumn("clv %", format="%.2f"), "pnl": st.column_config.NumberColumn(format="%.3f"),
        "risk": st.column_config.NumberColumn(format="%.2f"), "equity": st.column_config.NumberColumn(format="%.2f"),
        "form10": st.column_config.NumberColumn(format="%.0f"), "form_surf": st.column_config.NumberColumn(format="%.0f"),
        "home_ppg5": st.column_config.NumberColumn(format="%.1f"), "away_ppg5": st.column_config.NumberColumn(format="%.1f"),
    })
    st.download_button("Esporta tutte le operazioni (CSV)", table.to_csv(index=False).encode("utf-8"),
                       file_name=f"operazioni_{cfg['sport']}_{date.today():%Y%m%d}.csv", mime="text/csv", icon=":material/download:")


VIEWS = ["Riepilogo", "Analisi", "Robustezza", "Operazioni", "Salva"]


def page_backtester() -> None:
    header("Backtester")
    if need_db():
        return
    cfg = sidebar_config()
    args = (cfg["sport"], cfg["market"], cfg["price"], cfg["price_timing"])
    bank = float(ss.get("bt_bank", 100.0))
    key = json.dumps([cfg, bank, db_version()], sort_keys=True, default=str)
    if ss.get("_bt_key") != key:  # ricalcola solo quando cambia la strategia, non a ogni cambio di vista
        ss["_bt_res"] = bt.backtest_selections(selections(*args, db_version()), cfg, bank)
        ss["_bt_key"] = key
        ss.pop("_bt_scan", None)
    res = ss["_bt_res"]
    ss["last"] = {"cfg": cfg, "metrics": res.metrics, "trades": res.trades}
    st.caption(describe(cfg))
    if res.metrics["trades"] == 0:
        st.warning("Nessun match soddisfa i filtri selezionati: allarga i criteri nel pannello laterale o premi *Azzera*.")
        return
    ss.setdefault("bt_view", VIEWS[0])
    view = st.segmented_control("Vista", VIEWS, key="bt_view", label_visibility="collapsed") or VIEWS[0]
    if view == "Riepilogo":
        tab_summary(cfg, res, bank)
    elif view == "Analisi":
        tab_analysis(cfg, res.trades)
    elif view == "Robustezza":
        tab_robustness(cfg, res, args, bank)
    elif view == "Operazioni":
        tab_trades(cfg, res.trades)
    else:
        st.caption("Salva la strategia per ritrovarla nella pagina Strategie e usarla nello scanner delle partite in programma.")
        save_form("save_bt")


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
            "puntata_exchange": st.column_config.NumberColumn("puntata da inserire", format="%.2f")})
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
STATUS_LABEL = {"open": "Aperta", "won": "Vinta", "lost": "Persa", "void": "Rimborsata"}
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
        status = c[1].selectbox("Esito", lg.RESULTS, format_func=STATUS_LABEL.get)
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

    opened = allb[allb["status"] == "open"]
    st.subheader(f"Giocate aperte ({len(opened)})")
    if opened.empty:
        st.caption("Nessuna giocata in attesa di esito.")
    else:
        st.dataframe(_bets_view(opened).drop(columns=["esito", "profitto", "clv %", "chiusura"]), hide_index=True, width="stretch", column_config=BET_COLS)
        labels = {int(r.id): _bet_label(r) for r in opened.itertuples()}
        c = st.columns([4, 2, 1, 1])
        bid = c[0].selectbox("Giocata da chiudere", list(labels), format_func=labels.get)
        result = c[1].radio("Esito", ["won", "lost", "void"], format_func=STATUS_LABEL.get, horizontal=True)
        close = c[2].number_input("Chiusura", 1.0, 1000.0, None, 0.01, placeholder="facolt.", key="settle_close")
        c[3].markdown("<div style='height:1.75rem'></div>", unsafe_allow_html=True)
        if c[3].button("Chiudi", type="primary", width="stretch"):
            with lg.open_ledger() as con:
                lg.settle_bet(con, bid, result, close)
            _flash(f"Giocata #{bid} chiusa: {STATUS_LABEL[result].lower()}.")

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
PAGES = {
    "backtester": st.Page(page_backtester, title="Backtester", icon=":material/query_stats:", url_path="backtester", default=True),
    "risk": st.Page(page_risk, title="Rischio", icon=":material/casino:", url_path="risk"),
    "vault": st.Page(page_vault, title="Strategie", icon=":material/bookmark:", url_path="vault"),
    "ledger": st.Page(page_ledger, title="Registro", icon=":material/receipt_long:", url_path="registro"),
    "roadmap": st.Page(page_roadmap, title="Roadmap 2027", icon=":material/flag:", url_path="roadmap"),
    "data": st.Page(page_data, title="Dati", icon=":material/database:", url_path="data"),
    "guide": st.Page(page_guide, title="Guida", icon=":material/help:", url_path="guide"),
}
st.navigation(list(PAGES.values())).run()
data_badge()
