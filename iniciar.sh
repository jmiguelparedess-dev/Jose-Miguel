#!/usr/bin/env bash
# Inicia Serrate Paz BI (crea el entorno la primera vez).
cd "$(dirname "$0")"
if [ ! -d .venv ]; then python3 -m venv .venv && .venv/bin/pip install -r requirements.txt; fi
echo "Abra http://127.0.0.1:5000 en su navegador"
.venv/bin/python run.py
