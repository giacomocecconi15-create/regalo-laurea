"""Roadmap 2027: il piano dell'anno, per trimestri.

Le voci predefinite sono qui; stato, note e voci aggiunte dall'utente stanno in `data/roadmap.json`
(la cartella `data/` non viene toccata dagli aggiornamenti del codice).
Due binari: "Piattaforma" (cosa aggiungere allo strumento) e "Metodo" (come usarlo per decidere).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import config as C  # noqa: E402

ROADMAP_PATH = C.DATA_DIR / "roadmap.json"
STATUSES = ["todo", "doing", "done"]
STATUS_LABEL = {"todo": "Da fare", "doing": "In corso", "done": "Fatto"}
TRACKS = ["Piattaforma", "Metodo"]
QUARTERS = {
    "Q1": ("Gennaio - Marzo", "Fondamenta", "Rendere lo strumento autonomo e scegliere le strategie candidate, senza rischiare denaro."),
    "Q2": ("Aprile - Giugno", "Automazione", "Togliere il lavoro manuale quotidiano e passare a stake reali ridotti solo dove i numeri reggono."),
    "Q3": ("Luglio - Settembre", "Profondità", "Passare dai filtri a un modello di probabilità e gestire più strategie come un portafoglio."),
    "Q4": ("Ottobre - Dicembre", "Consolidamento", "Tenere solo ciò che ha funzionato dal vivo, chiudere l'anno con i conti e preparare il 2028."),
}

# (id, trimestre, binario, titolo, descrizione, dipendenza esterna o None)
DEFAULT_ITEMS = [
    ("q1-auto-update", "Q1", "Piattaforma", "Aggiornamento automatico dei dati",
     "Il database si aggiorna da solo ogni mattina, senza premere il pulsante.", None),
    ("q1-backup", "Q1", "Piattaforma", "Backup automatico di registro e strategie",
     "Copia giornaliera di gestionale.duckdb, strategies.json e roadmap.json in una cartella a scelta (es. iCloud).", None),
    ("q1-ah-btts", "Q1", "Piattaforma", "Nuovi mercati calcio: handicap asiatico",
     "Le quote dell'handicap asiatico sono già nel database: aggiungerle come mercato del backtester.", None),
    ("q1-candidates", "Q1", "Metodo", "Scegliere 2-3 strategie candidate",
     "Solo strategie che superano i controlli della scheda Robustezza e hanno una logica spiegabile. Salvarle nella pagina Strategie.", None),
    ("q1-rules", "Q1", "Metodo", "Scrivere le regole di bankroll",
     "Banca dedicata, stake per giocata (da Rischio: stake fisso o Kelly frazionario), soglia di stop e drawdown massimo accettato.", None),
    ("q1-paper", "Q1", "Metodo", "Prova a stake simbolico: 100 giocate per strategia",
     "Registrare ogni segnale nel Registro con la quota realmente disponibile e la quota di chiusura, per misurare il CLV dal vivo.", None),

    ("q2-odds-feed", "Q2", "Piattaforma", "Quote del giorno scaricate in automatico",
     "Lo scanner legge le quote da un servizio esterno invece che da un CSV compilato a mano.",
     "Serve scegliere un servizio di quote (chiave API, di norma a pagamento)."),
    ("q2-alerts", "Q2", "Piattaforma", "Avviso quando scatta un segnale",
     "Notifica sul Mac (o email) quando un match del giorno attiva una strategia salvata.", "Dipende dalle quote automatiche."),
    ("q2-ledger-import", "Q2", "Piattaforma", "Importazione dell'estratto conto",
     "Caricare nel Registro lo storico scommesse esportato da exchange e bookmaker, invece di inserirlo a mano.",
     "Dipende dal formato di esportazione dei tuoi conti."),
    ("q2-go-live", "Q2", "Metodo", "Stake reali ridotti sulle strategie promosse",
     "Promuovere solo le strategie con CLV reale positivo su almeno 100-200 giocate. Le altre restano in prova o si scartano.", None),
    ("q2-review", "Q2", "Metodo", "Revisione mensile",
     "Ogni mese: reale contro backtest per strategia nel Registro. Se il CLV reale è negativo per due mesi, sospendere.", None),

    ("q3-model", "Q3", "Piattaforma", "Modello di probabilità",
     "Rating Elo per il tennis (per superficie) e modello dei gol per il calcio: confrontare la probabilità stimata con la quota per trovare valore, oltre ai filtri.", None),
    ("q3-walk-forward", "Q3", "Piattaforma", "Validazione walk-forward",
     "Ricalibrare i filtri su una finestra e verificarli sulla successiva, ripetendo lungo tutto lo storico: riduce il rischio di adattarsi al passato.", None),
    ("q3-portfolio", "Q3", "Piattaforma", "Portafoglio di strategie",
     "Risultato combinato di più strategie, correlazione tra loro e ripartizione della banca.", None),
    ("q3-challenger", "Q3", "Piattaforma", "Quote Challenger e doppi storici",
     "Estendere il backtest tennis ai Challenger.", "Serve una fonte di quote Challenger: quelle gratuite non esistono."),
    ("q3-scale", "Q3", "Metodo", "Adeguare gli stake ai risultati reali",
     "Ricalcolare lo stake con il ROI reale (non quello del backtest) e aumentare solo dopo 300+ giocate coerenti.", None),

    ("q4-prune", "Q4", "Metodo", "Tagliare ciò che non regge",
     "Ritirare le strategie con CLV reale non positivo o drawdown oltre le regole scritte a inizio anno.", None),
    ("q4-annual", "Q4", "Metodo", "Bilancio dell'anno",
     "Profitto, ROI e CLV reali per strategia, sport e conto; confronto con le attese di gennaio.", None),
    ("q4-report", "Q4", "Piattaforma", "Report periodico esportabile",
     "Riepilogo mensile e annuale in un file unico (PDF o Excel) generato dall'app.", None),
    ("q4-installer", "Q4", "Piattaforma", "Versione installabile",
     "Pacchetto unico che non richiede Python installato, per Mac e Windows.",
     "Va costruito e provato su ciascun sistema; su macOS 11 i componenti recenti non sono garantiti."),
    ("q4-plan-2028", "Q4", "Metodo", "Piano 2028",
     "Decidere cosa tenere, cosa approfondire e l'obiettivo dell'anno successivo sulla base dei dati reali.", None),
]


# voci gia' realizzate nell'app: partono come "Fatto" (o "In corso") finche' non le cambi tu
BUILT = {"q1-auto-update": "done", "q1-backup": "done", "q2-alerts": "done", "q3-model": "done", "q3-walk-forward": "done",
         "q3-portfolio": "done", "q2-odds-feed": "doing"}


def _load_state(path: Path | None = None) -> dict:
    path = Path(path or ROADMAP_PATH)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict, path: Path | None = None) -> None:
    path = Path(path or ROADMAP_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def items(path: Path | None = None) -> list[dict]:
    """Voci predefinite + voci dell'utente, con stato e nota correnti."""
    state = _load_state(path)
    saved = state.get("items", {})
    out = []
    for iid, quarter, track, title, desc, needs in DEFAULT_ITEMS:
        s = saved.get(iid, {})
        out.append({"id": iid, "quarter": quarter, "track": track, "title": title, "description": desc, "needs": needs,
                    "status": s.get("status") if s.get("status") in STATUSES else BUILT.get(iid, "todo"),
                    "note": s.get("note", ""), "custom": False})
    for c in state.get("custom", []):
        if c.get("quarter") in QUARTERS and c.get("id"):
            out.append({"id": c["id"], "quarter": c["quarter"], "track": c.get("track", "Metodo"), "title": c.get("title", ""),
                        "description": c.get("description", ""), "needs": None,
                        "status": c.get("status", "todo") if c.get("status") in STATUSES else "todo",
                        "note": c.get("note", ""), "custom": True})
    return out


