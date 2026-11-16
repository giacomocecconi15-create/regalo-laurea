"""Sports Trading Lab - dashboard Streamlit.

Avvio:  streamlit run app.py   (oppure start_app.sh / start_app.bat)
"""
from __future__ import annotations

import subprocess
import sys
from contextlib import closing
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from src import backtester as bt  # noqa: E402
from src import config as C  # noqa: E402
from src import risk  # noqa: E402
from src import strategies as sv  # noqa: E402

# palette (tema scuro): superfici, inchiostri e serie validate per daltonismo
SURFACE, GRID, INK, MUTED = "#1a1a19", "#2c2c2a", "#f0efec", "#898781"
BLUE, ORANGE, RED = "#3987e5", "#d95926", "#e66767"

SPORT_LABEL = {"tennis": "Tennis", "football": "Calcio"}
MARKET_LABEL = {"h2h": "Testa a Testa", "1x2": "1X2", "ou25": "Over/Under 2.5"}
SEL_LABEL = {"favourite": "Favorito", "underdog": "Sfavorito", "home": "1 (Casa)", "draw": "X (Pareggio)",
             "away": "2 (Ospite)", "over": "Over 2.5", "under": "Under 2.5"}

st.set_page_config(page_title="Sports Trading Lab", page_icon=":material/monitoring:", layout="wide")
st.markdown("""
<style>
[data-testid="stMetric"] {background:#1a1a19;border:1px solid #2c2c2a;border-radius:6px;padding:12px 14px;}
[data-testid="stMetricLabel"] p {color:#898781;font-size:0.72rem;letter-spacing:.08em;text-transform:uppercase;}
[data-testid="stMetricValue"] {font-variant-numeric:tabular-nums;font-size:1.2rem;}
.block-container {padding-top:2.2rem;}
h1 {font-size:1.5rem !important;letter-spacing:.02em;}
</style>
""", unsafe_allow_html=True)

# i widget del backtester mantengono lo stato quando si cambia pagina
for _k in list(st.session_state.keys()):
    if _k.startswith("bt_"):
        st.session_state[_k] = st.session_state[_k]


# --------------------------------------------------------------------------- dati
def db_ready() -> bool:
    return C.DB_PATH.exists()


def db_version() -> float:
    return C.DB_PATH.stat().st_mtime if db_ready() else 0.0


@st.cache_data(show_spinner="Carico i match dal database...")
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


def need_db() -> bool:
    if not db_ready():
        st.warning("Database non trovato. Apri **Data Manager** e premi *Aggiorna Database*.")
        return True
    return False


# --------------------------------------------------------------------------- grafici
def base_layout(fig: go.Figure, height: int, title: str | None = None) -> go.Figure:
    fig.update_layout(
        height=height, paper_bgcolor=SURFACE, plot_bgcolor=SURFACE, font=dict(color=INK, size=12),
        margin=dict(l=56, r=20, t=44 if title else 16, b=40), hovermode="x unified",
        title=dict(text=title, font=dict(size=14), x=0.01) if title else None,
        hoverlabel=dict(bgcolor="#0d0d0d", bordercolor=GRID, font=dict(color=INK)), showlegend=False,
    )
    fig.update_xaxes(gridcolor=GRID, linecolor=GRID, zeroline=False, tickfont=dict(color=MUTED))
    fig.update_yaxes(gridcolor=GRID, linecolor=GRID, zerolinecolor=MUTED, zerolinewidth=1, tickfont=dict(color=MUTED))
    return fig


def equity_chart(trades: pd.DataFrame) -> go.Figure:
    fig = go.Figure(go.Scatter(
        x=trades["date"], y=trades["equity"], mode="lines", line=dict(color=BLUE, width=2, shape="hv"),
        customdata=np.arange(1, len(trades) + 1), hovertemplate="%{y:+.2f} u  (trade n. %{customdata})<extra></extra>"))
    base_layout(fig, 360, "Equity curve - profitto cumulato (unità)")
    fig.update_yaxes(ticksuffix=" u")
    return fig


