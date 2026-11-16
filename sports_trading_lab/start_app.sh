#!/usr/bin/env bash
# Avvio rapido (Mac/Linux): crea l'ambiente al primo avvio, poi apre la dashboard.
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
fi
if [ ! -f data/trading_db.duckdb ]; then
  .venv/bin/python src/update_data.py
fi
exec .venv/bin/python -m streamlit run app.py
