# Sports Trading Lab

Suite locale in Python per archiviare storici di **tennis (ATP / Challenger)** e **calcio (18 campionati europei)**,
fare backtest di strategie senza scrivere codice, simulare il rischio sul bankroll e scansionare le quote del giorno.

## Avvio rapido

| Sistema | Comando |
|---|---|
| Windows | doppio clic su `start_app.bat` |
| Mac / Linux | `./start_app.sh` |

Al primo avvio lo script crea l'ambiente virtuale, installa le dipendenze, scarica i dati (1-2 minuti) e apre la
dashboard su `http://localhost:8501`. Serve Python 3.10+.

**Mac Intel con macOS 11 o precedente:** serve Python **3.12** (non più recente) e si installa da
`requirements-macos11.txt`; `start_app.sh` lo fa da solo.

**App da doppio clic su Mac.** Dopo la prima installazione, esegui una volta `./crea_app_mac.sh`: crea
*Sports Trading Lab.app* sulla Scrivania. Il doppio clic avvia la piattaforma e la apre in una finestra dedicata
(con Chrome, Edge o Brave; altrimenti nel browser predefinito). Se è già aperta chiede se riaprire la finestra o
spegnerla. L'app punta alla cartella del progetto: se la sposti, riesegui lo script.

Uso manuale:

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python src/update_data.py        # scarica / aggiorna il database
.venv/bin/python -m pytest tests -q        # 60 test
.venv/bin/python -m streamlit run app.py   # dashboard
```

## Struttura

```
app.py                 dashboard Streamlit (Backtester, Oggi, Rischio, Strategie, Registro, Roadmap, Dati, Guida)
src/config.py          percorsi, sorgenti dati, leghe, schema delle tabelle
src/update_data.py     download, pulizia, caricamento incrementale in DuckDB
src/backtester.py      motore di backtesting vettorizzato
src/risk.py            Monte Carlo: stake fisso vs Kelly frazionario
src/strategies.py      Strategy Vault (strategies.json) e Pre-Match Scanner
src/ledger.py          gestionale: conti, movimenti e registro delle giocate reali
src/roadmap.py         roadmap 2027 (voci predefinite e stato salvato)
src/analysis.py        partite del giorno, punto di pareggio, monitor delle strategie, calcolatore di copertura
src/cashout.py         simulazione del cash out (regole manuali e modello intelligente)
src/fixtures.py        partite in programma con quote (calcio gratis, tennis con chiave gratuita) e impostazioni
assets/                icona dell'app
crea_app_mac.sh        crea l'app da doppio clic per Mac
tests/test_engine.py   test dei calcoli, dei parser e dell'integrità del database
data/trading_db.duckdb database (generato, non versionato)   data/raw/  cache dei file scaricati
```

## Dati

| Tabella | Contenuto | Fonte |
|---|---|---|
| `football_matches` | dal 2019/20: risultati, tiri, angoli, falli, cartellini, xG (dove presente), quote 1X2 e O/U 2.5 pre-match e di chiusura (Pinnacle, Bet365, massimo e media mercato, Betfair Exchange), linea asiatica | football-data.co.uk |
| `tennis_matches` | dal 2019: ATP, qualificazioni e Challenger con ranking, punti, statistiche di servizio, punteggio; quote Pinnacle, Bet365, massimo e media mercato per l'ATP | TML-Database / formato Sackmann + tennis-data.co.uk |

Variabili pre-partita calcolate senza look-ahead (usano solo i match precedenti):

- **Calcio**, per casa e ospite: punti a partita nelle ultime 5 (totali e solo in casa / in trasferta), gol fatti e
  subiti, tiri in porta fatti e subiti, calci d'angolo, posizione in classifica, punti a partita in stagione,
  giorni di riposo.
- **Tennis**, per ciascun giocatore: % vittorie nelle ultime 10 e nelle ultime 20 sulla superficie, giorni
  dall'ultimo match, match negli ultimi 14 giorni, precedenti contro l'avversario (dal 2019), % punti vinti al
  servizio e in risposta, % ace e doppi falli (ultimi 10 match). Lo storico Challenger entra in questi calcoli.

Se il database è stato creato da una versione precedente, l'app aggiunge le colonne nuove e ricalcola le
variabili da sola al primo avvio, senza riscaricare nulla.

**Aggiornamento.** Automatico all'apertura dell'app quando i dati hanno più di 12 ore (regolabile o disattivabile
nella pagina Dati), oppure a mano con *Aggiorna ora*. Gira in background senza bloccare l'app: lavora su una copia del
database e la sostituisce solo a fine lavoro, quindi un aggiornamento interrotto non lascia mai i dati a metà.
Scarica in parallelo soltanto la stagione in corso e ricarica nel database solo i file davvero cambiati: di norma
pochi secondi. Un indice `UNIQUE` su `match_id` impedisce i duplicati. Da riga di comando: `--full` forza il
riscaricamento completo, `--sport tennis|football` limita lo sport.

**Partite in programma (gratis).** Lo scanner scarica con un clic `fixtures.csv` di football-data.co.uk: le partite
dei prossimi giorni con quote 1X2, Over/Under 2.5 e handicap asiatico, senza registrazione. Classifica, form, tiri,
angoli e riposo delle squadre vengono calcolati dallo storico con lo stesso codice del backtest, quindi i filtri di
una strategia salvata si applicano alle partite future esattamente come nel test. Per il tennis non esiste una fonte
gratuita senza registrazione: è integrata The Odds API (piano gratuito da 500 crediti al mese, chiave personale;
un credito per torneo), oppure si carica un CSV.

**Nomi dei giocatori.** Le fonti scrivono alcuni nomi in modo diverso ("Auger-Aliassime" / "Auger Aliassime"):
vengono unificati al caricamento, altrimenti form e precedenti dello stesso giocatore risulterebbero spezzati.

**Pulizia.** Encoding misti e BOM, date in due formati, righe malformate, quote ≤ 1 annullate, risultati incoerenti
con i gol scartati. Le quote incoerenti col consenso degli altri bookmaker (refusi nei file originali, prezzi exchange
non liquidi come 1.01) vengono annullate. A fine caricamento gira una validazione che fallisce se trova anomalie.

### Limiti dei dati da conoscere

- **Challenger senza quote.** tennis-data.co.uk pubblica quote solo per l'ATP: i Challenger sono nel database con
  ranking e statistiche, ma non sono backtestabili sul profitto.
- **Tennis: una sola rilevazione di quote** (di chiusura). Il CLV tennis confronta la quota d'ingresso (es. massimo
  mercato) con la chiusura Pinnacle; con ingresso Pinnacle è 0 per costruzione.
- **Calcio "apertura".** Le quote pre-match di football-data sono rilevate qualche giorno prima del match, non
  all'apertura vera del mercato. Pinnacle manca in parte del 2025/26 e nel 2026/27: il CLV ripiega su Betfair
  Exchange e poi sulla media di chiusura (colonna `close_source`).
- **Sorgenti tennis.** Il repository originale `JeffSackmann/tennis_atp` non è più pubblico: le statistiche ATP
  arrivano da TML-Database (stesso formato) e i Challenger 2019-2024 da un mirror; i Challenger dal 2025 non sono
  disponibili. tennis-data.co.uk è il primo tentativo per le quote, con mirror GitHub di riserva (coprono fino a
  luglio 2025). Lo script riprova le fonti ufficiali a ogni esecuzione. I dati sono per uso personale e non commerciale.

## Backtester

```python
from src.backtester import run_backtest
res = run_backtest({
    "sport": "tennis", "market": "h2h", "selection": ["favourite"], "side": "back", "price": "max",
    "odds_min": 1.3, "odds_max": 1.8,
    "filters": {"surface": {"in": ["Clay"]}, "rank": {"max": 30}, "form10": {"min": 0.6}},
})
res.metrics; res.trades; res.equity_curve; res.monthly
```

Mercati: tennis testa a testa; calcio 1X2 e Over/Under 2.5. Metriche: operazioni, win rate, profitto, yield/ROI,
max drawdown (unità e %), serie perdente più lunga, CLV % medio, EV rispetto alla chiusura senza margine, equity
curve, rendimenti mensili. Ogni operazione rischia 1 unità.

- **Back**: vinta `+(quota-1)·(1-commissione)`, persa `-1`.
- **Lay**: responsabilità fissa 1u (puntata del banco `1/(quota-1)`) oppure puntata fissa 1u. Il prezzo di banca è la
  quota back scelta: il risultato reale sull'exchange è un po' peggiore (spread).
- **CLV %**: back `quota/chiusura - 1`, lay `1 - quota/chiusura`.

### Cash out

Il database non contiene quote in corso di partita, quindi le chiusure anticipate si simulano in due modi
(pannello laterale, sezione *4 · Cash out*; risultati nella vista *Cash out*):

- **Manuale** – riproduci le tue abitudini con quattro regole, ognuna con la quota di giocate interessate e
  l'importo: vincenti incassate prima (parte del profitto), vincenti chiuse in perdita, perdenti chiuse in
  profitto, perdenti tagliate (parte della puntata recuperata). Le giocate interessate sono scelte a caso ma in
  modo ripetibile (seme): lo stesso backtest dà sempre lo stesso risultato.
- **Intelligente** – decide da solo usando lo stato reale della partita a un punto di controllo presente nei
  dati: il risultato all'intervallo nel calcio, l'esito del primo set nel tennis. Un modello calibrato sulle
  quote pre-partita (Poisson sui gol del secondo tempo; set indipendenti con una correzione misurata sul
  database) stima la probabilità della selezione a quel punto e quindi il valore del cash out, a cui si toglie
  il margine impostato. Chiude se il valore supera la soglia *incassa* o scende sotto la soglia *taglia*. La
  vista mostra anche il confronto tra soglie diverse.

Sul database le probabilità stimate sono in linea con le frequenze reali (scarti entro pochi punti, verificati
da un test), ma resta una stima: le quote reali in corso di partita possono differire e il cash out vero può
scattare in altri momenti. Chiudere prima costa il margine, quindi in media riduce il rendimento.

**Punto di pareggio.** La vista *Cash out* calcola quanto bisogna spingersi con le chiusure perché la strategia vada
in utile: quale quota di giocate perdenti va tagliata, per ogni livello di puntata recuperata, e una griglia del ROI
atteso. È la soglia teorica (presume di chiudere solo giocate che avrebbero perso); un campo permette di aggiungere il
costo delle vincenti chiuse per errore.

Nel **Registro** una giocata reale si può chiudere come *Cash out* indicando a mano il profitto o la perdita
effettivamente incassati.

## Dashboard

Navigazione in alto tra le pagine; il pannello laterale compare solo nel Backtester, dove servono i filtri. In cima
a ogni pagina c'è lo stato dei dati. Tema scuro, senza animazioni: i mini-grafici nelle schede sono SVG statici.

1. **Backtester** – pannello laterale con esempi pronti e filtri a gruppi (torneo/lega, calendario,
   giocatore/squadra, avversario, confronto). Cinque schede:
   - *Riepilogo*: KPI, equity curve, drawdown, rendimenti mensili.
   - *Analisi*: risultato scomposto per anno, fascia di quota, torneo, superficie...; elenco automatico dei
     segmenti migliori e peggiori, applicabili come filtro con un clic.
   - *Robustezza*: sei controlli (campione, significatività, intervallo di confidenza del ROI, CLV, tenuta
     fuori campione, costanza negli anni), confronto studio/verifica, anno per anno, confronto tra bookmaker.
   - *Cash out*: effetto delle chiusure anticipate rispetto a tenere fino alla fine, per tipo di chiusura e per soglia.
   - *Operazioni*: tabella esportabile in CSV.  *Salva*: archivia la strategia.

   Ogni vista viene calcolata solo quando la apri, e il backtest si ricalcola solo se cambia la strategia.
2. **Oggi** – le partite in programma (calcio) analizzate con lo storico: classifica, form, gol recenti; per ogni
   esito la frequenza con cui si è verificato nei precedenti a quota simile contro la probabilità implicita nella
   quota (*scarto storico*); le strategie salvate che la partita attiva; scheda partita con le variabili delle due
   squadre e le ultime gare.
3. **Rischio** – 1.000 simulazioni bootstrap su 500/1.000 operazioni: stake fisso vs Kelly frazionario,
   risk of ruin, distribuzione del drawdown.
4. **Strategie** – include il *Monitor*: riesegue ogni strategia sui dati aggiornati, confronta ultimi 12 mesi e
   90 giorni con lo storico, segnala quelle in calo o al massimo drawdown, e mostra il portafoglio complessivo
   con la correlazione tra strategie.
   Inoltre: archivio in `strategies.json` (riapribili nel Backtester) e scanner delle partite in programma:
   calcio scaricato con un clic, tennis con chiave gratuita o da CSV; segnali con lo stake calcolato sul bankroll.
5. **Registro** – con *calcolatore di copertura*: data la quota presa e quella attuale, dice quanto puntare o
   bancare per chiudere e il risultato bloccato.
   Inoltre: gestionale delle giocate reali: conti per bookmaker/exchange con depositi e prelievi, giocate
   aperte e chiuse (anche importate dai segnali dello scanner), saldo e disponibile, ROI e CLV reali confrontati
   con quelli del backtest per strategia. I dati stanno in `data/gestionale.duckdb`, separato dagli storici.
6. **Roadmap 2027** – il piano dell'anno per trimestri, su due binari (funzioni della piattaforma e metodo di
   lavoro); lo stato di ogni voce, le note e le voci aggiunte si salvano in `data/roadmap.json`.
7. **Dati** – stato del database e pulsante di aggiornamento con barra di avanzamento.
8. **Guida** – come si usa, in cinque passi.

Un backtest positivo non garantisce risultati futuri: più filtri si provano, più è probabile trovare un risultato
buono per caso. Guarda numero di operazioni, t-stat e CLV prima di fidarti di un ROI.
