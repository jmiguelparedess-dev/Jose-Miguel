@echo off
REM Inicia Serrate Paz BI. La primera vez instala los componentes necesarios.
cd /d "%~dp0"
title Serrate Paz BI
where python >nul 2>nul
if errorlevel 1 goto nopython
if not exist ".venv\Scripts\python.exe" python -m venv .venv
if not exist ".venv\instalado.txt" goto instalar
goto iniciar

:instalar
echo.
echo Instalando componentes. Solo la primera vez; puede tardar entre 2 y 10 minutos.
echo No cierre esta ventana.
echo.
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto error
echo ok> ".venv\instalado.txt"

:iniciar
echo.
echo Iniciando... el navegador se abrira en unos segundos: http://127.0.0.1:5000
echo Mantenga esta ventana abierta mientras usa la aplicacion.
start "" cmd /c "timeout /t 5 >nul & start http://127.0.0.1:5000"
".venv\Scripts\python.exe" run.py
pause
goto :eof

:nopython
echo No se encontro Python. Instalelo desde https://www.python.org/downloads/
echo y marque la casilla "Add python.exe to PATH" durante la instalacion.
pause
goto :eof

:error
echo.
echo ERROR durante la instalacion. Envie una captura de esta ventana.
echo Para reintentar, cierre esta ventana y vuelva a abrir iniciar.bat.
pause
