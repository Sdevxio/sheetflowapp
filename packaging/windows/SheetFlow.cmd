@echo off
setlocal
cd /d "%~dp0"
set "ROOT=%~dp0"
set "PYTHONPATH=%ROOT%backend"
set "SHEETFLOW_STATIC=%ROOT%frontend\dist"

if not exist "%ROOT%python\python.exe" (
  echo SheetFlow could not find its Python next to this file.
  echo.
  echo Unzip the whole download first. Right-click the zip and choose Extract all.
  echo Then open the SheetFlow-windows folder and double-click SheetFlow.cmd.
  echo Do not start SheetFlow from inside the zip window.
  echo.
  pause
  exit /b 1
)

echo Starting SheetFlow. Leave this window open.
echo The dashboard opens in your browser.
"%ROOT%python\python.exe" "%ROOT%launcher.py"
if errorlevel 1 (
  echo.
  echo SheetFlow stopped. If a log was written, it is in:
  echo %LOCALAPPDATA%\SheetFlow\sheetflow.log
  echo.
  pause
  exit /b 1
)
exit /b 0
