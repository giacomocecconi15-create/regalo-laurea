#!/usr/bin/env bash
# Avvio rapido (Mac/Linux): crea l'ambiente al primo avvio, poi apre la dashboard.
set -e
cd "$(dirname "$0")"
# Preferisce una versione di Python per cui esistono librerie precompilate (3.10-3.13).
PY=""
for c in python3.12 python3.13 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then echo "Python non trovato: installa Python 3.12 da python.org"; exit 1; fi
if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c "import streamlit, duckdb, plotly" >/dev/null 2>&1; then
  rm -rf .venv
  echo "Creo l'ambiente con $($PY --version)..."
  "$PY" -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install --only-binary=:all: -r requirements.txt || {
    echo ""
    echo "Installazione non riuscita: questa versione di Python ($($PY --version)) non ha librerie precompilate."
    echo "Installa Python 3.12 da https://www.python.org/downloads/ e rilancia ./start_app.sh"
    rm -rf .venv
    exit 1
  }
fi
if [ ! -f data/trading_db.duckdb ]; then
  .venv/bin/python src/update_data.py
fi
exec .venv/bin/python -m streamlit run app.py