def drawdown_chart(trades: pd.DataFrame) -> go.Figure:
    eq = trades["equity"].to_numpy()
    dd = eq - np.maximum.accumulate(np.maximum(eq, 0))
    fig = go.Figure(go.Scatter(x=trades["date"], y=dd, mode="lines", line=dict(color=RED, width=1.5, shape="hv"),
                               fill="tozeroy", fillcolor="rgba(230,103,103,0.25)",
                               hovertemplate="%{y:.2f} u<extra></extra>"))
    base_layout(fig, 220, "Drawdown dal massimo (unità)")
    fig.update_yaxes(ticksuffix=" u")
    return fig


def monthly_chart(monthly: pd.DataFrame) -> go.Figure:
    colors = np.where(monthly["profit"] >= 0, BLUE, RED)
    fig = go.Figure(go.Bar(
        x=monthly["month"], y=monthly["profit"], marker=dict(color=colors, line=dict(color=SURFACE, width=1)),
        customdata=np.stack([monthly["trades"], monthly["yield_pct"]], axis=1),
        hovertemplate="%{y:+.2f} u<br>%{customdata[0]} trade, yield %{customdata[1]:+.1f}%<extra></extra>"))
    base_layout(fig, 280, "Rendimenti mensili (unità) - blu positivo, rosso negativo")
    fig.update_xaxes(type="category", nticks=14)
    fig.update_yaxes(ticksuffix=" u")
    return fig


def _rgba(hex_color: str, a: float) -> str:
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{a})"


def fan_chart(results: list[tuple[str, dict, str]]) -> go.Figure:
    fig = make_subplots(rows=1, cols=len(results), subplot_titles=[r[0] for r in results], horizontal_spacing=0.08)
    for i, (_, res, color) in enumerate(results, start=1):
        b = res["bands"]
        for lo, hi, alpha, name in (("p5", "p95", 0.15, "5°-95° percentile"), ("p25", "p75", 0.30, "25°-75° percentile")):
            fig.add_trace(go.Scatter(x=b["trade"], y=b[hi], mode="lines", line=dict(width=0), hoverinfo="skip"), 1, i)
            fig.add_trace(go.Scatter(x=b["trade"], y=b[lo], mode="lines", line=dict(width=0), fill="tonexty",
                                     fillcolor=_rgba(color, alpha), hoverinfo="skip", name=name), 1, i)
        fig.add_trace(go.Scatter(
            x=b["trade"], y=b["p50"], mode="lines", line=dict(color=color, width=2),
            customdata=np.stack([b["p5"], b["p95"]], axis=1),
            hovertemplate="mediana %{y:,.0f}<br>5°-95°: %{customdata[0]:,.0f} - %{customdata[1]:,.0f}<extra></extra>"), 1, i)
        fig.add_hline(y=res["bankroll"], line=dict(color=MUTED, width=1, dash="dot"), row=1, col=i)
    base_layout(fig, 380)
    fig.update_layout(margin=dict(t=48))
    fig.update_xaxes(title_text="operazioni future", title_font=dict(color=MUTED, size=11))
    return fig


def dd_hist_chart(results: list[tuple[str, dict, str]]) -> go.Figure:
    fig = make_subplots(rows=1, cols=len(results), subplot_titles=[r[0] for r in results], horizontal_spacing=0.08)
    top = max(float(np.percentile(r[1]["max_dd_pct"], 99.5)) for r in results)
    for i, (_, res, color) in enumerate(results, start=1):
        fig.add_trace(go.Histogram(
            x=res["max_dd_pct"], xbins=dict(start=0, end=max(top, 5), size=max(top, 5) / 40),
            marker=dict(color=color, line=dict(color=SURFACE, width=1)),
            hovertemplate="drawdown %{x}%<br>%{y} simulazioni<extra></extra>"), 1, i)
        fig.add_vline(x=res["p95_dd_pct"], line=dict(color=INK, width=1, dash="dot"), row=1, col=i,
                      annotation_text=f"95° pct: {res['p95_dd_pct']:.1f}%", annotation_font=dict(color=INK, size=11))
    base_layout(fig, 320)
    fig.update_layout(margin=dict(t=48), hovermode="closest", bargap=0.05)
    fig.update_xaxes(title_text="max drawdown del percorso (%)", title_font=dict(color=MUTED, size=11), ticksuffix="%")
    return fig


