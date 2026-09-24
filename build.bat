@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo [1/6] Checking Python...
py -3 --version >nul 2>&1
if errorlevel 1 (
  echo Python 3 was not found. Install Python 3.11+ and enable the py launcher.
  pause
  exit /b 1
)

echo [2/6] Installing build requirements...
py -3 -m pip install --upgrade -r requirements.txt
if errorlevel 1 (
  echo Failed to install build requirements.
  pause
  exit /b 1
)

echo [3/6] Running unit tests...
set "WIRESPOT_DATA=%TEMP%\wirespot-build-tests"
py -3 -m unittest discover -s tests -t .
if errorlevel 1 (
  echo Unit tests failed - not building.
  pause
  exit /b 1
)
set "WIRESPOT_DATA="

echo [4/6] Building WireSpot.exe (app) and WireSpotCLI.exe (CLI)...
if exist build rmdir /s /q build
for %%F in (WireSpot.exe WireSpotCLI.exe WireSpotSetup.exe) do if exist dist\%%F del /q dist\%%F
mkdir build
py -3 -m wirespot.icon build\wirespot.ico assets\wirespot.svg
if errorlevel 1 (
  echo Icon generation failed.
  pause
  exit /b 1
)
py -3 -m wirespot.icons assets\icons >nul
py -3 -m wirespot.icon splash build\splash.png >nul
py -3 -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --console ^
  --uac-admin ^
  --icon build\wirespot.ico ^
  --name WireSpotCLI ^
  --exclude-module tkinter ^
  app.py
if errorlevel 1 (
  echo CLI build failed.
  pause
  exit /b 1
)
py -3 -m PyInstaller ^
  --noconfirm ^
  --onefile ^
  --noconsole ^
  --uac-admin ^
  --icon build\wirespot.ico ^
  --name WireSpot ^
  wirespot_app.py
if errorlevel 1 (
  echo App build failed.
  pause
  exit /b 1
)

echo [5/6] Building WireSpotSetup.exe (installer with both programs inside)...
py -3 -m PyInstaller ^
  --noconfirm ^
  --onefile ^
  --noconsole ^
  --uac-admin ^
  --icon build\wirespot.ico ^
  --name WireSpotSetup ^
  --splash build\splash.png ^
  --add-data "dist\WireSpot.exe;payload" ^
  --add-data "dist\WireSpotCLI.exe;payload" ^
  setup_app.py
if errorlevel 1 (
  echo Setup build failed.
  pause
  exit /b 1
)
for %%F in (WireSpot WireSpotCLI WireSpotSetup) do if exist %%F.spec del /q %%F.spec

echo [6/6] Updating release\ and the portable folder...
if not exist release mkdir release
copy /y dist\WireSpotSetup.exe release\WireSpotSetup.exe >nul
if not exist portable mkdir portable
copy /y dist\WireSpot.exe portable\WireSpot.exe >nul
copy /y dist\WireSpotCLI.exe portable\WireSpotCLI.exe >nul
if exist portable\WireSpotTray.exe del /q portable\WireSpotTray.exe
if not exist portable\settings.json copy /y settings.json portable\settings.json >nul
if not exist portable\vpn mkdir portable\vpn
copy /y vpn\README.txt portable\vpn\README.txt >nul

echo.
echo Done.
echo   %CD%\release\WireSpotSetup.exe   installer: installs WireSpot, puts a shortcut on your desktop
echo   %CD%\portable\WireSpot.exe       portable app (no install; data next to the exe)
echo.
echo Installed copies keep settings.json and vpn\ in %%APPDATA%%\WireSpot.
echo Run setup from this folder and it moves the profiles from portable\vpn over for you.
echo.
pause
