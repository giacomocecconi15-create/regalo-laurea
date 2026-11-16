@echo off
rem Avvio rapido (Windows): crea l ambiente al primo avvio, poi apre la dashboard.
cd /d "%~dp0"
if not exist .venv (
  python -m venv .venv || goto :error
  .venv\Scripts\python -m pip install --upgrade pip
  .venv\Scripts\python -m pip install -r requirements.txt || goto :error
)
if not exist data\trading_db.duckdb (
  .venv\Scripts\python src\update_data.py
)
.venv\Scripts\python -m streamlit run app.py
goto :eof
:error
echo Installazione non riuscita: verifica che Python 3.10+ sia installato e nel PATH.
pause
