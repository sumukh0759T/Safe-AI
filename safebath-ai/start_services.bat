@echo off
REM SafeBath AI -- Start all services
REM Run this script to start Mosquitto, Bridge, Fusion Engine, API, and Dashboard

set SAFEBATH_DIR=%~dp0
cd /d "%SAFEBATH_DIR%"

echo ========================================
echo   SafeBath AI -- Starting Services
echo ========================================

REM 1. Mosquitto broker
echo [1/5] Starting Mosquitto broker...
tasklist /FI "IMAGENAME eq mosquitto.exe" 2>NUL | find /I "mosquitto.exe" >NUL
if %ERRORLEVEL% NEQ 0 (
    start "" /B "C:\Program Files\mosquitto\mosquitto.exe" -c "%SAFEBATH_DIR%mosquitto.conf"
    timeout /t 2 /nobreak >NUL
    echo       Mosquitto started on :1883
) else (
    echo       Mosquitto already running
)

REM 2. MQTT Bridge
echo [2/5] Starting MQTT Bridge...
tasklist /FI "WINDOWTITLE eq safebath-bridge" 2>NUL | find /I "python" >NUL
if %ERRORLEVEL% NEQ 0 (
    start "safebath-bridge" /B ".venv\Scripts\python.exe" -u backend/mqtt_bridge.py
    timeout /t 2 /nobreak >NUL
    echo       Bridge started
) else (
    echo       Bridge already running
)

REM 3. Fusion Engine
echo [3/5] Starting Fusion Engine...
tasklist /FI "WINDOWTITLE eq safebath-fusion" 2>NUL | find /I "python" >NUL
if %ERRORLEVEL% NEQ 0 (
    start "safebath-fusion" /B ".venv\Scripts\python.exe" -u backend/fusion_engine.py
    timeout /t 2 /nobreak >NUL
    echo       Fusion engine started
) else (
    echo       Fusion engine already running
)

REM 4. API Server
echo [4/5] Starting API Server...
netstat -an | findstr ":8000" | findstr "LISTENING" >NUL
if %ERRORLEVEL% NEQ 0 (
    start "safebath-api" /B ".venv\Scripts\python.exe" -u -m uvicorn backend.api_server:app --port 8000 --host 0.0.0.0
    timeout /t 3 /nobreak >NUL
    echo       API server started on :8000
) else (
    echo       API server already running on :8000
)

REM 5. HTTP Dashboard
echo [5/5] Starting Dashboard Server...
netstat -an | findstr ":8080" | findstr "LISTENING" >NUL
if %ERRORLEVEL% NEQ 0 (
    start "safebath-dashboard" /B ".venv\Scripts\python.exe" -m http.server 8080
    timeout /t 1 /nobreak >NUL
    echo       Dashboard started on :8080
) else (
    echo       Dashboard already running on :8080
)

echo.
echo ========================================
echo   All services started!
echo   Dashboard:  http://localhost:8080
echo   Caretaker:  http://localhost:8080/caretaker.html
echo   API:        http://localhost:8000/api/latest
echo ========================================