def show(fig: go.Figure) -> None:
    st.plotly_chart(fig, width="stretch", theme=None, config={"displaylogo": False})


def money(v: float) -> str:
    return f"{v:,.0f}".replace(",", ".")


def fmt(v: float, pattern: str = "{:+.2f}") -> str:
    return "n/d" if v is None or (isinstance(v, float) and not np.isfinite(v)) else pattern.format(v)


# --------------------------------------------------------------------------- pagina 1
def page_data() -> None:
    st.title("Data Manager")
    if db_ready():
        s = db_status(db_version())
        t = s["tennis"].set_index("circuit")
        n = lambda c: int(t["match"].get(c, 0))
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Match ATP", f"{n('ATP'):,}".replace(",", "."))
        c2.metric("Match Challenger", f"{n('CHALLENGER'):,}".replace(",", "."))
        c3.metric("Qualificazioni ATP", f"{n('ATP_QUALI'):,}".replace(",", "."))
        c4.metric("Match Calcio", f"{int(s['football']['match'].sum()):,}".replace(",", "."))
        c5.metric("Ultimo aggiornamento", s["last"].replace(" UTC", ""))
        st.caption(f"Database: `{C.DB_PATH.relative_to(ROOT)}` - {C.DB_PATH.stat().st_size / 1e6:.1f} MB")
    else:
        st.info("Nessun database presente: premi il pulsante per scaricare gli storici.")

    with st.container(border=True):
        a, b = st.columns([1, 3])
        full = b.checkbox("Riscarica tutto da zero (ignora la cache dei file già scaricati)", value=False)
        if a.button("Aggiorna Database", type="primary", icon=":material/sync:"):
            run_update(full)

    if db_ready():
        s = db_status(db_version())
        st.subheader("Tennis")
        st.dataframe(s["tennis"].rename(columns={"circuit": "circuito"}), hide_index=True, width="stretch")
        st.caption("Le quote (Pinnacle, Bet365, massimo e media mercato) esistono solo per il circuito ATP: "
                   "tennis-data.co.uk non pubblica quote Challenger. I Challenger hanno risultati, ranking e statistiche di servizio.")
        pivot = s["tennis_year"].pivot(index="anno", columns="circuit", values="n").fillna(0).astype(int)
        st.dataframe(pivot, width="stretch")
        st.subheader("Calcio")
        st.dataframe(s["football"], hide_index=True, width="stretch")


def run_update(full: bool) -> None:
    cmd = [sys.executable, str(ROOT / "src" / "update_data.py")] + (["--full"] if full else [])
    bar = st.progress(0.0, text="Avvio aggiornamento...")
    tail: list[str] = []
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    for line in proc.stdout:
        line = line.rstrip()
        if line.startswith("PROGRESS "):
            _, frac, label = line.split(" ", 2)
            i, n = (int(x) for x in frac.split("/"))
            bar.progress(min(i / n, 1.0), text=f"{label}  ({i}/{n})")
        elif line:
            tail.append(line)
    code = proc.wait()
    st.cache_data.clear()
    if code == 0:
        bar.progress(1.0, text="Aggiornamento completato")
        st.success("Database aggiornato e validato.")
    else:
        st.error("L'aggiornamento ha segnalato problemi: controlla il log qui sotto.")
    st.code("\n".join(tail[-40:]) or "(nessun output)", language="text")


