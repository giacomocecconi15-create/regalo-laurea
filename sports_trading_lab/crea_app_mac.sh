#!/usr/bin/env bash
# Crea "Sports Trading Lab.app" sulla Scrivania: doppio clic per aprire la piattaforma in una finestra dedicata.
# Da eseguire una volta sola, dopo che l'ambiente e' stato installato (start_app.sh o i comandi manuali).
set -e
cd "$(dirname "$0")"
PROJ="$(pwd)"
DEST="${1:-$HOME/Desktop}/Sports Trading Lab.app"
PORT=8501

if [ "$(uname -s)" != "Darwin" ]; then echo "Questo script serve solo su Mac."; exit 1; fi
if [ ! -x .venv/bin/python ]; then echo "Ambiente non trovato: esegui prima ./start_app.sh"; exit 1; fi
if ! command -v osacompile >/dev/null 2>&1; then echo "osacompile non trovato (fa parte di macOS)."; exit 1; fi

SRC="$(mktemp -t stl_app).applescript"
cat > "$SRC" <<APPLESCRIPT
set proj to "$PROJ"
set appUrl to "http://localhost:$PORT"
set healthCmd to "curl -s -o /dev/null --max-time 2 " & appUrl & "/_stcore/health && echo si || echo no"

on openWindow(appUrl)
	-- finestra dedicata (senza barra degli indirizzi) se c'e' un browser Chromium, altrimenti browser predefinito
	set browsers to {"Google Chrome", "Microsoft Edge", "Brave Browser"}
	repeat with b in browsers
		try
			do shell script "test -d " & quoted form of ("/Applications/" & b & ".app")
			do shell script "open -na " & quoted form of (b as text) & " --args --app=" & appUrl
			return
		end try
	end repeat
	do shell script "open " & appUrl
end openWindow

if (do shell script healthCmd) is "si" then
	set r to button returned of (display dialog "Sports Trading Lab è già in esecuzione." buttons {"Spegni", "Apri"} default button "Apri" with title "Sports Trading Lab")
	if r is "Spegni" then
		do shell script "pkill -f 'streamlit run app.py' || true"
		return
	end if
	openWindow(appUrl)
	return
end if

try
	do shell script "test -x " & quoted form of (proj & "/.venv/bin/python")
on error
	display dialog "Non trovo la cartella del progetto:" & return & proj & return & return & "Se l'hai spostata, riesegui crea_app_mac.sh dalla nuova posizione." buttons {"OK"} default button "OK" with title "Sports Trading Lab" with icon stop
	return
end try

do shell script "cd " & quoted form of proj & " && mkdir -p data && nohup ./.venv/bin/python -m streamlit run app.py --server.headless true --server.port $PORT > data/app.log 2>&1 &"
set ready to false
repeat 80 times
	if (do shell script healthCmd) is "si" then
		set ready to true
		exit repeat
	end if
	delay 0.5
end repeat
if ready then
	openWindow(appUrl)
else
	display dialog "L'avvio non è riuscito. Il dettaglio è nel file:" & return & proj & "/data/app.log" buttons {"OK"} default button "OK" with title "Sports Trading Lab" with icon stop
end if
APPLESCRIPT

rm -rf "$DEST"
osacompile -o "$DEST" "$SRC"
rm -f "$SRC"
echo "Creata: $DEST"
echo "Doppio clic per aprire. Se è già aperta, il doppio clic chiede se riaprire la finestra o spegnerla."
