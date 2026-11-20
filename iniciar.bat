@echo off
REM Inicia Serrate Paz BI (crea el entorno la primera vez).
cd /d "%~dp0"
if not exist .venv (
  python -m venv .venv
  .venv\Scripts\pip install -r requirements.txt
)
start "" http://127.0.0.1:5000
.venv\Scripts\python run.py
