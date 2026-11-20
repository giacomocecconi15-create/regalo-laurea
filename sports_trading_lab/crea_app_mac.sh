#!/usr/bin/env bash
# Crea "Sports Trading Lab.app" (in ~/Applicazioni, con collegamento sulla Scrivania).
# E' un'app vera e propria che resta aperta:
#   - al primo clic apre subito la finestra con "Avvio in corso..." e avvia il motore in background
#     (niente rotella che gira: l'app risponde sempre);
#   - resta nel Dock con la sua icona finche' e' accesa;
#   - un altro clic sull'icona (Dock, Launchpad o Applicazioni) riporta in primo piano la finestra gia' aperta
#     invece di aprirne un'altra; se la finestra era stata chiusa, la riapre;
#   - Esci (Cmd+Q o tasto destro sull'icona nel Dock) spegne il motore e chiude la finestra.
# Da eseguire una volta, dopo che l'ambiente e' stato installato (start_app.sh o i comandi manuali), e di nuovo
# se sposti la cartella del progetto.
set -e
cd "$(dirname "$0")"
PROJ="$(pwd)"
APPS="${1:-$HOME/Applications}"
DEST="$APPS/Sports Trading Lab.app"
PORT=8501
PROFILE="$HOME/Library/Application Support/Sports Trading Lab/browser"

if [ "$(uname -s)" != "Darwin" ]; then echo "Questo script serve solo su Mac."; exit 1; fi
if [ ! -x .venv/bin/python ]; then echo "Ambiente non trovato: esegui prima ./start_app.sh"; exit 1; fi
if ! command -v osacompile >/dev/null 2>&1; then echo "osacompile non trovato (fa parte di macOS)."; exit 1; fi

mkdir -p "$APPS" "$PROFILE" data
# pagina di attesa mostrata subito nella finestra dell'app (indirizzo file:// con gli spazi codificati)
cp -f assets/icon.png data/icon.png 2>/dev/null || true
cp -f assets/avvio.html data/avvio.html
LOADING_URL="$(.venv/bin/python -c 'import pathlib, sys; print(pathlib.Path(sys.argv[1]).resolve().as_uri())' data/avvio.html)"
# primo avvio piu' rapido: i file dell'app gia' compilati
.venv/bin/python -m compileall -q app.py src >/dev/null 2>&1 || true

SRC="$(mktemp -t stl_app).applescript"
cat > "$SRC" <<APPLESCRIPT
property proj : "$PROJ"
property appPort : "$PORT"
property profileDir : "$PROFILE"
property loadingUrl : "$LOADING_URL"
global appState, startedAt, browserKind, readySince

on appUrl()
	return "http://localhost:" & appPort
end appUrl

on healthy()
	try
		return (do shell script "curl -s -o /dev/null --max-time 1 " & my appUrl() & "/_stcore/health && echo si || echo no") is "si"
	on error
		return false
	end try
end healthy

on chooseBrowser()
	repeat with b in {"Google Chrome", "Microsoft Edge", "Brave Browser"}
		try
			do shell script "test -d " & quoted form of ("/Applications/" & (b as text) & ".app")
			return b as text
		end try
	end repeat
	return "Safari"
end chooseBrowser

-- processo principale del browser dedicato all'app (profilo separato: non tocca le finestre normali del browser)
on browserPid()
	try
		return do shell script "pgrep -f -- " & quoted form of ("--user-data-dir=" & profileDir) & " | head -1"
	on error
		return ""
	end try
end browserPid

-- c'e' una finestra collegata all'app? (connessione aperta verso il motore da un processo diverso dal motore)
on windowOpen()
	try
		set listening to do shell script "lsof -nP -iTCP:" & appPort & " -sTCP:LISTEN -t 2>/dev/null | head -1 || true"
		set pids to do shell script "lsof -nP -iTCP:" & appPort & " -sTCP:ESTABLISHED -t 2>/dev/null | sort -u || true"
	on error
		return false
	end try
	repeat with p in paragraphs of pids
		if (p as text) is not "" and (p as text) is not listening then return true
	end repeat
	return false
end windowOpen

on openWindow(targetUrl)
	if browserKind is "Safari" then
		do shell script "open -a Safari " & quoted form of targetUrl
	else
		do shell script "open -na " & quoted form of browserKind & " --args --user-data-dir=" & quoted form of profileDir & " --no-first-run --no-default-browser-check --app=" & quoted form of targetUrl
	end if
end openWindow

-- porta in primo piano la finestra dell'app se esiste, altrimenti ne apre una
on focusWindow()
	if browserKind is "Safari" then
		set prefix to my appUrl()
		try
			tell application "Safari"
				repeat with w in windows
					repeat with t in tabs of w
						set u to ""
						try
							set u to (URL of t) as text
						end try
						if (u starts with prefix) or ((u starts with "file:") and (u contains "avvio.html")) then
							set current tab of w to t
							set index of w to 1
							activate
							return
						end if
					end repeat
				end repeat
			end tell
		end try
		if appState is "starting" then
			my openWindow(loadingUrl)
		else
			my openWindow(my appUrl())
		end if
		return
	end if
	set bp to my browserPid()
	if bp is not "" and (appState is "starting" or my windowOpen()) then
		try
			tell application "System Events" to set frontmost of (first process whose unix id is (bp as integer)) to true
			return
		end try
	end if
	if appState is "starting" then
		my openWindow(loadingUrl)
	else
		my openWindow(my appUrl())
	end if