# --------------------------------------------------------------------------- pagina 2
def sidebar_config() -> dict:
    sb = st.sidebar
    sb.header("Strategia")
    sport = sb.radio("Sport", list(SPORT_LABEL), format_func=SPORT_LABEL.get, horizontal=True, key="bt_sport")
    market = sb.selectbox("Mercato", bt.MARKETS[sport], format_func=MARKET_LABEL.get, key=f"bt_{sport}_market")
    opts = bt.SELECTIONS[market]
    ss = st.session_state  # i default passano dallo stato di sessione, cosi' sopravvivono al cambio pagina
    ss.setdefault(f"bt_{sport}_{market}_sel", opts[:1])
    selection = sb.multiselect("Selezione", opts, format_func=SEL_LABEL.get, key=f"bt_{sport}_{market}_sel")
    side = sb.radio("Modalità", ["back", "lay"], format_func={"back": "Back (punta)", "lay": "Lay (banca)"}.get,
                    horizontal=True, key="bt_side")
    ss.setdefault("bt_comm", 0.0)
    commission = sb.number_input("Commissione Exchange sulle vincite (%)", 0.0, 20.0, step=0.5, key="bt_comm")
    lay_mode = "liability"
    if side == "lay":
        lay_mode = sb.radio("Stake del lay", ["liability", "stake"], key="bt_laymode",
                            format_func={"liability": "Responsabilità fissa 1u", "stake": "Puntata banco fissa 1u"}.get)
    books = bt.PRICE_BOOKS[sport]
    ss.setdefault(f"bt_{sport}_price", "avg")
    price = sb.selectbox("Quota di ingresso", books, format_func=bt.BOOK_LABELS.get, key=f"bt_{sport}_price")
    timing = "open"
    if sport == "football":
        timing = sb.radio("Momento della quota", ["open", "close"], horizontal=True, key="bt_timing",
                          format_func={"open": "Pre-match (apertura)", "close": "Chiusura"}.get)

    sel = selections(sport, market, price, timing, db_version())
    c1, c2 = sb.columns(2)
    ss.setdefault("bt_omin", 1.01)
    ss.setdefault("bt_omax", 50.0)
    odds_min = c1.number_input("Quota min", 1.01, 1000.0, step=0.05, key="bt_omin")
    odds_max = c2.number_input("Quota max", 1.01, 1000.0, step=0.05, key="bt_omax")
    dmin, dmax = sel["date"].min().date(), sel["date"].max().date()
    ss.setdefault(f"bt_{sport}_period", (dmin, dmax))
    period = sb.date_input("Periodo", min_value=dmin, max_value=dmax, key=f"bt_{sport}_period")
    d_from, d_to = (period if isinstance(period, tuple) and len(period) == 2 else (dmin, dmax))

    filters: dict = {}
    cols = bt.FILTER_COLUMNS[sport]
    sb.subheader("Filtri")
    for col, (label, kind) in cols.items():
        if kind != "cat":
            continue
        values = sorted(sel[col].dropna().unique().tolist())
        picked = sb.multiselect(label, values, key=f"bt_{sport}_{col}", placeholder="tutti")
        if picked:
            filters[col] = {"in": picked}
    with sb.expander("Ranking, form e statistiche pre-match"):
        st.caption("Lascia vuoto per non filtrare. I match senza il dato vengono esclusi dal filtro.")
        for col, (label, kind) in cols.items():
            if kind != "num":
                continue
            st.markdown(f"<small>{label}</small>", unsafe_allow_html=True)
            a, b = st.columns(2)
            lo = a.number_input("min", value=None, key=f"bt_{sport}_{col}_min", label_visibility="collapsed", placeholder="min")
            hi = b.number_input("max", value=None, key=f"bt_{sport}_{col}_max", label_visibility="collapsed", placeholder="max")
            if lo is not None or hi is not None:
                filters[col] = {"min": lo, "max": hi}
    excl = sb.checkbox("Escludi i match finiti per ritiro", key="bt_excl") if sport == "tennis" else False
    ss.setdefault("bt_bank", 100.0)
    sb.number_input("Banca di riferimento per il drawdown % (unità)", 10.0, 100000.0, step=10.0, key="bt_bank",
                    help="Ogni operazione rischia 1 unità: il drawdown % è calcolato su una banca iniziale di questa dimensione.")
    return {
        "sport": sport, "market": market, "selection": selection, "side": side, "price": price,
        "price_timing": timing, "commission": commission / 100, "lay_mode": lay_mode,
        "odds_min": odds_min, "odds_max": odds_max, "date_from": str(d_from), "date_to": str(d_to),
        "exclude_retirements": bool(excl), "filters": filters,
    }


