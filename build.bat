@echo off
setlocal EnableExtensions
cd /d "%~dp0"

rem Builds WireSpot.exe: the desktop app (Rust + Vue, in app\) with its engine
rem and CLI (Python, packaged with PyInstaller) inside, so it's one download.
rem Needs Python 3.11+ (py launcher), Node.js with pnpm, and Rust (cargo).

echo [1/7] Checking the tools...
py -3 --version >nul 2>&1
if errorlevel 1 (
  echo Python 3 was not found. Install Python 3.11+ and enable the py launcher.
  pause
  exit /b 1
)
where pnpm >nul 2>&1
if errorlevel 1 (
  echo pnpm was not found. Install Node.js, then run: npm install -g pnpm
  pause
  exit /b 1
)
where cargo >nul 2>&1
if errorlevel 1 (
  echo Rust was not found. Install it from https://rustup.rs
  pause
  exit /b 1
)

echo [2/7] Installing build requirements...
py -3 -m pip install --upgrade -r requirements.txt
if errorlevel 1 (
  echo Failed to install build requirements.
  pause
  exit /b 1
)

echo [3/7] Running unit tests...
set "WIRESPOT_DATA=%TEMP%\wirespot-build-tests"
py -3 -m unittest discover -s tests -t .
if errorlevel 1 (
  echo Unit tests failed - not building.
  pause
  exit /b 1
)
set "WIRESPOT_DATA="

echo [4/7] Drawing the icons...
if exist build rmdir /s /q build
mkdir build
py -3 -m wirespot.icon build\wirespot.ico assets\wirespot.svg
if errorlevel 1 (
  echo Icon generation failed.
  pause
  exit /b 1
)
py -3 -m wirespot.icons assets\icons >nul
rem the app's copies of the icon set and the logo
py -3 app\scripts\export-icons.py >nul

echo [5/7] Building WireSpotEngine.exe and WireSpotCLI.exe...
for %%F in (WireSpotEngine.exe WireSpotCLI.exe) do if exist dist\%%F del /q dist\%%F
rem the engine inherits administrator rights from WireSpot.exe; the CLI asks for them itself
py -3 -m PyInstaller --noconfirm --clean --onefile --console --exclude-module tkinter ^
  --icon "%CD%\build\wirespot.ico" --name WireSpotEngine ^
  --distpath dist --workpath build\pyi --specpath build engine_app.py
if errorlevel 1 (
  echo Engine build failed.
  pause
  exit /b 1
)
py -3 -m PyInstaller --noconfirm --onefile --console --uac-admin --exclude-module tkinter ^
  --icon "%CD%\build\wirespot.ico" --name WireSpotCLI ^
  --distpath dist --workpath build\pyi --specpath build app.py
if errorlevel 1 (
  echo CLI build failed.
  pause
  exit /b 1
)
if not exist app\src-tauri\payload mkdir app\src-tauri\payload
copy /y dist\WireSpotEngine.exe app\src-tauri\payload\WireSpotEngine.exe >nul
copy /y dist\WireSpotCLI.exe app\src-tauri\payload\WireSpotCLI.exe >nul

echo [6/7] Building WireSpot.exe (the app, with the engine and CLI inside)...
pushd app
call pnpm install --frozen-lockfile
if errorlevel 1 (
  popd
  echo Installing the app's packages failed.
  pause
  exit /b 1
)
call pnpm tauri build --no-bundle
if errorlevel 1 (
  popd
  echo App build failed.
  pause
  exit /b 1
)
popd

echo [7/7] Updating release\ and the portable folder...
if not exist release mkdir release
copy /y app\src-tauri\target\release\wirespot-app.exe release\WireSpot.exe >nul
if not exist portable mkdir portable
copy /y release\WireSpot.exe portable\WireSpot.exe >nul
for %%F in (WireSpotCLI.exe WireSpotTray.exe) do if exist portable\%%F del /q portable\%%F
if not exist portable\settings.json copy /y settings.json portable\settings.json >nul
if not exist portable\vpn mkdir portable\vpn
copy /y vpn\README.txt portable\vpn\README.txt >nul

echo.
echo Done.
echo   %CD%\release\WireSpot.exe    the app: started anywhere else, it offers to install itself
echo   %CD%\portable\WireSpot.exe   portable (no install; your data stays next to the exe)
echo.
echo Installed copies keep settings.json and vpn\ in %%APPDATA%%\WireSpot.
echo Installing from this folder moves the profiles from portable\vpn over for you.
echo.
pause
