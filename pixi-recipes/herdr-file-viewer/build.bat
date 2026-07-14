@echo off
setlocal

:: Install the herdr-file-viewer plugin into the conda prefix (Windows only;
:: Linux is handled by build.sh). See build.sh for the on-disk layout.
::
:: VERSION and SHA256_WIN_64 are set by recipe.yaml (build.script.env).

set "TRIPLE=x86_64-pc-windows-msvc"
set "REPO=smarzban/herdr-file-viewer"
set "RELEASE=https://github.com/%REPO%/releases/download/v%VERSION%"
set "RAW=https://raw.githubusercontent.com/%REPO%/v%VERSION%"

set "ROOT=%PREFIX%\home\.config\herdr\plugins\herdr-file-viewer"
if not exist "%ROOT%\scripts" mkdir "%ROOT%\scripts"
if not exist "%ROOT%\target\release" mkdir "%ROOT%\target\release"

:: --- download + verify the prebuilt viewer .exe -------------------------------------------
set "ASSET=herdr-file-viewer-%TRIPLE%.exe"
echo Downloading %RELEASE%/%ASSET%
curl -fsSL --retry 3 --connect-timeout 10 --max-time 180 "%RELEASE%/%ASSET%" -o "%ROOT%\target\release\herdr-file-viewer.exe" || exit /b 1
echo Verifying sha256...
set "ACTUAL="
for /f "tokens=1" %%i in ('certutil -hashfile "%ROOT%\target\release\herdr-file-viewer.exe" SHA256 ^| findstr /v "hash"') do set "ACTUAL=%%i"
if /i not "%ACTUAL%"=="%SHA256_WIN_64%" (
    echo sha256 mismatch!
    echo expected: %SHA256_WIN_64%
    echo actual:   %ACTUAL%
    exit /b 1
)

:: --- fetch the manifest, launcher scripts, and example config from the tagged source -------
curl -fsSL "%RAW%/herdr-plugin.toml"     -o "%ROOT%\herdr-plugin.toml" || exit /b 1
curl -fsSL "%RAW%/config.example.toml"  -o "%ROOT%\config.example.toml" || exit /b 1
for %%s in (fetch-or-build.sh open-file-viewer.sh open-file-viewer-tab.sh install-renderers.sh ^
            fetch-or-build.ps1 open-file-viewer.ps1 open-file-viewer-tab.ps1) do (
    curl -fsSL "%RAW%/scripts/%%s" -o "%ROOT%\scripts\%%s" || exit /b 1
)

:: --- portable registry snippet (no absolute paths) for the inject script -------------------
> "%ROOT%\entry.json" echo {
>> "%ROOT%\entry.json" echo   "plugin_id": "herdr-file-viewer",
>> "%ROOT%\entry.json" echo   "name": "herdr-file-viewer",
>> "%ROOT%\entry.json" echo   "version": "%VERSION%",
>> "%ROOT%\entry.json" echo   "min_herdr_version": "0.7.0",
>> "%ROOT%\entry.json" echo   "description": "A git-aware, read-only file viewer: a keyboard-driven TUI in a herdr split pane."
>> "%ROOT%\entry.json" echo }

echo herdr-file-viewer plugin installed to %ROOT%