def describe(cfg: dict) -> str:
    sel = ", ".join(SEL_LABEL.get(s, s) for s in cfg.get("selection") or []) or "tutte le selezioni"
    parts = [SPORT_LABEL[cfg["sport"]], MARKET_LABEL[cfg["market"]], f"{'Lay' if cfg['side'] == 'lay' else 'Back'} {sel}",
             f"quota {cfg['odds_min']:.2f}-{cfg['odds_max']:.2f} ({bt.BOOK_LABELS[cfg['price']]})"]
    if cfg.get("commission"):
        parts.append(f"comm. {cfg['commission'] * 100:.1f}%")
    for col, rule in (cfg.get("filters") or {}).items():
        label = bt.FILTER_COLUMNS[cfg["sport"]].get(col, (col,))[0]
        if rule.get("in"):
            parts.append(f"{label}: {', '.join(map(str, rule['in']))}")
        else:
            parts.append(f"{label}: {rule.get('min') if rule.get('min') is not None else '-inf'}..{rule.get('max') if rule.get('max') is not None else '+inf'}")
    return " | ".join(parts)


TRADE_COLS = {
    "tennis": ["date", "competition", "surface", "round", "event", "player", "selection", "rank", "opp_rank", "form10"],
    "football": ["date", "league", "event", "selection", "home_ppg5", "away_ppg5"],
}


def trades_table(trades: pd.DataFrame, sport: str) -> pd.DataFrame:
    cols = TRADE_COLS[sport] + ["side", "odds", "close_odds", "clv_pct", "won", "risk", "pnl", "equity"]
    out = trades[[c for c in cols if c in trades.columns]].copy()
    out["date"] = out["date"].dt.date
    return out


def save_form(key: str) -> None:
    last = st.session_state.get("last")
    if not last or last["metrics"]["trades"] == 0:
        st.caption("Esegui prima un backtest con almeno un'operazione.")
        return
    with st.form(key, border=False):
        name = st.text_input("Nome della strategia", placeholder="es. Favoriti terra 1.40-1.80")
        if st.form_submit_button("Salva nel Vault", icon=":material/save:"):
            if not name.strip():
                st.error("Inserisci un nome.")
            else:
                sv.save_strategy(name.strip(), last["cfg"], last["metrics"])
                st.success(f"Strategia '{name.strip()}' salvata in strategies.json")


