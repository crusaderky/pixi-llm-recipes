@echo off
setlocal

if not exist "%PREFIX%\home\.claude\skills" mkdir "%PREFIX%\home\.claude\skills"
xcopy /s /e /y skills "%PREFIX%\home\.claude\skills\"
