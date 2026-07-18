@echo off
REM Build ConfFlow 1.3.0 wheel from upstream source
REM
REM Usage:
REM   1. Open PowerShell or Command Prompt
REM   2. Run: scripts\build_confflow_wheel.bat
REM
REM This script will:
REM   - Clone the upstream ConfFlow repo (if not exists)
REM   - Checkout v1.3.0 tag
REM   - Build the wheel

setlocal enabledelayedexpansion

echo ================================================
echo ConfFlow 1.3.0 Wheel Builder
echo ================================================
echo.

REM Check if upstream already exists
if exist "C:\dft\tool\ConfFlow" (
    echo [INFO] Upstream ConfFlow found at C:\dft\tool\ConfFlow
    echo.
    echo Checking version...
    cd C:\dft\tool\ConfFlow
    git describe --tags --abbrev=0 > temp_tag.txt
    set /p CURRENT_TAG=<temp_tag.txt
    del temp_tag.txt
    if "!CURRENT_TAG!"=="v1.3.0" (
        echo   Already at v1.3.0
    ) else (
        echo   Current tag: !CURRENT_TAG!
        echo   Checking out v1.3.0...
        git checkout v1.3.0
    )
) else (
    echo [1/3] Cloning upstream ConfFlow...
    git clone https://github.com/moxuezhuchen/ConfFlow.git C:\dft\tool\ConfFlow
    if errorlevel 1 (
        echo ERROR: Failed to clone repository
        exit /b 1
    )
    cd C:\dft\tool\ConfFlow
    echo   OK: Repository cloned
    echo.
    echo [2/3] Checking out v1.3.0...
    git checkout v1.3.0
    if errorlevel 1 (
        echo ERROR: Failed to checkout v1.3.0
        exit /b 1
    )
    echo   OK: Checked out v1.3.0
)

echo.
echo [3/3] Building wheel...
cd C:\dft\tool\ConfFlow

REM Install build dependencies
py -m pip install build wheel --quiet

REM Create dist directory
if not exist "C:\dft\tool\confflow-dist" (
    mkdir C:\dft\tool\confflow-dist
)

REM Build wheel
py -m build --wheel --outdir C:\dft\tool\confflow-dist
if errorlevel 1 (
    echo ERROR: Failed to build wheel
    exit /b 1
)

echo.
echo ================================================
echo BUILD SUCCESSFUL
echo ================================================
echo.
echo Wheel location: C:\dft\tool\confflow-dist\
dir C:\dft\tool\confflow-dist\confflow*.whl
echo.
echo Next steps:
echo   1. Run verification: scripts\verify_confflow_wheel.bat
echo   2. Or install manually:
echo      py -m pip install C:\dft\tool\confflow-dist\confflow-1.3.0-py3-none-any.whl
echo      py -m pip install -e "C:\dft\tool\jobdesk-dev[chem]"
