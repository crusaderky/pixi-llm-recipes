@echo off
setlocal EnableDelayedExpansion

:: Configure backend-specific CMake flags.
set "EXTRA_CMAKE_ARGS="
if /i "%BACKEND%"=="cuda"   set "EXTRA_CMAKE_ARGS=-DGGML_CUDA=ON"
if /i "%BACKEND%"=="vulkan" set "EXTRA_CMAKE_ARGS=-DGGML_VULKAN=ON"

if /i not "%BACKEND%"=="cpu" (
    if /i not "%BACKEND%"=="cuda" (
        if /i not "%BACKEND%"=="vulkan" (
            echo Unknown backend: %BACKEND%
            exit /b 1
        )
    )
)

:: Self-contained Windows build using GCC 15 (gcc_win-64) + modern UCRT-based
:: MinGW-w64 sysroot — no system Visual Studio required.
:: The gcc_win-64 / gxx_win-64 activation sets CC, CXX, CFLAGS, CXXFLAGS,
:: so CMake picks up the compiler automatically.
::
:: CUDA note: NVCC on Windows requires MSVC (cl.exe) as host compiler —
:: a hard NVIDIA constraint.  A CUDA win-64 build therefore needs a system
:: Visual Studio installation (use the vs2022_win-64 conda package with
:: VS Build Tools 2022 installed on the host).

:: On Windows, executables and DLLs (runtime backend plugins) must be co-located
:: so that LoadLibrary("ggml-cuda.dll") succeeds via the exe-directory search path.
:: Library/bin satisfies both: CMake puts DLLs there and conda adds it to PATH.
cmake -S . -B build -G Ninja ^
    -DCMAKE_BUILD_TYPE=Release ^
    "-DCMAKE_INSTALL_PREFIX=%PREFIX%" ^
    -DCMAKE_INSTALL_BINDIR=Library/bin ^
    -DCMAKE_INSTALL_LIBDIR=Library/lib ^
    -DGGML_BACKEND_DL=ON ^
    -DGGML_NATIVE=OFF ^
    -DGGML_CPU_ALL_VARIANTS=ON ^
    -DGGML_RPC=ON ^
    -DLLAMA_BUILD_TESTS=OFF ^
    -DLLAMA_BUILD_EXAMPLES=OFF ^
    -DLLAMA_BUILD_TOOLS=ON ^
    -DLLAMA_BUILD_SERVER=ON ^
    %EXTRA_CMAKE_ARGS%
if %ERRORLEVEL% neq 0 exit /b %ERRORLEVEL%

cmake --build build --config Release --parallel %CPU_COUNT%
if %ERRORLEVEL% neq 0 exit /b %ERRORLEVEL%

cmake --install build --config Release
if %ERRORLEVEL% neq 0 exit /b %ERRORLEVEL%
