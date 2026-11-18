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
.venv/bin/python -m pytest tests -q        # 91 test
.venv/bin/python -m streamlit run app.py   # dashboard
```

## Struttura

```
app.py                 dashboard Streamlit (Backtester, Oggi, Scalping, Quote reali, Rischio, Strategie, Registro, Roadmap, Dati, Guida)
src/config.py          percorsi, sorgenti dati, leghe, schema delle tabelle
src/update_data.py     download, pulizia, caricamento incrementale in DuckDB
src/backtester.py      motore di backtesting vettorizzato
src/risk.py            Monte Carlo: stake fisso vs Kelly frazionario
src/strategies.py      Strategy Vault (strategies.json) e Pre-Match Scanner
src/ledger.py          gestionale: conti, movimenti e registro delle giocate reali
src/roadmap.py         roadmap 2027 (voci predefinite e stato salvato)
src/backup.py          backup e ripristino dei dati personali
src/notify.py          avvisi Telegram sui nuovi segnali
attiva_avvisi_mac.sh   attiva il controllo automatico dei segnali su Mac
src/analysis.py        partite del giorno, punto di pareggio, monitor delle strategie, calcolatore di copertura
src/scalping.py        scalping sul servizio: dati punto per punto, modello delle quote, simulatore
src/betfair.py         importazione dei dati storici Betfair e cash out su prezzi reali
src/cashout.py         uscita a quote per esito o con Take Profit / Stop Loss (break, set, intervallo)
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
| `tennis_matches` | dal 2019: ATP, qualificazioni e Challenger con ranking, punti, statistiche di servizio, punteggio; quote Pinnacle, Bet365, massimo e media mercato per l'ATP | TML-Database / archivio Sackmann + tennis-data.co.uk |

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

**The Odds API in pratica.** È facoltativa e serve solo per le quote tennis del giorno (pagine *Oggi* e *Strategie*):
1. vai su the-odds-api.com, premi *Get API Key* e scegli il piano gratuito *Starter* (nessuna carta di credito);
2. arriva per email una chiave (una stringa di lettere e numeri);
3. incollala nel campo *Chiave di The Odds API* della pagina e scarica le quote: la chiave resta salvata sul Mac in
   `data/settings.json` e non va inserita di nuovo.
Senza chiave tutto il resto funziona; per il tennis si può sempre caricare un CSV. La stessa guida è nell'app, nel
riquadro *Non hai ancora la chiave? Ecco cosa fare*.

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
- **Sorgenti tennis.** Il repository originale `JeffSackmann/tennis_atp` resta il primo tentativo ma oggi non è
  raggiungibile: ATP e Challenger arrivano da TML-Database e dall'archivio completo dei file Sackmann
  (`Aneeshers/tennis-sackmann-archive`, aggiornato a giugno 2026, Challenger 2025-26 compresi). Per ogni anno si
  scaricano tutte le fonti e si tiene la più completa (più match e data più recente). Turni e livelli sono uniformati
  (es. *Quarterfinals* → QF, *Grand Slam*, *Masters 1000*, *ATP 500*, *ATP 250*). tennis-data.co.uk è il primo tentativo per le quote, con mirror GitHub di riserva (coprono fino a
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
  quota **equa** (senza il margine del bookmaker) più lo *spread lay sull'exchange* (2% di default). Prima si bancava
  alla quota back del bookmaker, che contiene il margine: per questo back e lay risultavano entrambi in perdita o con
  segni incoerenti. Ora, a parità di condizioni, back + lay ≈ -(margine + spread + commissione).
- **CLV %**: back `quota/chiusura - 1`, lay `1 - quota/chiusura`.

### Modello di probabilità e incroci

- **Elo interno.** Tennis: media tra Elo generale ed Elo sulla superficie, calcolato su tutti i match (Challenger
  compresi). Calcio: Elo con vantaggio del campo e scarto di gol, tradotto in probabilità 1-X-2 con parametri tarati
  sullo storico. Sempre calcolato prima del match, senza look-ahead.
- **Filtri *Modello***: probabilità del modello e *vantaggio del modello sulla quota* (modello meno probabilità
  implicita senza margine). Sullo storico il mercato è più preciso del modello (tennis: 66% di vincitori indovinati
  contro il 68% delle quote Pinnacle) e puntare dove il modello vede più valore della quota non ha reso: va usato
  come secondo parere, non come segnale.
- **Incrocio esterno (ClubElo).** Nella pagina Oggi le valutazioni pubbliche di clubelo.com danno una seconda stima
  indipendente; la colonna *incrocio modelli* segnala gli esiti su cui le due stime concordano contro la quota.
- **Walk-forward** (vista Robustezza): ogni anno usa solo le fasce di quota che negli anni precedenti erano in utile
  e misura il risultato nell'anno in corso.

### Backup e avvisi

- **Dove stanno i tuoi dati**: `data/gestionale.duckdb` (giocate, conti), `strategies.json` (strategie),
  `data/roadmap.json`, `data/settings.json`. Gli storici (`data/trading_db.duckdb`) si riscaricano e non vanno salvati.
- **Backup**: uno zip al giorno in `data/backup/` all'apertura dell'app (ultimi 14) e, se indicata nella pagina
  Dati, copia in una seconda cartella (iCloud Drive, chiavetta). Per ripristinare, scompatta lo zip nella cartella
  del progetto.
- **Avvisi Telegram**: pagina Strategie. Serve un bot personale creato con @BotFather (gratis). `bash
  attiva_avvisi_mac.sh` attiva il controllo ogni 3 ore anche ad app chiusa; `off` lo disattiva.

### Scalping sul servizio (tennis)

Pagina *Scalping*: punti chi serve a un punteggio scelto (0-0, 0-30, 15-30...) e chiudi dopo N punti oppure a fine
game, con uscita opzionale alla prima palla break. Filtri per superficie, set e probabilità pre-match di chi serve;
spread e commissione configurabili.

- **Dati**: Match Charting Project di Jeff Sackmann (pubblico): circa 3.400 match ATP dal 2019 con ogni punto
  registrato, pari a quasi 89.000 game di servizio. Scaricati con l'aggiornamento dati (circa 90 MB la prima volta,
  poi al massimo una volta a settimana) nella tabella `tennis_service_games`.
