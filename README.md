# regalo-laurea

Sito di una sola pagina — regalo di laurea per Sofia.
Giornata in barca, Messina, 1 agosto 2026.

HTML, CSS e JavaScript puri: nessun framework, nessuna build.
Basta aprire `index.html` con un server locale (o pubblicare la
cartella su GitHub Pages).

## File

| file | contenuto |
| --- | --- |
| `index.html` | le cinque sezioni e tutti i testi |
| `styles.css` | tipografia, colori, animazioni |
| `script.js` | player YouTube, effetti allo scroll, coriandoli, riscatto |

## Le cinque sezioni

1. **Hero** — il nome entra una lettera alla volta, data e luogo sotto,
   e l'invito «Scorri per iniziare».
2. **Video** — lo short di YouTube a schermo intero, muto in automatico,
   si tocca per l'audio.
3. **Grazie** — la frase si illumina parola per parola mentre scorri.
4. **Congratulazioni** — sezione chiara, con il giro del mondo
   (Indonesia → Portogallo → chissà) che entra una riga alla volta.
5. **Regalo** — il bottone manda la notifica, poi partono i coriandoli,
   le lettere del regalo si compongono una alla volta e un'onda calda
   le attraversa.

L'indicazione **«Scorri verso il basso ↓»** resta fissa in basso per
tutta la pagina: compare al primo scroll, si schiarisce sopra la
sezione chiara e sparisce solo sul finale, dove lascia il posto a
«Tocca il bottone». Sul video, sopra di essa, c'è anche «Tocca lo
schermo per l'audio».

## Testi da sostituire

Nel file `index.html` sono marcati con `PLACEHOLDER`:

1. **didascalia del video** — `.video__caption`
2. **il grazie parola per parola** — `.thanks__text`
3. **messaggio prima del regalo** — `.finale__message`
4. **il regalo** — `.gift__text` (la riga grande) e `.gift__note` (sotto)

La riga di `.gift__text` si compone lettera per lettera, quindi va
tenuta corta: fino a ~30 caratteri l'effetto resta bello, più lunga
diventa lenta. Il resto si scrive in `.gift__note`.

Il testo del punto 2 viene diviso in parole automaticamente: si scrive
come una frase normale, senza tag. Va bene fino a ~25 parole; oltre,
su telefono, rischia di non entrare in una schermata.

I testi della sezione «Congratulazioni» (titolo, tappe del viaggio,
saluto finale) si modificano direttamente nel blocco `.congrats`.

## Notifica

Al tocco di «Accetta il regalo» il sito manda una POST a
`https://ntfy.sh/barca-laurea-9k3xq7`. Per riceverla bisogna essere
iscritti al topic: app **ntfy** (iOS/Android) → «+» → `barca-laurea-9k3xq7`.
Senza iscrizione il messaggio non viene recapitato a nessuno.
Se la rete fallisce il regalo viene mostrato lo stesso.

Lo stato «riscattato» resta in `localStorage` (chiave
`regalo-sofia:riscattato:v2`): riaprendo il sito il regalo è già
visibile, senza rifare l'animazione.

**Per rimettere il bottone da cliccare** basta aprire il sito con
`?reset` in fondo all'indirizzo:

```
https://.../regalo-laurea/?reset
```

Lo stato si azzera, l'indirizzo torna pulito e il regalo è di nuovo da
scartare. Utile per provarlo quante volte si vuole prima di mandarlo.

## Note

- Il video è il short YouTube `45bXmwsr5h8`: parte muto e in automatico
  quando la sezione entra in vista. L'autoplay muto è l'unico permesso
  dai browser su mobile.
- Le animazioni partono con Intersection Observer e si fermano quando la
  sezione esce dallo schermo, video compreso.
- I coriandoli sono un canvas che si cancella da solo dopo ~3 secondi.
- Con «riduci movimento» attivo su iOS le animazioni si disattivano
  (coriandoli compresi) e tutti i testi restano leggibili.
