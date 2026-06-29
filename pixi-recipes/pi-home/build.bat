@echo off
setlocal

rem bin/ will be populated on first start with downloaded executables
if not exist "%PREFIX%\home\.pi\agent\bin" mkdir "%PREFIX%\home\.pi\agent\bin"
type nul > "%PREFIX%\home\.pi\agent\bin\.keep"

rem Copy bundled skills into the pi agent skills directory (flat copy)
if not exist "%PREFIX%\home\.pi\agent\skills" mkdir "%PREFIX%\home\.pi\agent\skills"
xcopy /s /e /y skills "%PREFIX%\home\.pi\agent\skills\"
xcopy /s /e /y AGENTS.md "%PREFIX%\home\.pi\agent\"
xcopy /s /e /y keybindings.json "%PREFIX%\home\.pi\agent\"
