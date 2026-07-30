@echo off
setlocal

rem Install the rtk integration for Claude Code.
rem Generates CLAUDE.md, RTK.md, and patches settings.json.

set "HOME=%PREFIX%\home"
set "USERPROFILE=%PREFIX%\home"
set "CLAUDE_CONFIG_DIR=%PREFIX%\home\.claude"
if not exist "%HOME%\.claude\hooks" mkdir "%HOME%\.claude\hooks"

if not exist "%HOME%\.claude" mkdir "%HOME%\.claude"
rtk init -g --auto-patch || exit /b 1
if exist "%HOME%\.claude\settings.json.bak" del "%HOME%\.claude\settings.json.bak"
