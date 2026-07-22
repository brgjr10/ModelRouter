@echo off

REM ModelRouter Full Stack Starter Script

REM Check for existing processes on ports
netstat -ano | findstr :5000 >nul
if %errorlevel% equ 0 (
    echo WARNING: Port 5000 is in use
)

netstat -ano | findstr :5174 >nul
if %errorlevel% equ 0 (
    echo WARNING: Port 5174 is in use
)

REM Start backend
echo Starting ModelRouter backend...
python -m uvicorn proxy:app --host 0.0.0.0 --port 5000 &

REM Short delay for backend to initialize
timeout /t 3 /nobreak >nul

REM Start frontend
echo Starting frontend dev server...
cd F:\ModelRouter\frontend && npm run dev &

REM Wait a moment for both to start
timeout /t 5 /nobreak >nul

REM Show paths
echo.
echo Backend: http://localhost:5000
echo Frontend: http://localhost:5174

REM Keep window open
pause

REM Cleanup on exit (not implemented - requires additional code)