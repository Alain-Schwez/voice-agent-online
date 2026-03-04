@echo off
cd /d %~dp0

echo ===============
echo Projet folder:
cd
echo ===============

echo Activating virtual environment ...
call .venv\scripts\activate.bat

echo Starting Uvicorn ...
start "" uvicorn server.app:app --reload --port 8000

echo Waiting for server to start ...
timeout /t 4 /nobreak > nul

echo Opening browser ...
start "" http://127.0.0.1:8000

echo ===============
echo Voice Agent Running :-)
echo ===============

pause