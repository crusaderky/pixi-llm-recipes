@echo off
setlocal

:: Install pre-built llama.cpp binaries into the conda prefix (Windows only;
:: Linux is handled by build.sh).
::
:: VERSION and BACKEND are set by recipe.yaml (build.script.env).
::
:: Executables and DLLs are installed together into %PREFIX%\bin: that
:: directory is on PATH in activated pixi environments, executables find their
:: DLLs in their own directory, and dynamic backend loading (GGML_BACKEND_DL)
:: scans the executable's directory for ggml-*.dll.

if "%BACKEND%"=="cpu" (
    set "ARCHIVE_POSTFIX=win-cpu-x64.zip"
) else if "%BACKEND%"=="vulkan" (
    set "ARCHIVE_POSTFIX=win-vulkan-x64.zip"
) else (
    echo Invalid backend=%BACKEND%
    exit /b 1
)

set "ARCHIVE_URL=https://github.com/ggml-org/llama.cpp/releases/download/%VERSION%/llama-%VERSION%-bin-%ARCHIVE_POSTFIX%"
echo Downloading %ARCHIVE_URL%...
curl -sL "%ARCHIVE_URL%" -o archive.zip || exit /b 1
tar -xf archive.zip || exit /b 1
del archive.zip

:: The Windows release archives don't ship a LICENSE file; fetch it for the
:: recipe's license_file entry.
curl -sL "https://raw.githubusercontent.com/ggml-org/llama.cpp/%VERSION%/LICENSE" -o LICENSE || exit /b 1

if not exist "%PREFIX%\bin" mkdir "%PREFIX%\bin"
copy /y *.exe "%PREFIX%\bin" || exit /b 1
copy /y *.dll "%PREFIX%\bin" || exit /b 1
