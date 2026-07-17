@echo off
REM ============================================================
REM  fluxr - lokalny worker (Reach Booster)
REM  Appka bezi na Railway, bot bezi tu a ovlada sa z webu.
REM  1) otvori Chrome s remote-debugging (port 9222)
REM  2) spusti workera, ktory caka na prikaz Start z webu
REM ============================================================
setlocal
cd /d "%~dp0"

REM --- Nastavenia ---------------------------------------------
REM Ak chces, AGENT_TOKEN si sem rovno vpis za "=" a nebude sa pytat.
set "FLUXR_SERVER=https://web-production-e461e.up.railway.app"
set "AGENT_TOKEN="

if "%AGENT_TOKEN%"=="" (
    echo Zadaj AGENT_TOKEN ^(rovnaky ako v Railway -^> Variables^):
    set /p AGENT_TOKEN=token:
)
if "%AGENT_TOKEN%"=="" (
    echo [CHYBA] Token je prazdny. Koncim.
    pause
    exit /b 1
)

echo.
echo === [1/3] Spustam Chrome s remote debugging (port 9222) ===
echo     V otvorenom Chrome sa prihlas na Instagram.
echo.
set "CHROME=C:\Program Files\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" (
    echo [CHYBA] Nenasiel som chrome.exe. Uprav cestu v tomto subore.
    pause
    exit /b 1
)
start "" "%CHROME%" --remote-debugging-port=9222 --user-data-dir="C:\chrome-bot"

echo.
echo === [2/3] Instalujem selenium + requests ===
python -m pip install selenium requests >nul 2>&1

echo.
echo === [3/3] Spustam workera ===
echo     Nechaj toto okno otvorene. Na webe chod na Bot a stlac Start.
echo.
python bot_worker.py

pause
endlocal
