@echo off
setlocal

rem pi resolves the home directory through USERPROFILE on Windows
set "HOME=%PREFIX%\home"
set "USERPROFILE=%PREFIX%\home"

if exist "%PREFIX%\home\.pi\agent\npm" rmdir /s /q "%PREFIX%\home\.pi\agent\npm"
if exist "%PREFIX%\home\.pi\agent\settings.json" del /q "%PREFIX%\home\.pi\agent\settings.json"

rem PLUGINS is set in recipe.yaml
for %%P in (%PLUGINS%) do (
    call pi install npm:%%P
    if errorlevel 1 exit /b 1
)

rem Install herdr integration for pi. Download the herdr binary from its latest preview
rem release (Windows builds are preview-only), run the integration install, then
rem discard the binary.
curl -fsSL --retry 3 --connect-timeout 10 --max-time 20 "https://herdr.dev/preview.json" -o "%TEMP%\herdr-manifest.json" || exit /b 1
for /f "delims=" %%U in ('node -e "process.stdin.setEncoding('utf8');let d='';process.stdin.on('data',function(c){d+=c});process.stdin.on('end',function(){console.log(JSON.parse(d).assets['windows-x86_64'].url)})" ^< "%TEMP%\herdr-manifest.json"') do set "HERDR_URL=%%U"
del "%TEMP%\herdr-manifest.json"
echo Downloading herdr from %HERDR_URL%
curl -fsSL --retry 3 --connect-timeout 10 --max-time 120 "%HERDR_URL%" -o "%TEMP%\herdr.exe" || exit /b 1
rem herdr integration install pi is not supported on Windows — skip without failing.
if not exist "%PREFIX%\home\.pi\agent\extensions" mkdir "%PREFIX%\home\.pi\agent\extensions"
"%TEMP%\herdr.exe" integration install pi || echo WARNING: herdr pi integration skipped (Windows not supported)
del "%TEMP%\herdr.exe"
