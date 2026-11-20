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

**App per Mac.** Dopo la prima installazione, esegui una volta `./crea_app_mac.sh`: crea *Sports Trading Lab* in
*Applicazioni* (cartella Applicazioni dell'utente, visibile anche in Launchpad e Spotlight) con un collegamento sulla
Scrivania. Si comporta come un'app normale:
- al clic apre subito la sua finestra con "Avvio in corso…" e avvia il motore in background: l'app risponde sempre
  (niente rotella) e la pagina passa da sola alla piattaforma appena è pronta, anche al primo avvio dopo un
  aggiornamento, che può richiedere qualche minuto;
- resta nel Dock con la sua icona finché è accesa (per tenerla sempre lì: tasto destro sull'icona > Opzioni >
  Mantieni nel Dock);
- un altro clic sull'icona riporta in primo piano la finestra già aperta invece di aprirne un'altra; se la finestra era
  stata chiusa, la riapre;
- Cmd+Q, oppure tasto destro sull'icona nel Dock > Esci, spegne il motore e chiude la finestra.

La finestra usa Chrome, Edge o Brave con un profilo separato (non tocca le tue finestre normali), altrimenti Safari. Al
primo utilizzo macOS può chiedere il permesso di controllare "System Events": serve solo a riportare in primo piano la
finestra. L'app punta alla cartella del progetto: se la sposti, riesegui lo script. Se l'avvio non riesce, l'app mostra
le ultime righe di `data/app.log`.

Uso manuale:

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python src/update_data.py        # scarica / aggiorna il database
.venv/bin/python -m pytest tests -q        # 97 test
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
  segni incoerenti. Ora, a parità di condizioni, back + lay ≈ -(margine + spread + commissione). Le partite senza la
  quota dell'altro lato (poche, per esempio Over/Under senza Under) non sono prezzabili in banca e vengono escluse.
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

**Quando entri.** Tennis: pre-partita, dopo il primo break fatto o subito, dopo il primo set vinto o perso, quando il
giocatore va **sotto pressione** (tiene due turni di servizio consecutivi dello stesso set affrontando palle break o
arrivando ai vantaggi in entrambi; si entra alla fine del secondo, solo match punto per punto) oppure quando **mette
sotto pressione** l'avversario (lo stesso momento visto dall'altro lato: spinge ma non riesce a breakkare). Calcio 1X2:
pre-partita, all'intervallo in vantaggio, sul pari o in svantaggio. Over/Under: pre-partita o all'intervallo con 0, 1,
2 gol. Il primo set vale per tutti i match completati (un ritiro a metà primo set lascia il dato vuoto); il primo break
esiste solo per i match registrati punto per punto (circa uno su cinque, soprattutto nei tornei maggiori). La squadra
che segna per prima non è disponibile: la fonte non riporta i minuti dei gol.

