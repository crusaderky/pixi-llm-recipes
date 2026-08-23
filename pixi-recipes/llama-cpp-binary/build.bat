@echo off
setlocal

:: Install pre-built llama.cpp binaries into the conda prefix (Windows only;
:: Linux is handled by build.sh).
::
:: VERSION, FORK, ASSET_PREFIX and BACKEND are set by recipe.yaml
:: (build.script.env). FORK is the GitHub `owner/repo` of the active llama.cpp
:: fork; ASSET_PREFIX is the release-asset file prefix (`beellama` for
:: Anbeeld/beellama.cpp, `llama` for mainline ggml-org/llama.cpp).
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

set "ARCHIVE_URL=https://github.com/%FORK%/releases/download/%VERSION%/%ASSET_PREFIX%-%VERSION%-bin-%ARCHIVE_POSTFIX%"
echo Downloading %ARCHIVE_URL%...
:: Release-asset downloads are intermittently 404 on GitHub; retry.
curl -sL --retry 5 --retry-delay 5 --retry-all-errors "%ARCHIVE_URL%" -o archive.zip || exit /b 1
:: The release asset is a real zip. The bare `tar` on the build PATH is the
:: Git-for-Windows GNU tar (no zip support); the System32 bsdtar handles zip.
"%SystemRoot%\System32\tar.exe" -xf archive.zip || exit /b 1
del archive.zip

:: The Windows release archives don't ship a LICENSE file; fetch it for the
:: recipe's license_file entry. FORK is `owner/repo`; the raw host needs the
:: same path.
curl -sL --retry 5 --retry-delay 5 --retry-all-errors "https://raw.githubusercontent.com/%FORK%/%VERSION%/LICENSE" -o LICENSE || exit /b 1

if not exist "%PREFIX%\bin" mkdir "%PREFIX%\bin"
copy /y *.exe "%PREFIX%\bin" || exit /b 1
copy /y *.dll "%PREFIX%\bin" || exit /b 1
