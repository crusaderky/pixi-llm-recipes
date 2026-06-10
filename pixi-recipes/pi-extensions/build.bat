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
