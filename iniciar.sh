#!/usr/bin/env bash
# Inicia Serrate Paz BI. La primera vez instala los componentes necesarios.
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || python3 -m venv .venv
if [ ! -f .venv/instalado.txt ]; then
  echo "Instalando componentes (solo la primera vez)..."
  .venv/bin/python -m pip install --upgrade pip && .venv/bin/python -m pip install -r requirements.txt \
    && echo ok > .venv/instalado.txt || { echo "ERROR durante la instalación"; exit 1; }
fi
echo "Abra http://127.0.0.1:5000 en su navegador"
.venv/bin/python run.py
