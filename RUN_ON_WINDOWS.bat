@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo Qafila Gross Margin Pipeline - Windows setup
echo ============================================
echo.

where uv >nul 2>nul
if errorlevel 1 (
  echo ERROR: uv was not found.
  echo Install uv first: https://docs.astral.sh/uv/getting-started/installation/
  echo Then close and reopen this window and run this file again.
  echo.
  pause
  exit /b 1
)

echo [1/5] Installing project dependencies...
uv sync
if errorlevel 1 goto :failed

echo.
echo [2/5] Generating demo input data...
uv run python tools/make_data.py
if errorlevel 1 goto :failed

echo.
echo [3/5] Running the pipeline...
uv run python run.py
if errorlevel 1 goto :failed

echo.
echo [4/5] Checking pipeline contracts and dbt tests...
uv run python tools/check.py
if errorlevel 1 goto :failed

echo.
echo [5/5] Comparing results with independent reference calculation...
uv run python tools/reference_check.py
if errorlevel 1 goto :failed

echo.
echo SUCCESS: all commands completed. Review the output above for test results.
echo Generated files are in data\ and warehouse\.
pause
exit /b 0

:failed
echo.
echo ERROR: a command failed. Read the error above, then check README.md - Troubleshooting.
pause
exit /b 1
