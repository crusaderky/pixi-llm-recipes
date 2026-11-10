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

rem pi-llama-cpp never disconnects the llama-server SSE stream it opens to follow
rem a model load, and that stream reconnects forever, so `pi -p` prints its answer
rem and then never exits (upstream gsanhueza/pi-llama-cpp#40). Close it on
rem session_shutdown. Hard error by design: if a plugin bump makes the patch stop
rem applying, the package build fails instead of shipping a plugin that wedges
rem every `pi -p` step in .github/workflows/pi.yml.
rem The cwd is the directory the recipe files were copied into (the same
rem assumption pi-home/build.sh makes with `cp -a skills`); the check turns a
rem wrong assumption into a message rather than "Cannot find module".
set "PATCH=patches\pi-llama-cpp-sse-shutdown.cjs"
if not exist "%PATCH%" (
    echo build.bat: %PATCH% not found relative to %CD% 1>&2
    exit /b 1
)
node "%PATCH%" "%PREFIX%\home\.pi\agent\npm\node_modules\pi-llama-cpp"
if errorlevel 1 exit /b 1

npm approve-scripts --allow-scripts-pending

rem Install rtk integration for pi
rtk init -g --agent pi --auto-patch
