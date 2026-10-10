@echo off
REM SafeBath AI -- start all local services (offline-first HTTP architecture).
REM No MQTT broker is needed: boards POST directly to the API on :8000.
REM
REM Services: Fusion Engine -> API Server -> Supabase sync worker -> Dashboard.
REM Cloud (Supabase / web push) is optional; everything local works without it.

set SAFEBATH_DIR=%~dp0
cd /d "%SAFBATH_DIR%"

set PY=.venv\Scripts\python.exe

echo ========================================
echo   SafeBath AI -- Starting Services
echo ========================================

REM 1. Fusion Engine (raw events -> risk state -> emergencies/alerts)
echo [1/4] Starting Fusion Engine...
start "safebath-fusion" /B "%PY%" -u backend/fusion_engine.py
timeout /t 2 /nobreak >NUL

REM 2. API Server (ESP32 HTTP ingestion + dashboard/caretaker API)
echo [2/5] Starting API Server...
netstat -an | findstr ":8000" | findstr "LISTENING" >NUL
if %ERRORLEVEL% NEQ 0 (
    start "safebath-api" /B ".venv\Scripts\python.exe" -u -m uvicorn backend.api_server:app --port 8000 --host 0.0.0.0
    timeout /t 3 /nobreak >NUL
    echo       API server started on :8000
) else (
    echo       API server already running on :8000
)

REM 3. Supabase sync worker (durable outbox -> cloud + web push; no-op if unset)
echo [3/4] Starting Supabase sync worker...
start "safebath-worker" /B ".venv\Scripts\python.exe" -u -m backend.supabase_sync
timeout /t 1 /nobreak >NUL
echo       Worker started (cloud sync disabled until SUPABASE_URL is set)

REM 4. Dashboard / caretaker static server
echo [4/4] Starting Dashboard Server...
netstat -an | findstr ":8080" | findstr "LISTENING" >NUL
if %ERRORLEVEL% NEQ 0 (
    start "safebath-dashboard" /B ".venv\Scripts\python.exe" -m http.server 8080 -d frontend
    timeout /t 1 /nobreak >NUL
    echo       Dashboard started on :8080
) else (
    echo       Dashboard already running on :8080
)

echo.
echo ========================================
echo   All services started!
echo   Dashboard:  http://localhost:8080/
echo   Caretaker:  http://localhost:8080/caretaker.html
echo   API:        http://localhost:8000/api/latest
echo   Boards POST to http://<this-pc-ip>:8000/api/v1/telemetry
echo ========================================