- **Quote**: non esistono quote in diretta gratuite, quindi il prezzo a ogni punteggio viene dal modello standard del
  tennis (punti, game, tie-break, set, match). La probabilità di vincere il punto al servizio dei due giocatori è
  tarata sulla quota pre-match e sulla quota reale di servizi tenuti di ogni superficie.
- **Cosa misura**: gli esiti dei punti sono reali, i prezzi sono di modello. La simulazione mostra dove i punti reali
  si scostano dall'ipotesi di punti indipendenti e quanto pesano spread e commissione; non riproduce i movimenti del
  mercato vero né la liquidità.
- La tabella *Punti reali contro modello* confronta, da ogni punteggio, quante volte chi serve tiene davvero il
  servizio con quanto previsto; *Dove rende* prova la stessa regola di uscita da tutti i punteggi di ingresso.

### Piano di trading: ingresso e uscita (pannello principale del Backtester)

In cima al Backtester, sopra i risultati, il riquadro **Piano di trading** riunisce le due scelte che contano di più:
su chi e quando entri, e come esci. A sinistra restano i filtri di quota, torneo, calendario, giocatori.

**Nessun lato fisso.** Se nella *Selezione* non scegli né favorito né sfavorito (o li scegli entrambi), il riquadro
propone *Su chi fa / subisce il primo break* e *Su chi vince / perde il primo set*: in ogni partita si opera sul
giocatore che vive quell'evento, una sola operazione per partita. Il range di quota vale sulla sua quota pre-partita;
punta o banca restano quelli scelti a sinistra. Nel calcio 1X2 lo stesso con la squadra in vantaggio, in parità o in
svantaggio all'intervallo.

**Quando entri.** Tennis: pre-partita, dopo il primo break fatto o subito, dopo il primo set vinto o perso. Calcio 1X2:
pre-partita, all'intervallo in vantaggio, sul pari o in svantaggio. Over/Under: pre-partita o all'intervallo con 0, 1,
2 gol. Il primo set vale per tutti i match completati (un ritiro a metà primo set lascia il dato vuoto); il primo break
esiste solo per i match registrati punto per punto (circa uno su cinque, soprattutto nei tornei maggiori). La squadra
che segna per prima non è disponibile: la fonte non riporta i minuti dei gol.

