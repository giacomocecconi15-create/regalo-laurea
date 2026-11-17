"""Avvisi su Telegram quando una partita in programma attiva una strategia salvata.

Preparazione (una volta sola, gratis):
  1. su Telegram cerca @BotFather, scrivi /newbot e segui le istruzioni: ricevi un "token";
  2. apri la chat con il tuo nuovo bot e scrivi un messaggio qualsiasi (per esempio "ciao");
  3. nell'app, pagina Strategie, incolla il token e premi "Trova il mio ID".

Uso da riga di comando (lo usa anche l'attivazione automatica su Mac):
    python src/notify.py            # controlla le partite e invia i segnali nuovi
Ogni segnale viene inviato una sola volta (memoria in data/notified.json).
"""
from __future__ import annotations

import json
import sys
from contextlib import closing
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import backtester as bt  # noqa: E402
from src import config as C  # noqa: E402
from src import fixtures as fx  # noqa: E402
from src import strategies as sv  # noqa: E402

API = "https://api.telegram.org/bot{token}/{method}"
SENT_PATH = C.DATA_DIR / "notified.json"


def send(token: str, chat_id: str, text: str, session=None) -> tuple[bool, str]:
    """Invia un messaggio. Ritorna (riuscito, dettaglio)."""
    if not token or not chat_id:
        return False, "Token o ID della chat mancanti."
    try:
        r = (session or requests).post(API.format(token=token.strip(), method="sendMessage"),
                                       json={"chat_id": str(chat_id).strip(), "text": text[:4000]}, timeout=30)
        data = r.json()
        return (True, "Messaggio inviato.") if data.get("ok") else (False, f"Telegram ha risposto: {data.get('description', r.status_code)}")
    except (requests.RequestException, ValueError) as exc:
        return False, f"Telegram non raggiungibile: {exc}"


def find_chat_id(token: str, session=None) -> tuple[str | None, str]:
    """Legge l'ID della chat dall'ultimo messaggio che hai scritto al bot."""
    if not token:
        return None, "Incolla prima il token del bot."
    try:
        r = (session or requests).get(API.format(token=token.strip(), method="getUpdates"), timeout=30)
        data = r.json()
    except (requests.RequestException, ValueError) as exc:
        return None, f"Telegram non raggiungibile: {exc}"
    if not data.get("ok"):
        return None, f"Token non valido: {data.get('description', '')}"
    chats = [u["message"]["chat"]["id"] for u in data.get("result", []) if u.get("message", {}).get("chat", {}).get("id")]
    if not chats:
        return None, "Nessun messaggio trovato: apri la chat con il bot, scrivi qualcosa e riprova."
    return str(chats[-1]), "ID trovato."


def format_signals(signals: pd.DataFrame) -> str:
    lines = [f"Sports Trading Lab: {len(signals)} nuovi segnali"]
    for r in signals.itertuples():
        lines.append(f"\n{r.data}  {r.evento}\n{r.lato} {r.selezione} @ {r.quota:.2f}  ({r.strategia})\nstake {r.importo_rischio:.2f}")
        cond = getattr(r, "condizione_in_partita", "") or ""
        if cond:
            lines.append(cond)
    return "\n".join(lines)


def new_signals(signals: pd.DataFrame, path: Path | None = None) -> pd.DataFrame:
    """Segnali non ancora notificati (e li segna come notificati)."""
    path = Path(path or SENT_PATH)
    try:
        seen = set(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        seen = set()
    if signals.empty:
        return signals
    key = signals["strategia"].astype(str) + "|" + signals["data"].astype(str) + "|" + signals["evento"].astype(str) + "|" + signals["selezione"].astype(str)
    fresh = signals[~key.isin(seen)]
    if len(fresh):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(sorted(seen | set(key))[-5000:]), encoding="utf-8")
    return fresh


def collect_signals(settings: dict, vault: dict, bankroll: float = 1000.0) -> pd.DataFrame:
    """Scarica le partite in programma (calcio; tennis se c'e' la chiave) e applica le strategie salvate."""
    frames = []
    with closing(bt.connect()) as con:
        foot, _ = fx.fetch_football_fixtures("avg")
        if len(foot):
            frames.append(sv.scan(foot, vault, bankroll, con)[0])
        if settings.get("odds_api_key") and any(e.get("config", {}).get("sport") == "tennis" for e in vault.values()):
            ten, _ = fx.fetch_tennis_odds(settings["odds_api_key"], "avg")
            if len(ten):
                frames.append(sv.scan(ten, vault, bankroll, con)[0])
    frames = [f for f in frames if len(f)]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def run_alerts(session=None) -> str:
    settings, vault = fx.load_settings(), sv.load_vault()
    token, chat = settings.get("telegram_token", ""), settings.get("telegram_chat_id", "")
    if not token or not chat:
        return "Avvisi non configurati: imposta token e ID nella pagina Strategie."
    if not vault:
        return "Nessuna strategia salvata."
    if not C.DB_PATH.exists():
        return "Database assente: apri l'app e aggiorna i dati."
    fresh = new_signals(collect_signals(settings, vault, float(settings.get("alert_bankroll") or 1000)))
    if fresh.empty:
        return "Nessun segnale nuovo."
    ok, detail = send(token, chat, format_signals(fresh), session)
    return f"{len(fresh)} segnali inviati." if ok else detail


if __name__ == "__main__":
    print(run_alerts())