def update_item(item_id: str, status: str | None = None, note: str | None = None, path: Path | None = None) -> None:
    if status is not None and status not in STATUSES:
        raise ValueError(f"stato non valido: {status}")
    state = _load_state(path)
    target = next((c for c in state.get("custom", []) if c.get("id") == item_id), None)
    if target is None:
        target = state.setdefault("items", {}).setdefault(item_id, {})
    if status is not None:
        target["status"] = status
    if note is not None:
        target["note"] = note
    _save_state(state, path)


def add_item(quarter: str, track: str, title: str, description: str = "", path: Path | None = None) -> str:
    if quarter not in QUARTERS:
        raise ValueError("trimestre non valido")
    if not title.strip():
        raise ValueError("il titolo e' obbligatorio")
    state = _load_state(path)
    custom = state.setdefault("custom", [])
    n = 1 + max([int(c["id"].split("-")[-1]) for c in custom if str(c.get("id", "")).startswith("mio-")] or [0])
    iid = f"mio-{n}"
    custom.append({"id": iid, "quarter": quarter, "track": track if track in TRACKS else "Metodo",
                   "title": title.strip(), "description": description.strip(), "status": "todo", "note": ""})
    _save_state(state, path)
    return iid


def delete_item(item_id: str, path: Path | None = None) -> None:
    """Elimina una voce aggiunta dall'utente (quelle predefinite non si eliminano)."""
    state = _load_state(path)
    state["custom"] = [c for c in state.get("custom", []) if c.get("id") != item_id]
    _save_state(state, path)


def progress(all_items: list[dict]) -> dict:
    """Avanzamento complessivo e per trimestre: {'all': (fatte, totale), 'Q1': (...), ...}."""
    out = {"all": (sum(i["status"] == "done" for i in all_items), len(all_items))}
    for q in QUARTERS:
        sub = [i for i in all_items if i["quarter"] == q]
        out[q] = (sum(i["status"] == "done" for i in sub), len(sub))
    return out
