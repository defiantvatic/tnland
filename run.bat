@echo off
REM One-command start: creates a virtualenv on first run, then launches.
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo First run - creating a virtual environment and installing dependencies.
  echo This takes a couple of minutes and only happens once.
  echo.
  python -m venv .venv
  if errorlevel 1 (
    echo.
    echo Could not run Python. Install it from https://python.org
    echo and be sure to tick "Add python.exe to PATH" during setup.
    pause
    exit /b 1
  )
  REM Upgrade pip via "python -m pip", never "pip install --upgrade pip".
  REM On Windows pip.exe cannot overwrite itself while it is running and
  REM the install fails with a permission error.
  ".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
  if errorlevel 1 (
    echo.
    echo Dependency install failed. Scroll up for the reason.
    pause
    exit /b 1
  )
  echo Setup complete.
  echo.
)

".venv\Scripts\python.exe" -m tnland %*
if errorlevel 1 pause
