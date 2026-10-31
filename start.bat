@echo off
set "KUGO_PYTHON=%USERPROFILE%\.codex\mcp\kugo-mcp\venv\Scripts\python.exe"
if not exist "%KUGO_PYTHON%" (
  echo Cannot find kugo-mcp Python environment: %KUGO_PYTHON%
  pause
  exit /b 1
)
echo Open http://127.0.0.1:8765 in your browser. Keep this window open.
"%KUGO_PYTHON%" "%~dp0server.py"
pause
