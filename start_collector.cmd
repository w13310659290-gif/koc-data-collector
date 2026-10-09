@echo off
setlocal
pushd "%~dp0"
if errorlevel 1 goto folder_failed
if not exist "%~dp0requirements-auto.txt" goto missing_files
if not exist "%~dp0collector_windows.py" goto missing_files
if not exist "%~dp0koc_auto\runner.py" goto missing_files
if not exist ".venv\Scripts\python.exe" (
  py -m venv .venv
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -c "import importlib.metadata; assert importlib.metadata.version('playwright') == '1.63.0'" >nul 2>&1
if errorlevel 1 (
  ".venv\Scripts\python.exe" -m pip install -r "%~dp0requirements-auto.txt"
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" "%~dp0collector_windows.py"
if errorlevel 1 goto failed
popd
exit /b 0
:missing_files
echo Project files are missing. Extract the ENTIRE ZIP first.
echo Do not run this script inside a ZIP or copy only this file.
echo Script folder: %~dp0
popd
pause
exit /b 1
:folder_failed
echo Cannot open the project folder. Extract the ZIP to a local folder first.
pause
exit /b 1
:failed
echo Setup or startup failed. Please send a screenshot without secrets.
popd
pause
exit /b 1