I range di quota restano sulle quote **pre-partita**, ma con un filtro in partita l'operazione entra **dopo l'evento**
(dopo il break, a fine primo set, all'intervallo) alla quota live stimata, meno un margine configurabile: così ROI e
profitto non usano informazioni future. Il Riepilogo mostra quota media pre-partita, quota live di entrata (mediana),
vittorie reali con l'intervallo di confidenza al 95% e la probabilità stimata al momento: se la stima cade dentro
l'intervallo lo scarto è spiegato dal campione. Nel riquadro *Quota live di ingresso* si può anche filtrare per quota live
stimata (minima e massima), che è nota al momento dell'ingresso. La vista *In partita* divide le operazioni per situazione
di gioco (primo set, primo break, controbreak, sotto pressione, tie-break) con il numero di partite, la percentuale di
vittorie (conteggio esatto) e il suo intervallo di confidenza al 95%.

**Taratura delle quote live.** Non esistono quote in partita gratuite, e online non si trovano misure affidabili del
calo medio delle quote (solo regole dei trader). La stima viene dai modelli dell'app, tarati sui risultati reali:
la forma di ogni correzione è scelta allenando sul 2019-2022 e verificando sul 2023 in poi (log-loss fuori campione), i
coefficienti finali sono stimati su tutti gli anni.

| Momento | Modello | Correzione tarata |
|---|---|---|
| Fine primo set | set indipendenti dalla quota pre-partita | inerzia del set; nei match punto per punto anche *come* è stato vinto: break fatto e mai ripreso (chi lo vince vince l'84,9%, il modello semplice diceva 81,0%), rimonta dopo il break subito, tie-break |
| Primo break | modello a punti dopo il game | inerzia +0,43 sul logit, dimezzata al meglio dei 5 set |
| Controbreak (primo set) | modello a punti | -0,11: dopo il controbreak il modello a punti è quasi giusto |
| Break successivi | modello a punti | +0,11, più +0,19 per chi ha fatto il primo break del match |
| Sotto pressione | modello a punti dopo il secondo game tenuto | -0,12 e -0,26 × forza pre-partita: il favorito sotto pressione vince meno di quanto dica il punteggio (66% contro 72% stimato), lo sfavorito un po' di più |
| Intervallo (calcio) | Poisson sul secondo tempo | vantaggio, pareggio e gol |

Un test controlla che per ogni situazione la probabilità stimata coincida con la frequenza reale (entro 1,5 punti; per
ruolo e per storia del set entro 2,5-3). Il punteggio del primo set (6-0 contro 7-6) invece non aggiunge nulla fuori
campione e non è usato. Punta e banca usano la stessa probabilità: quota per chi punta = equa × (1 − margine), per chi
banca = equa × (1 + margine); con margine zero il valore atteso secondo la stima è zero per entrambi, quindi non c'è un
lato favorito dall'errore di stima.

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

### Uscita: Take Profit e Stop Loss separati

L'uscita ha due regole indipendenti, ognuna con il proprio evento; vale quella che scatta per prima dopo l'ingresso, e se
nessuna scatta la posizione resta aperta fino alla fine.

- **Take Profit · esci in positivo quando la selezione…** fa il primo break, fa il controbreak, fa il prossimo break, vince
  il primo set, vince il secondo set.
- **Stop Loss · esci in negativo quando la selezione…** subisce il primo break, subisce il controbreak, subisce il prossimo
  break, perde il primo set, perde il secondo set.

Per chi banca i due elenchi si invertono. Nel calcio gli eventi sono gli esiti all'intervallo (in vantaggio, in parità, in
svantaggio; pari / non pari per il pareggio; 0, 1, 2 gol per l'Over/Under). Esempio: punti chi fa il primo break, Take
Profit a fine primo set se lo vince, Stop Loss al controbreak se lo subisce.

- **Controbreak**: il break di chi aveva subito il primo break, in qualsiasi game successivo del primo set (anche
  l'ultimo). **Prossimo break**: il primo break dopo l'ingresso, in qualsiasi set. Primo break, controbreak e prossimo
  break esistono solo nei match registrati punto per punto (circa uno su cinque): con queste regole il backtest usa solo
  quei match.
- **Come esci**:
  - *A quote che inserisci tu*: per ogni regola la quota di uscita; il ROI di ogni operazione si calcola dalla **sua**
    quota di ingresso (pre-partita, live stimata o inserita a mano): punta `ingresso/uscita − 1`, banca
    `(1 − ingresso/uscita)/(ingresso − 1)`, commissione sui profitti. **Quota vuota** = ogni partita esce alla quota
    stimata per lei in quel momento (modello tarato, meno il *margine all'uscita*): con quote di ingresso molto diverse una
    quota fissa non vale per tutte (una partita entrata a 13,0 non può uscire a 1,20 dopo il set vinto) e l'app avvisa
    quando per oltre il 20% delle partite la quota inserita rende più di 10 punti oltre la stima di quella partita.
  - *Take Profit / Stop Loss in % di ROI* sull'unità rischiata (SL 25% = esci quando la posizione perde 0,25 u),
    controllati nel momento dell'evento con il valore stimato: si esce al valore di quel momento, che a fine set va spesso
    ben oltre la soglia (es. sfavoriti 2,00-3,50 con SL 25% a fine primo set: uscita media −61%). L'app traduce le
    soglie in quote equivalenti e mostra il ROI medio effettivo. *In ogni momento* controlla la soglia al primo break, al
    controbreak e a fine primo e secondo set, nell'ordine in cui avvengono.
  - *Scalping sul servizio*: vedi sotto.

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
all'uscita, profitto, profitto tenendo fino alla fine, differenza) e quante partite hanno davvero ciascun evento dopo
l'ingresso. Due riferimenti:

- **Controllo del modello**: lo stesso piano uscendo alla quota stimata con margine zero. Se la stima è giusta per
  *questo* piano, il risultato coincide con il tenere fino alla fine; lo scarto è mostrato con il suo errore standard
  (verde se entro due errori standard). Sui piani tipici (primo break, set, controbreak, intervallo) lo scarto è entro
  un punto; con il "prossimo break" dopo ingressi particolari può arrivare a 2-3 punti, e l'app lo segnala in rosso.
- **ROI con le quote stimate**: le tue regole con quote realistiche partita per partita, da confrontare con le quote
  inserite a mano.

Poi la mappa: con le quote, ROI per quota di Take Profit × quota di Stop Loss, da poco meglio della stima del modello
fino al 30% peggio (off = regola spenta); con le percentuali, ROI per ogni coppia di soglie (bordo bianco = combinazione
migliore nello storico, bordo blu = quella scelta).

Le strategie salvate con il vecchio cash out (regole manuali o modello "intelligente") si riaprono convertite in Take
Profit / Stop Loss in % a fine primo set (tennis) o all'intervallo (calcio); le vecchie regole manuali vengono ignorate e
l'app lo segnala.

**Quota di ingresso.** Con ingresso pre-partita ogni operazione entra alla propria quota pre-partita (il Riepilogo lo
indica, con la quota mediana del campione). Con ingresso in partita la quota stimata si può sostituire con una quota fissa
scelta a mano (riquadro *Quota live di ingresso*); se si discosta di oltre il 10% dalla stima compare un avviso.

### Adjustment: togliere anni o partite

Nella vista *Analisi*, scomponendo per anno, ogni riga ha la casella **escludi**: l'anno viene tolto dal backtest e tutte
le viste (Riepilogo, Robustezza, Uscita, Operazioni) si aggiornano; gli anni esclusi restano nella tabella con i loro
numeri per poterli rimettere. Nella vista *Operazioni* si clicca una riga e si apre la **scheda della partita**:
risultato dal punto di vista del giocatore, statistiche ufficiali dei due giocatori (ace, doppi falli, prime, punti vinti,
palle break salvate, quote) e, per i match registrati punto per punto, la sequenza game per game con il punteggio dopo ogni
punto, palle break, vantaggi, eventi (primo break, controbreak, sotto pressione) e probabilità e quota stimate dopo ogni
game, con il grafico. Dalla scheda *Escludi questa partita* la toglie dall'analisi, e *Correggi la quota di ingresso*
sostituisce la quota stimata (o pre-partita) di quella operazione con quella che indichi, per esempio la quota che c'era
davvero: la correzione vale in tutta l'analisi (profitto, uscite, quota live mediana del Riepilogo) e la tabella
Operazioni mostra quota stimata e quota corretta. Il riquadro **Adjustment** sopra i
risultati mostra le esclusioni e permette di ripristinarle; le esclusioni vengono salvate con la strategia ma lo scanner
delle partite di oggi le ignora. Togliere a posteriori gli anni o le partite peggiori gonfia il risultato: va fatto solo
con un motivo (dati incompleti, stagione anomala, regole diverse).

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
   - *Uscita*: analisi finale delle uscite rispetto a tenere fino alla fine, per momento ed esito, controllo del modello, mappa di calore e, nello
     scalping, valori tipici di game tenuto e break per superficie e ruolo.
   - *Operazioni*: tabella esportabile in CSV con risultato e disponibilità del punto per punto; clic su una riga = scheda
     della partita. *Salva*: archivia la strategia.

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