end focusWindow

on run
	set browserKind to my chooseBrowser()
	set readySince to missing value
	if my healthy() then
		-- motore gia' acceso (per esempio da un'apertura precedente): basta la finestra
		set appState to "ready"
		my focusWindow()
		return
	end if
	try
		do shell script "test -x " & quoted form of (proj & "/.venv/bin/python")
	on error
		display dialog "Non trovo la cartella del progetto:" & return & proj & return & return & "Se l'hai spostata, riesegui crea_app_mac.sh dalla nuova posizione." buttons {"OK"} default button "OK" with title "Sports Trading Lab" with icon stop
		quit
		return
	end try
	do shell script "cd " & quoted form of proj & " && mkdir -p data && nohup ./.venv/bin/python -m streamlit run app.py --server.headless true --server.port " & appPort & " --server.fileWatcherType none --browser.gatherUsageStats false > data/app.log 2>&1 &"
	set startedAt to current date
	set appState to "starting"
	my openWindow(loadingUrl)
end run

on idle
	try
		appState
	on error
		return 5
	end try
	if appState is "starting" then
		if my healthy() then
			set appState to "ready"
			set readySince to current date
			return 2
		end if
		set alive to true
		try
			do shell script "pgrep -f " & quoted form of ("streamlit run app.py --server.headless true --server.port " & appPort) & " >/dev/null"
		on error
			set alive to false
		end try
		if (not alive) or ((current date) - startedAt) > 900 then
			set appState to "failed"
			set logTail to ""
			try
				set logTail to do shell script "tail -n 12 " & quoted form of (proj & "/data/app.log")
			end try
			display dialog "L'avvio non è riuscito." & return & return & logTail & return & return & "Il dettaglio completo è in " & proj & "/data/app.log" buttons {"OK"} default button "OK" with title "Sports Trading Lab" with icon stop
			quit
		end if
		return 1
	end if
	if appState is "ready" and readySince is not missing value then
		-- rete di sicurezza: se dopo 20 secondi la pagina di attesa non si e' collegata all'app, apre la finestra
		if ((current date) - readySince) > 20 then
			set readySince to missing value
			if not my windowOpen() then my focusWindow()
		end if
		return 2
	end if
	return 10
end idle

on reopen
	try
		appState
	on error
		return
	end try
	if appState is "failed" then return
	my focusWindow()
end reopen

on quit
	try
		do shell script "pkill -f " & quoted form of ("streamlit run app.py --server.headless true --server.port " & appPort) & " || true"
	end try
	try
		if browserKind is not "Safari" then do shell script "pkill -f -- " & quoted form of ("--user-data-dir=" & profileDir) & " || true"
	end try
	continue quit
end quit
APPLESCRIPT

pkill -f "streamlit run app.py --server.headless true --server.port $PORT" 2>/dev/null || true   # vecchia versione ancora accesa
rm -rf "$DEST"
osacompile -s -o "$DEST" "$SRC"      # -s: l'app resta aperta (icona nel Dock, risponde ai clic successivi)
rm -f "$SRC"
PL="$DEST/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier it.sportstradinglab.app" "$PL" 2>/dev/null || \
  /usr/libexec/PlistBuddy -c "Add :CFBundleIdentifier string it.sportstradinglab.app" "$PL"
/usr/libexec/PlistBuddy -c "Add :NSAppleEventsUsageDescription string Serve per riportare in primo piano la finestra di Sports Trading Lab." "$PL" 2>/dev/null || true
if [ -f assets/icon.icns ]; then   # icona personalizzata al posto di quella generica degli script
  cp assets/icon.icns "$DEST/Contents/Resources/applet.icns"
  rm -f "$DEST/Contents/Resources/Assets.car"
fi
codesign --force --deep --sign - "$DEST" >/dev/null 2>&1 || true
touch "$DEST"
# collegamento sulla Scrivania (la vecchia app sulla Scrivania viene sostituita)
if [ "$APPS" != "$HOME/Desktop" ]; then
  rm -rf "$HOME/Desktop/Sports Trading Lab.app"
  ln -sfn "$DEST" "$HOME/Desktop/Sports Trading Lab"
fi
echo "Creata: $DEST (e collegamento sulla Scrivania)"
echo "Doppio clic per aprirla. Per tenerla sempre nel Dock: tasto destro sull'icona nel Dock > Opzioni > Mantieni nel Dock."
echo "Per spegnerla: Cmd+Q, oppure tasto destro sull'icona nel Dock > Esci."
