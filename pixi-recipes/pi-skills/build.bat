@echo off
setlocal

if not exist "%PREFIX%\home\.pi\agent\skills" mkdir "%PREFIX%\home\.pi\agent\skills"
xcopy /s /e /y skills "%PREFIX%\home\.pi\agent\skills\"
