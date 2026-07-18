# ConfFlow 1.3.0 Wheel Deployment Verification Script (PowerShell)
#
# Usage:
#   1. Open PowerShell as Administrator
#   2. Run: .\scripts\verify_confflow_wheel.ps1
#
# Prerequisites:
#   - C:\dft\tool\confflow-dist\confflow-1.3.0-py3-none-any.whl exists
#   - C:\dft\tool\jobdesk-dev exists
#   - Python 3.11+ with py launcher

param(
    [string]$WheelPath = "C:\dft\tool\confflow-dist\confflow-1.3.0-py3-none-any.whl",
    [string]$JobdeskPath = "C:\dft\tool\jobdesk-dev",
    [string]$VenvPath = "C:\dft\tool\verify-venv"
)

$ErrorActionPreference = "Stop"

Write-Host "================================================"
Write-Host "ConfFlow 1.3.0 Wheel Deployment Verification"
Write-Host "================================================"
Write-Host ""

# Step 1: Check prerequisites
Write-Host "[1/5] Checking prerequisites..."
if (-not (Test-Path $WheelPath)) {
    Write-Host "ERROR: confflow wheel not found at $WheelPath" -ForegroundColor Red
    Write-Host "Please build the wheel first following docs\CONFFLOW_1_3_0_WHEEL_DEPLOYMENT.md"
    exit 1
}
if (-not (Test-Path $JobdeskPath)) {
    Write-Host "ERROR: jobdesk-dev not found at $JobdeskPath" -ForegroundColor Red
    exit 1
}
Write-Host "  OK: Prerequisites met" -ForegroundColor Green
Write-Host ""

# Step 2: Create clean venv
Write-Host "[2/5] Creating clean virtual environment..."
if (Test-Path $VenvPath) {
    Write-Host "  Removing existing venv..."
    Remove-Item -Recurse -Force $VenvPath
}
& py -m venv $VenvPath
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Failed to create virtual environment" -ForegroundColor Red
    exit 1
}
$activateScript = Join-Path $VenvPath "Scripts\Activate.ps1"
if (-not (Test-Path $activateScript)) {
    $activateScript = Join-Path $VenvPath "Scripts\activate.bat"
    & $activateScript
} else {
    & $activateScript
}
Write-Host "  OK: Virtual environment created" -ForegroundColor Green
Write-Host ""

# Step 3: Install confflow wheel
Write-Host "[3/5] Installing confflow wheel..."
& py -m pip install $WheelPath
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Failed to install confflow wheel" -ForegroundColor Red
    exit 1
}
Write-Host "  OK: confflow wheel installed" -ForegroundColor Green
Write-Host ""

# Step 4: Verify confflow version
Write-Host "[4/5] Verifying confflow version..."
$version = python -c "import confflow; print(confflow.__version__)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Failed to import confflow" -ForegroundColor Red
    exit 1
}
if ($version -ne "1.3.0") {
    Write-Host "ERROR: confflow version is $version, expected 1.3.0" -ForegroundColor Red
    exit 1
}
Write-Host "  OK: confflow version is 1.3.0" -ForegroundColor Green
Write-Host ""

# Step 5: Install jobdesk with chem extra
Write-Host "[5/5] Installing jobdesk with chem extra..."
& py -m pip install -e "${JobdeskPath}[chem]"
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Failed to install jobdesk" -ForegroundColor Red
    exit 1
}
Write-Host "  OK: jobdesk installed" -ForegroundColor Green
Write-Host ""

# Step 6: Run verification tests
Write-Host ""
Write-Host "================================================"
Write-Host "Running Phase 3 Tests"
Write-Host "================================================"

Push-Location $JobdeskPath
try {
    & py -m pytest tests\test_confflow_results.py tests\test_run_monitor_checkpoint.py tests\test_workflow_spec.py tests\test_gui_settings.py -v
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "================================================"
        Write-Host "TESTS FAILED" -ForegroundColor Red
        Write-Host "================================================"
        exit 1
    }
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "================================================"
Write-Host "ALL CHECKS PASSED" -ForegroundColor Green
Write-Host "================================================"
Write-Host ""
Write-Host "Deployment verification successful:"
Write-Host "  - confflow 1.3.0 wheel installed"
Write-Host "  - jobdesk[chem] installed"
Write-Host "  - Phase 3 tests passed"
Write-Host ""
Write-Host "You may now proceed with vendored subtree deletion."
Write-Host ""
