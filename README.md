# regalo-laurea

Sito di una sola pagina — regalo di laurea per Sofia.
Giornata in barca, Messina, 1 agosto 2026.

HTML, CSS e JavaScript puri: nessun framework, nessuna build.
Basta aprire `index.html` (o pubblicare la cartella su GitHub Pages).

## File

| file | contenuto |
| --- | --- |
| `index.html` | le quattro sezioni e tutti i testi |
| `styles.css` | tipografia, spaziature, animazioni |
| `script.js` | player YouTube, effetti allo scroll, riscatto del regalo |

## Testi da sostituire

Nel file `index.html` ci sono quattro punti marcati con `PLACEHOLDER`:

1. **didascalia del video** — `.video__caption`
2. **testo che si illumina parola per parola** — `.reveal__text`
3. **messaggio finale** — `.finale__message`
4. **il regalo vero** — `.gift__text` (e l'occhiello `.gift__eyebrow`)

Il testo del punto 2 viene diviso in parole automaticamente: si scrive
come una frase normale, senza tag. Va bene fino a ~25 parole; oltre,
su telefono, la frase rischia di non entrare in una schermata.

## Notifica

Al tocco di «Accetta il regalo» il sito manda una POST a
`https://ntfy.sh/barca-laurea-9k3xq7`. Per riceverla basta iscriversi a
quel topic dall'app ntfy o aprire la stessa URL nel browser.
Se la rete fallisce il regalo viene mostrato lo stesso.

Lo stato «riscattato» resta in `localStorage` (chiave
`regalo-sofia:riscattato`): riaprendo il sito il regalo è già visibile.
Per rivedere l'animazione dall'inizio basta svuotare i dati del sito.

## Note

- Il video è il short YouTube `45bXmwsr5h8`: parte muto e in automatico
  quando la sezione entra in vista, si tocca lo schermo per l'audio.
  L'autoplay muto è l'unico permesso dai browser su mobile.
- Le animazioni partono con Intersection Observer e si fermano quando la
  sezione esce dallo schermo, video compreso.
- Con «riduci movimento» attivo su iOS le transizioni si disattivano e il
  testo resta leggibile.
