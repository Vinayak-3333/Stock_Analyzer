@echo off
title StockRadar IN
echo ============================================================
echo  StockRadar IN — Starting services
echo ============================================================
echo.
echo [1/2] Starting FastAPI backend (port 8000)...
start "StockRadar API" cmd /k "cd /d %~dp0backend && python -m uvicorn api:app --host 0.0.0.0 --port 8000"

timeout /t 3 /nobreak >nul

echo [2/2] Starting React dashboard (port 5173)...
start "StockRadar Dashboard" cmd /k "cd /d %~dp0frontend && npm run dev"

timeout /t 5 /nobreak >nul

echo.
echo ============================================================
echo  Dashboard: http://localhost:5173
echo  API:       http://localhost:8000
echo  API Docs:  http://localhost:8000/docs
echo ============================================================
echo.
echo Scheduled auto-runs: 09:15 and 15:30 IST (Mon-Fri)
echo Daily email alerts will be sent automatically.
echo Press any key to open dashboard in browser...
pause >nul
start http://localhost:5173
