@echo off
REM ConfFlow 1.3.0 Wheel Deployment Verification Script
REM
REM Usage:
REM   1. Open PowerShell as Administrator
REM   2. Run: .\scripts\verify_confflow_wheel.ps1
REM
REM Prerequisites:
REM   - C:\dft\tool\confflow-dist\confflow-1.3.0-py3-none-any.whl exists
REM   - C:\dft\tool\jobdesk-dev exists
REM   - Python 3.11+ with py launcher

setlocal enabledelayedexpansion

echo ================================================
echo ConfFlow 1.3.0 Wheel Deployment Verification
echo ================================================
echo.

REM Step 1: Check prerequisites
echo [1/5] Checking prerequisites...
if not exist "C:\dft\tool\confflow-dist\confflow-1.3.0-py3-none-any.whl" (
    echo ERROR: confflow wheel not found at C:\dft\tool\confflow-dist\confflow-1.3.0-py3-none-any.whl
    echo Please build the wheel first following docs\CONFFLOW_1_3_0_WHEEL_DEPLOYMENT.md
    exit /b 1
)
if not exist "C:\dft\tool\jobdesk-dev" (
    echo ERROR: jobdesk-dev not found at C:\dft\tool\jobdesk-dev
    exit /b 1
)
echo   OK: Prerequisites met
echo.

REM Step 2: Create clean venv
echo [2/5] Creating clean virtual environment...
if exist "C:\dft\tool\verify-venv" (
    echo   Removing existing venv...
    rmdir /s /q "C:\dft\tool\verify-venv"
)
py -m venv C:\dft\tool\verify-venv
if errorlevel 1 (
    echo ERROR: Failed to create virtual environment
    exit /b 1
)
call C:\dft\tool\verify-venv\Scripts\activate.bat
echo   OK: Virtual environment created
echo.

REM Step 3: Install confflow wheel
echo [3/5] Installing confflow wheel...
py -m pip install C:\dft\tool\confflow-dist\confflow-1.3.0-py3-none-any.whl
if errorlevel 1 (
    echo ERROR: Failed to install confflow wheel
    exit /b 1
)
echo   OK: confflow wheel installed
echo.

REM Step 4: Verify confflow version
echo [4/5] Verifying confflow version...
for /f "tokens=2" %%v in ('py -c "import confflow; print(confflow.__version__)"') do set CONFFLOW_VERSION=%%v
if not "%CONFFLOW_VERSION%"=="1.3.0" (
    echo ERROR: confflow version is %CONFFLOW_VERSION%, expected 1.3.0
    exit /b 1
)
echo   OK: confflow version is 1.3.0
echo.

REM Step 5: Install jobdesk with chem extra
echo [5/5] Installing jobdesk with chem extra...
py -m pip install -e "C:\dft\tool\jobdesk-dev[chem]"
if errorlevel 1 (
    echo ERROR: Failed to install jobdesk
    exit /b 1
)
echo   OK: jobdesk installed
echo.

REM Step 6: Run verification tests
echo.
echo ================================================
echo Running Phase 3 Tests
echo ================================================
cd C:\dft\tool\jobdesk-dev
py -m pytest tests\test_confflow_results.py tests\test_run_monitor_checkpoint.py tests\test_workflow_spec.py tests\test_gui_settings.py -v
if errorlevel 1 (
    echo.
    echo ================================================
    echo TESTS FAILED
    echo ================================================
    exit /b 1
)

echo.
echo ================================================
echo ALL CHECKS PASSED
echo ================================================
echo.
echo Deployment verification successful:
echo   - confflow 1.3.0 wheel installed
echo   - jobdesk[chem] installed
echo   - Phase 3 tests passed
echo.
echo You may now proceed with vendored subtree deletion.
echo.
deactivate