def page_backtester() -> None:
    st.title("Strategy Backtester")
    if need_db():
        return
    cfg = sidebar_config()
    sel = selections(cfg["sport"], cfg["market"], cfg["price"], cfg["price_timing"], db_version())
    bank = float(st.session_state.get("bt_bank", 100.0))
    res = bt.backtest_selections(sel, cfg, bank)
    m, trades = res.metrics, res.trades
    st.session_state["last"] = {"cfg": cfg, "metrics": m, "trades": trades}
    st.caption(describe(cfg))
    if m["trades"] == 0:
        st.warning("Nessun match soddisfa i filtri selezionati: allarga i criteri nel pannello laterale.")
        return

    k = st.columns(6)
    k[0].metric("ROI / Yield", f"{m['roi_pct']:+.2f}%")
    k[1].metric("Profitto", f"{m['profit']:+.2f} u")
    k[2].metric("CLV medio", fmt(m["clv_pct"], "{:+.2f}%"))
    k[3].metric("Max Drawdown", f"-{m['max_dd_units']:.1f} u")
    k[4].metric("Win Rate", f"{m['win_rate_pct']:.1f}%")
    k[5].metric("Operazioni", f"{m['trades']:,}".replace(",", "."))
    k = st.columns(6)
    k[0].metric("Quota media", f"{m['avg_odds']:.2f}")
    k[1].metric("Rischiato", f"{m['staked']:,.0f} u".replace(",", "."))
    k[2].metric("EV su chiusura", fmt(m["ev_close_pct"], "{:+.2f}%"))
    k[3].metric("Max DD %", f"-{m['max_dd_pct']:.1f}%")
    k[4].metric("Serie negativa", f"{m['longest_losing_streak']}")
    k[5].metric("t-stat", fmt(m["t_stat"]))
    st.caption(f"Stake 1 unità di rischio per operazione. Max DD % riferito a una banca di {bank:.0f} u (100% = banca azzerata). "
               "EV su chiusura: valore atteso rispetto alla probabilità di chiusura senza margine.")
    if cfg["side"] == "lay":
        st.caption(":material/warning: Lay: il prezzo di banca è la quota back selezionata. Sull'exchange la quota lay reale è più alta "
                   "(spread) e bancare a quote da bookmaker non è possibile: usa *Betfair Exchange* come quota dove disponibile, "
                   "imposta la commissione e considera il risultato come limite superiore.")
    if cfg["sport"] == "tennis" and cfg["price"] == "ps":
        st.caption("Tennis: l'unica quota Pinnacle disponibile è quella di chiusura, quindi con ingresso Pinnacle il CLV è 0 per costruzione.")
    elif m["clv_coverage_pct"] < 99:
        st.caption(f"CLV calcolato sul {m['clv_coverage_pct']:.0f}% delle operazioni (quota di chiusura non disponibile per le altre).")

    show(equity_chart(trades))
    show(drawdown_chart(trades))
    show(monthly_chart(res.monthly))

    st.subheader("Operazioni")
    table = trades_table(trades, cfg["sport"])
    st.dataframe(table, hide_index=True, width="stretch", height=380, column_config={
        "odds": st.column_config.NumberColumn(format="%.2f"), "close_odds": st.column_config.NumberColumn(format="%.2f"),
        "clv_pct": st.column_config.NumberColumn("clv %", format="%.2f"), "pnl": st.column_config.NumberColumn(format="%.3f"),
        "risk": st.column_config.NumberColumn(format="%.2f"), "equity": st.column_config.NumberColumn(format="%.2f"),
        "form10": st.column_config.NumberColumn(format="%.2f"),
    })
    a, b = st.columns([1, 3])
    a.download_button("Esporta operazioni (CSV)", table.to_csv(index=False).encode("utf-8"),
                      file_name=f"trades_{cfg['sport']}_{date.today():%Y%m%d}.csv", mime="text/csv", icon=":material/download:")
    with b.expander("Salva questa strategia nel Vault"):
        save_form("save_bt")


