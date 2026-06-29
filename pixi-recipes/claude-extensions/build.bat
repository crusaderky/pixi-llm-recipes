@echo off
setlocal

rem Install herdr integration for Claude Code.
rem Downloads the herdr binary from its latest preview release (Windows builds
rem are preview-only), runs `herdr integration install claude`, then deploys
rem the output to the prefix.

rem On Windows rtk -g ignores HOME/USERPROFILE and resolves the Claude config
rem dir via the real user profile (C:\Users\runneradmin\.claude), which the
rem conda build must not touch. CLAUDE_CONFIG_DIR overrides that resolution so
rem rtk writes into the prefix instead. herdr honours HOME/USERPROFILE.
set "HOME=%PREFIX%\home"
set "USERPROFILE=%PREFIX%\home"
set "CLAUDE_CONFIG_DIR=%PREFIX%\home\.claude"
if not exist "%HOME%\.claude\hooks" mkdir "%HOME%\.claude\hooks"

rem Download herdr binary via the preview manifest
curl -fsSL --retry 3 --connect-timeout 10 --max-time 20 "https://herdr.dev/preview.json" -o "%TEMP%\herdr-manifest.json" || exit /b 1
for /f "delims=" %%U in ('node -e "process.stdin.setEncoding('utf8');let d='';process.stdin.on('data',function(c){d+=c});process.stdin.on('end',function(){console.log(JSON.parse(d).assets['windows-x86_64'].url)})" ^< "%TEMP%\herdr-manifest.json"') do set "HERDR_URL=%%U"
del "%TEMP%\herdr-manifest.json"

echo Downloading herdr from %HERDR_URL%
curl -fsSL --retry 3 --connect-timeout 10 --max-time 120 "%HERDR_URL%" -o "%TEMP%\herdr.exe" || exit /b 1

"%TEMP%\herdr.exe" integration install claude || exit /b 1
del "%TEMP%\herdr.exe"

rem Install the rtk integration last, so its PreToolUse hook merges into the
rem settings.json that herdr just wrote rather than being clobbered by it.
rem --auto-patch patches settings.json without the interactive prompt (the
rem conda build has no TTY). rtk leaves a settings.json.bak we don't want to package.
if not exist "%HOME%\.claude" mkdir "%HOME%\.claude"
rtk init -g --auto-patch || exit /b 1
if exist "%HOME%\.claude\settings.json.bak" del "%HOME%\.claude\settings.json.bak"
