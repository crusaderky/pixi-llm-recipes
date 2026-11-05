@echo off
rem The Windows packaging path is a later slice (the deliberately unsafe
rem `opendesigner-unsafe` launcher). It is not implemented yet, and the recipe
rem is skipped on non-linux-x86_64 platforms via build.skip in recipe.yaml, so
rem this script should never actually run. Fail fast with a clear message if it
rem is ever invoked.
echo opendesigner: the Windows build path is not implemented yet (see recipe.yaml build.skip).>&2
exit /b 1