# --------------------------------------------------------------------------- pagina 3
def page_risk() -> None:
    st.title("Risk & Monte Carlo Simulator")
    last = st.session_state.get("last")
    if not last or last["metrics"]["trades"] == 0:
        st.info("Esegui prima un backtest nella pagina **Strategy Backtester**: la simulazione usa i suoi risultati.")
        return
    m, trades = last["metrics"], last["trades"]
    st.caption(describe(last["cfg"]))
    st.caption(f"Base storica: {m['trades']} operazioni, ROI {m['roi_pct']:+.2f}%, win rate {m['win_rate_pct']:.1f}%.")
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

    summary = pd.DataFrame({
        "Metrica": ["Risk of Ruin", "Probabilità di chiudere in utile", "Bankroll finale mediano", "Bankroll finale 5° percentile",
                    "Bankroll finale 95° percentile", "Max drawdown mediano", "Max drawdown 95° percentile", "Stake medio"],
        "Stake fisso": [f"{flat['risk_of_ruin_pct']:.1f}%", f"{flat['prob_profit_pct']:.1f}%", money(flat['median_final']),
                        money(flat['p5_final']), money(flat['p95_final']), f"{flat['median_dd_pct']:.1f}%",
                        f"{flat['p95_dd_pct']:.1f}%", f"{flat['mean_stake_pct']:.2f}% della banca iniziale"],
        "Kelly frazionario": [f"{kelly['risk_of_ruin_pct']:.1f}%", f"{kelly['prob_profit_pct']:.1f}%", money(kelly['median_final']),
                              money(kelly['p5_final']), money(kelly['p95_final']), f"{kelly['median_dd_pct']:.1f}%",
                              f"{kelly['p95_dd_pct']:.1f}%", f"{kelly['mean_stake_pct']:.2f}% della banca corrente"],
    })
    k = st.columns(4)
    k[0].metric("Risk of Ruin - stake fisso", f"{flat['risk_of_ruin_pct']:.1f}%")
    k[1].metric("Risk of Ruin - Kelly", f"{kelly['risk_of_ruin_pct']:.1f}%")
    k[2].metric("Mediana finale - stake fisso", money(flat['median_final']).replace(",", "."))
    k[3].metric("Mediana finale - Kelly", money(kelly['median_final']).replace(",", "."))
    st.dataframe(summary, hide_index=True, width="stretch")

    panels = [(f"Stake fisso {stake_pct:.1f}%", flat, BLUE), (f"Kelly x{kf:g}", kelly, ORANGE)]
    st.subheader("Evoluzione del bankroll - 1.000 simulazioni")
    st.caption("Linea: mediana. Fascia scura: 25°-75° percentile. Fascia chiara: 5°-95° percentile. Tratteggio: bankroll iniziale.")
    show(fan_chart(panels))
    st.subheader("Distribuzione del max drawdown atteso")
    show(dd_hist_chart(panels))
    st.caption("Metodo: bootstrap con reinserimento delle operazioni storiche (1.000 percorsi, seme fisso). "
               "Kelly stima l'edge con il ROI storico: se quel ROI è sovrastimato (overfitting dei filtri), Kelly sovra-punta. "
               "I rendimenti passati non garantiscono quelli futuri.")


