@echo off
REM Build ConfFlow 1.3.0 wheel from upstream source (isolated build)
REM
REM Usage:
REM   1. Open PowerShell or Command Prompt
REM   2. Run: scripts\build_confflow_wheel.bat
REM
REM This script uses an isolated build directory (C:\dft\tool\confflow-build)
REM and never touches any existing C:\dft\tool\ConfFlow worktree.

setlocal EnableExtensions EnableDelayedExpansion

echo ================================================
echo ConfFlow 1.3.0 Wheel Builder (Isolated)
echo ================================================
echo.

set BUILD_SRC=C:\dft\tool\confflow-build
set WHEEL_OUT=C:\dft\tool\confflow-dist
set TARGET_TAG=v1.3.0

REM Step 1: Clone or update isolated build tree
echo [1/3] Setting up build environment...
if exist "C:\dft\tool\ConfFlow" (
    echo   Note: Using isolated build; existing C:\dft\tool\ConfFlow is not modified
)
if not exist "%BUILD_SRC%" (
    echo   Cloning upstream ConfFlow to isolated directory...
    git clone https://github.com/moxuezhuchen/ConfFlow.git "%BUILD_SRC%"
    if errorlevel 1 (
        echo ERROR: Failed to clone repository
        endlocal
        exit /b 1
    )
) else (
    echo   Isolated build directory already exists
)

REM Step 2: Verify working tree is clean
pushd "%BUILD_SRC%"
git fetch --tags
if errorlevel 1 (
    echo ERROR: Failed to fetch tags
    popd
    endlocal
    exit /b 1
)
git status --porcelain > "%TEMP%\build_status.tmp"
findstr /N "." "%TEMP%\build_status.tmp" > nul
if not errorlevel 1 (
    echo ERROR: Build directory has uncommitted changes
    echo Please stash or discard changes before rebuilding:
    echo   cd C:\dft\tool\confflow-build
    echo   git status
    del "%TEMP%\build_status.tmp" 2>nul
    popd
    endlocal
    exit /b 1
)
del "%TEMP%\build_status.tmp" 2>nul

REM Step 3: Verify HEAD is exactly v1.3.0
for /f %%i in ('git rev-parse HEAD') do set "CURRENT_COMMIT=%%i"
REM Use rev-list to dereference annotated tags to their commit SHA
for /f %%i in ('git rev-list -n 1 %TARGET_TAG%') do set "TAG_COMMIT=%%i"

if not "!CURRENT_COMMIT!"=="!TAG_COMMIT!" (
    echo   Checking out %TARGET_TAG%...
    git checkout %TARGET_TAG%
    if errorlevel 1 (
        echo ERROR: Failed to checkout %TARGET_TAG%
        popd
        endlocal
        exit /b 1
    )
    REM Re-fetch CURRENT_COMMIT after checkout
    for /f %%i in ('git rev-parse HEAD') do set "CURRENT_COMMIT=%%i"
)

if not "!CURRENT_COMMIT!"=="!TAG_COMMIT!" (
    echo ERROR: HEAD does not match %TARGET_TAG% after checkout
    echo Expected: !TAG_COMMIT!
    echo Got:      !CURRENT_COMMIT!
    popd
    endlocal
    exit /b 1
)
echo   Verified: HEAD is exactly %TARGET_TAG%

REM Step 4: Build wheel
popd
echo.
echo [2/3] Installing build dependencies...
py -m pip install build wheel --quiet
if errorlevel 1 (
    echo ERROR: Failed to install build dependencies
    endlocal
    exit /b 1
)

echo.
echo [3/3] Building wheel...
if not exist "%WHEEL_OUT%" (
    mkdir "%WHEEL_OUT%"
)

pushd "%BUILD_SRC%"
py -m build --wheel --outdir "%WHEEL_OUT%"
set "BUILD_EXIT=%ERRORLEVEL%"
popd
if not "%BUILD_EXIT%"=="0" (
    echo ERROR: Failed to build wheel
    endlocal
    exit /b %BUILD_EXIT%
)

REM Step 5: Verify wheel
echo.
echo [Verify] Checking wheel file...
if not exist "%WHEEL_OUT%\confflow-1.3.0-py3-none-any.whl" (
    echo ERROR: Expected wheel not found: %WHEEL_OUT%\confflow-1.3.0-py3-none-any.whl
    dir "%WHEEL_OUT%"
    endlocal
    exit /b 1
)

echo.
echo ================================================
echo BUILD SUCCESSFUL
echo ================================================
echo.
echo Isolated build directory: %BUILD_SRC%
echo Wheel output: %WHEEL_OUT%\confflow-1.3.0-py3-none-any.whl
echo.
echo Next steps:
echo   1. Run verification: scripts\verify_confflow_wheel.bat
echo   2. Or install manually:
echo      py -m pip install %WHEEL_OUT%\confflow-1.3.0-py3-none-any.whl
echo      py -m pip install -e "C:\dft\tool\jobdesk-dev[chem]"
echo.
echo NOTE: Your existing C:\dft\tool\ConfFlow worktree was NOT modified.

endlocal