I range di quota restano sulle quote **pre-partita**, ma con un filtro in partita l'operazione entra **dopo l'evento**
(dopo il break, a fine primo set, all'intervallo) alla quota live stimata, meno un margine configurabile: così ROI e
profitto non usano informazioni future. Il Riepilogo mostra quota media pre-partita, quota live di entrata (mediana),
vittorie reali e probabilità stimata al momento. La vista *In partita* divide le operazioni per situazione di gioco
con il numero di partite, la percentuale di vittorie (conteggio esatto) e il suo intervallo di confidenza al 95%.

**Taratura delle quote live.** Non esistono quote in partita gratuite, e online non si trovano misure affidabili del
calo medio delle quote (solo regole dei trader, per esempio "la quota dello sfavorito che vince il primo set si divide
per 4-4,5"). La stima viene quindi dai modelli dell'app, tarati sui risultati reali: primo set su tutti i match,
primo break sui match punto per punto (chi breakka per primo vince circa 8 punti più di quanto preveda un modello a punti
indipendenti), intervallo sulle partite di calcio 2019-2022 con verifica sul 2023 in poi. Un test controlla che, per ogni
situazione, la probabilità stimata coincida con la frequenza reale (entro 1,5 punti nel calcio e 2 nel tennis).

Lo **Scalping** può partire da una strategia salvata: simula solo i game dei match in cui la strategia avrebbe operato,
scegliendo se serve il giocatore scelto, l'avversario o entrambi, ed eventualmente solo dopo il momento di ingresso.
Lo scanner segnala le strategie con condizioni in partita come "da verificare dal vivo", con la quota live attesa.

### Quote reali (Betfair Exchange)

Pagina *Quote reali*: il cash out simulato su prezzi veri in corso di partita, invece che stimati da un modello.

- **Come avere i dati (gratis)**: con un account Betfair, su historicdata.betfair.com → *My Data* scegli sport
  (Tennis o Soccer), piano **BASIC**, periodo e mercato **Match Odds**, poi scarica l'archivio `.tar`. Nella pagina
  indica dove l'hai salvato (anche una cartella con più archivi) e premi *Importa*. Reimportare non duplica nulla.
- **Cosa si simula**: ingresso all'ultimo prezzo scambiato prima dell'inizio, poi il percorso reale della quota;
  chiusura alla prima rilevazione che supera la soglia di incasso o di taglio, oppure dopo un numero di minuti.
  Back o lay, su favorito, sfavorito o pareggio, con spread e commissione. Confronto con il tenere fino alla fine e
  griglia di tutte le combinazioni di soglie.
- **Limite**: il piano BASIC registra circa un prezzo al minuto, quindi va bene per il cash out ma non per lo scalping
  punto per punto. I dati restano in `data/betfair.duckdb`.

### Uscita: a quote per esito oppure Take Profit / Stop Loss in %

Prima scegli **in quale momento** puoi uscire: al primo break, a fine primo set, a fine secondo set, in ogni momento
disponibile (in ordine dopo l'ingresso), all'intervallo (calcio) oppure *scalping sul servizio*. Un momento che arriva
prima dell'ingresso viene ignorato e l'app lo segnala. Poi **come esci**:

- **A quote che inserisci tu** (consigliato). Per ogni momento e per ogni esito reale indichi la quota a cui chiudi:
  tennis *Take Profit · quota se vince il primo set* e *Stop Loss · quota se perde il primo set* (al primo break: fa /
  subisce il break; per chi banca i due ruoli si invertono); calcio *in vantaggio / in parità / in svantaggio*, per il
  pareggio *pari / non pari*, per l'Over/Under *0, 1, 2 gol* nel primo tempo. Vuoto = con quell'esito non esci. Il ROI di
  ogni operazione si calcola dalla **sua** quota di ingresso (pre-partita, live stimata o quella inserita a mano):
  punta `ingresso/uscita − 1`, banca `(1 − ingresso/uscita)/(ingresso − 1)`, commissione sui profitti. Gli esiti sono
  quelli reali del database, partita per partita. Sotto le quote una tabella mostra per ogni esito quante operazioni lo
  hanno, la quota media stimata dal modello e il ROI con la quota di ingresso mediana; se una quota è molto più
  favorevole della stima compare un avviso (il risultato sarebbe ottimistico).
- **Take Profit / Stop Loss in % di ROI** sull'unità rischiata (SL 25% = esci quando la posizione perde 0,25 u). Il
  controllo avviene solo nel momento scelto, con il valore stimato da modelli tarati sui risultati reali, e si esce al
  valore che la posizione ha allora: a fine set il prezzo si è già mosso molto, quindi l'uscita è spesso ben oltre la
  soglia (es. sfavoriti 2,00-3,50 con SL 25% a fine primo set: uscita media −61%). L'app traduce le soglie in quote
  equivalenti e mostra il ROI medio effettivo delle uscite. Senza margine il risultato coincide con il tenere fino alla
  fine entro circa un punto: il modello non crea né distrugge valore.

*Perché non punto per punto.* Ho provato a ricostruire la quota dopo ogni punto sui ~3.400 match registrati punto per
punto, per uscire nel punto esatto in cui la soglia viene toccata. Anche dopo la taratura, nei momenti in cui le soglie
scattano il modello sbaglia la probabilità di 2-5 punti, che su uno sfavorito a 3,20 valgono fino al 15% di ROI per
operazione: non abbastanza affidabile, quindi non è nell'app.

**Scalping sul servizio.** Un'operazione per ogni game di servizio dei match della strategia (servizio del giocatore
scelto, dell'avversario o di entrambi), entrando a 0-0. Esclusi i primi due game della partita e i game a match ormai
deciso (probabilità sotto/sopra la soglia *Salta se il match è deciso*, 5% di default: lì non ci sono quote). Il prezzo
segue il punteggio punto per punto: si chiude al Take Profit o allo Stop Loss, altrimenti a fine game (servizio tenuto
o break), con uscita opzionale alla prima palla break. Spread per lato regolabile.

Valori tipici per game (puntando chi serve a 0-0, spread 0,5%, giochi reali 2019-2026, stessi filtri dello scalping):

| Superficie | Chi serve | Tiene il servizio | Guadagno se tiene | Perdita se subisce il break |
|---|---|---|---|---|
| Terra | Favorito | 80% | +3,6% | -20,8% |
| Terra | Sfavorito | 72% | +11,4% | -36,7% |
| Erba | Favorito | 88% | +1,8% | -23,7% |
| Erba | Sfavorito | 81% | +8,1% | -42,1% |
| Cemento | Favorito | 85% | +2,9% | -25,0% |
| Cemento | Sfavorito | 78% | +9,6% | -40,9% |

Tenere il servizio vale molto meno che subire il break, e la differenza cambia con superficie e ruolo: sulla terra il
break pesa meno (si recupera più spesso), sull'erba di più; lo sfavorito al servizio muove la quota molto di più. Online
non esistono misure numeriche affidabili di questi movimenti (solo regole dei trader), quindi i valori vengono dal
modello applicato ai punti reali; la vista *Uscita* li ricalcola sul campione della strategia.

**Vista Uscita (analisi finale).** ROI con e senza uscita anticipata, effetto sul profitto, quota di operazioni chiuse,
drawdown; tabella per esito e momento (operazioni, % sul totale, quota di ingresso media, quota di uscita, ROI
all'uscita, profitto, profitto tenendo fino alla fine, differenza) e il conteggio degli esiti reali per ogni momento
(compresi i casi senza dato, che restano aperti). Con le quote: mappa del ROI per quota di Take Profit × quota di Stop
Loss, da poco meglio della stima del modello fino al 30% peggio. Con le percentuali: mappa del ROI per ogni coppia di
soglie (bordo bianco = combinazione migliore nello storico, bordo blu = quella scelta).

Le strategie salvate con il vecchio cash out (regole manuali o modello "intelligente") si riaprono convertite in Take
Profit / Stop Loss in % a fine primo set (tennis) o all'intervallo (calcio); le vecchie regole manuali vengono ignorate e
l'app lo segnala.

**Quota di ingresso.** Con ingresso pre-partita ogni operazione entra alla propria quota pre-partita (il Riepilogo lo
indica, con la quota mediana del campione). Con ingresso in partita la quota stimata si può sostituire con una quota fissa
scelta a mano (riquadro *Quota live di ingresso*); se si discosta di oltre il 10% dalla stima compare un avviso.

Nel **Registro** una giocata reale si può chiudere come *Cash out* indicando a mano il profitto o la perdita
effettivamente incassati.

## Dashboard

Navigazione in alto tra le pagine; il pannello laterale compare solo nel Backtester, dove servono i filtri. In cima
a ogni pagina c'è lo stato dei dati. Tema scuro, senza animazioni: i mini-grafici nelle schede sono SVG statici.

1. **Backtester** – pannello laterale con esempi pronti e filtri a gruppi (torneo/lega, calendario,
   giocatore/squadra, avversario, confronto); in alto il riquadro *Piano di trading* (su chi e quando entri, in
   quale momento e come esci). Viste:
   - *Riepilogo*: KPI, equity curve, drawdown, rendimenti mensili.
   - *In partita*: operazioni divise per situazione di gioco (set, break, intervallo) con vittorie reali e intervallo di confidenza.
   - *Analisi*: risultato scomposto per anno, fascia di quota, torneo, superficie...; elenco automatico dei
     segmenti migliori e peggiori, applicabili come filtro con un clic.
   - *Robustezza*: sei controlli (campione, significatività, intervallo di confidenza del ROI, CLV, tenuta
     fuori campione, costanza negli anni), confronto studio/verifica, anno per anno, confronto tra bookmaker.
   - *Uscita*: analisi finale delle uscite rispetto a tenere fino alla fine, per momento ed esito, mappa di calore e, nello
     scalping, valori tipici di game tenuto e break per superficie e ruolo.
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
