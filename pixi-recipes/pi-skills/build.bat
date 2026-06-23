@echo off
setlocal

if not exist "%PREFIX%\home\.pi\agent\agents" mkdir "%PREFIX%\home\.pi\agent\agents"
xcopy /s /e /y agents "%PREFIX%\home\.pi\agent\agents\"
if not exist "%PREFIX%\home\.pi\agent\skills" mkdir "%PREFIX%\home\.pi\agent\skills"
xcopy /s /e /y skills "%PREFIX%\home\.pi\agent\skills\"
xcopy /s /e /y AGENTS.md "%PREFIX%\home\.pi\agent\"
