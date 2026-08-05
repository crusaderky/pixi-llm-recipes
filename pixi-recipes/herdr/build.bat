@echo off
setlocal

:: Install herdr pre-built binary into the conda prefix (Windows only; Linux
:: is handled by build.sh).
::
:: VERSION_PREVIEW and SHA256_PREVIEW_WIN_64 are set by recipe.yaml (build.script.env).
:: Windows builds are preview-only.

set "TARGET=windows-x86_64.zip"
set "URL=https://github.com/herdrdev/herdr/releases/download/%VERSION_PREVIEW%/herdr-%TARGET%"

echo Downloading %URL%...
curl -fsSL --retry 3 --connect-timeout 10 --max-time 120 "%URL%" -o herdr.zip || exit /b 1

echo Verifying sha256...
for /f "tokens=1" %%i in ('certutil -hashfile herdr.zip SHA256 ^| findstr /v "hash"') do set "ACTUAL=%%i"
if /i not "%ACTUAL%"=="%SHA256_PREVIEW_WIN_64%" (
    echo sha256 mismatch!
    echo expected: %SHA256_PREVIEW_WIN_64%
    echo actual:   %ACTUAL%
    exit /b 1
)

echo Extracting herdr.exe from herdr.zip...
tar -xf herdr.zip herdr.exe || exit /b 1

if not exist "%PREFIX%\bin" mkdir "%PREFIX%\bin"
move /y herdr.exe "%PREFIX%\bin\herdr.exe" || exit /b 1

echo herdr installed to %PREFIX%\bin\herdr.exe
