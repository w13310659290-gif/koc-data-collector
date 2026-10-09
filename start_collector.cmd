@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -m venv .venv
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -c "import importlib.metadata; assert importlib.metadata.version('playwright') == '1.63.0'" >nul 2>&1
if errorlevel 1 (
  ".venv\Scripts\python.exe" -m pip install -r requirements-auto.txt
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" collector_windows.py
if errorlevel 1 goto failed
exit /b 0
:failed
echo Setup or startup failed. Please send a screenshot without secrets.
pause
exit /b 1