# --------------------------------------------------------------------------- pagina 4
def page_vault() -> None:
    st.title("Strategy Vault & Pre-Match Scanner")
    vault = sv.load_vault()
    st.subheader("Strategie salvate")
    if vault:
        rows = [{"nome": n, "sport": SPORT_LABEL.get(e["config"].get("sport"), "?"), "regole": describe(e["config"]),
                 "operazioni": e["metrics"].get("trades"), "ROI %": e["metrics"].get("roi_pct"),
                 "CLV %": e["metrics"].get("clv_pct"), "max DD (u)": e["metrics"].get("max_dd_units"),
                 "salvata il": e.get("saved_at")} for n, e in vault.items()]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", column_config={
            "ROI %": st.column_config.NumberColumn(format="%+.2f"), "CLV %": st.column_config.NumberColumn(format="%+.2f"),
            "max DD (u)": st.column_config.NumberColumn(format="%.1f")})
        a, b = st.columns([3, 1])
        victim = a.selectbox("Elimina strategia", list(vault), index=None, placeholder="scegli...", label_visibility="collapsed")
        if b.button("Elimina", disabled=victim is None, icon=":material/delete:"):
            sv.delete_strategy(victim)
            st.rerun()
    else:
        st.info("Nessuna strategia salvata. Testa una strategia nel Backtester e salvala qui sotto.")
    with st.expander("Salva l'ultima strategia testata", expanded=not vault):
        last = st.session_state.get("last")
        if last:
            st.caption(describe(last["cfg"]))
        save_form("save_vault")

    st.subheader("Pre-Match Scanner")
    c = st.columns([2, 1, 1, 1])
    upload = c[0].file_uploader("CSV con le quote del giorno", type=["csv"])
    bankroll = c[1].number_input("Bankroll", 1.0, 1e9, 1000.0, 100.0)
    method = c[2].radio("Staking", ["flat", "kelly"], format_func={"flat": "Stake fisso", "kelly": "Kelly frazionario"}.get)
    if method == "flat":
        flat_pct, kf, cap = c[3].number_input("Stake (% banca)", 0.1, 25.0, 1.0, 0.1), 0.25, 5.0
    else:
        kf = c[3].selectbox("Frazione", [0.125, 0.25, 0.5], index=1, format_func=lambda x: f"{x:g} Kelly")
        flat_pct, cap = 1.0, c[3].number_input("Tetto (%)", 0.5, 25.0, 5.0, 0.5)
    t1, t2, _ = st.columns([1, 1, 3])
    t1.download_button("Modello CSV tennis", sv.TENNIS_TEMPLATE.to_csv(index=False).encode(), "quote_tennis_modello.csv", "text/csv")
    t2.download_button("Modello CSV calcio", sv.FOOTBALL_TEMPLATE.to_csv(index=False).encode(), "quote_calcio_modello.csv", "text/csv")
    if upload is None:
        st.caption("Carica un CSV (separatore virgola o punto e virgola). Colonne minime - tennis: player1, player2, odds1, odds2; "
                   "calcio: home_team, away_team, odds_home, odds_draw, odds_away. Ranking e form mancanti vengono letti dal database.")
        return
    try:
        df = pd.read_csv(upload, sep=None, engine="python")
    except Exception as exc:  # file malformato
        st.error(f"Impossibile leggere il CSV: {exc}")
        return
    if not vault:
        st.warning("Salva almeno una strategia per poter generare segnali.")
        return
    if db_ready():
        with closing(bt.connect()) as con:
            signals, notes = sv.scan(df, vault, bankroll, con, method, flat_pct, kf, cap)
    else:
        signals, notes = sv.scan(df, vault, bankroll, None, method, flat_pct, kf, cap)
    for n in dict.fromkeys(notes):
        st.caption(":material/info: " + n)
    k = st.columns(3)
    k[0].metric("Match nel file", len(df))
    k[1].metric("Segnali attivi", len(signals))
    k[2].metric("Esposizione totale", f"{signals['importo_rischio'].sum():,.2f}" if len(signals) else "0")
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
    with st.expander("File caricato (righe con segnale evidenziate)"):
        hits = set(signals["evento"]) if len(signals) else set()
        norm = df.rename(columns=lambda c: str(c).strip().lower().replace(" ", "_"))
        if "player1" in norm:
            ev = norm["player1"].astype(str).str.strip() + " vs " + norm["player2"].astype(str).str.strip()
            ev2 = norm["player2"].astype(str).str.strip() + " vs " + norm["player1"].astype(str).str.strip()
            flag = ev.isin(hits) | ev2.isin(hits)
        elif "home_team" in norm:
            flag = (norm["home_team"].astype(str).str.strip() + " - " + norm["away_team"].astype(str).str.strip()).isin(hits)
        else:
            flag = pd.Series(False, index=df.index)
        styled = df.style.apply(lambda row: ["background-color: rgba(57,135,229,0.28)" if flag[row.name] else ""] * len(row), axis=1)
        st.dataframe(styled, hide_index=True, width="stretch")


# --------------------------------------------------------------------------- navigazione
pages = [
    st.Page(page_data, title="Data Manager", icon=":material/database:", url_path="data"),
    st.Page(page_backtester, title="Strategy Backtester", icon=":material/query_stats:", url_path="backtester", default=True),
    st.Page(page_risk, title="Risk & Monte Carlo", icon=":material/casino:", url_path="risk"),
    st.Page(page_vault, title="Vault & Scanner", icon=":material/bookmark:", url_path="vault"),
]
st.navigation(pages).run()
