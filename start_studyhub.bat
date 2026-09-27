@echo off
setlocal
cd /d "%~dp0"

echo [StudyHub] Checking Python...
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "from pathlib import Path; marker=Path('.venv/.studyhub_path'); expected=(Path.cwd()/'.venv').resolve(); raise SystemExit(0 if marker.exists() and Path(marker.read_text(encoding='utf-8').strip()) == expected else 1)"
  if errorlevel 1 goto :invalid_venv
  set "PYTHON_CMD=.venv\Scripts\python.exe"
) else (
  where py >nul 2>nul
  if not errorlevel 1 (
    set "PYTHON_CMD=py -3"
  ) else (
    where python >nul 2>nul
    if errorlevel 1 (
      echo Python 3.11 or newer was not found.
      echo Install Python from https://www.python.org/downloads/ and try again.
      pause
      exit /b 1
    )
    set "PYTHON_CMD=python"
  )
)

%PYTHON_CMD% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
  echo StudyHub requires Python 3.11 or newer.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [StudyHub] Creating the local virtual environment...
  %PYTHON_CMD% -m venv .venv
  if errorlevel 1 goto :failed
  ".venv\Scripts\python.exe" -c "from pathlib import Path; Path('.venv/.studyhub_path').write_text(str((Path.cwd()/'.venv').resolve()), encoding='utf-8')"
  if errorlevel 1 goto :failed
)

if not exist ".venv\.studyhub_ready" (
  echo [StudyHub] Installing dependencies. The first run can take a few minutes...
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto :failed

  type nul > ".venv\.studyhub_ready"
)

echo [StudyHub] Initializing or upgrading the database...
".venv\Scripts\python.exe" -m alembic upgrade head
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" scripts\seed_demo.py
if errorlevel 1 goto :failed

echo.
echo [StudyHub] Starting at http://127.0.0.1:5000
echo Press Ctrl+C in this window to stop the server.
start "" http://127.0.0.1:5000
".venv\Scripts\python.exe" -m flask --app app run --host 127.0.0.1
goto :end

:invalid_venv
echo.
echo [StudyHub] This virtual environment belongs to a different project location.
echo Delete "%CD%\.venv" and run start_studyhub.bat again.
pause
exit /b 1

:failed
echo.
echo StudyHub setup failed. Read the error above, then send it to the project owner.
pause
exit /b 1

:end
endlocal
