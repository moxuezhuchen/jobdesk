# ConfFlow 1.4.0 Wheel Deployment Verification Script (PowerShell)
#
# Usage:
#   Run: .\scripts\verify_confflow_wheel.ps1
#
# Prerequisites:
#   - C:\dft\tool\confflow-dist\confflow-1.4.0-py3-none-any.whl exists
#   - C:\dft\tool\jobdesk-dev exists
#   - Python 3.11+ with py launcher

param(
    [string]$WheelPath = "C:\dft\tool\confflow-dist\confflow-1.4.0-py3-none-any.whl",
    [string]$JobdeskPath = "C:\dft\tool\jobdesk-dev",
    [string]$VenvPath = "C:\dft\tool\verify-venv"
)

$ErrorActionPreference = "Stop"

Write-Host "================================================"
Write-Host "ConfFlow 1.4.0 Wheel Deployment Verification"
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

# Safety: refuse to delete unsafe paths
$safeToDelete = $true
if ([string]::IsNullOrWhiteSpace($VenvPath)) {
    Write-Host "ERROR: VenvPath cannot be empty" -ForegroundColor Red
    exit 1
}
if ($VenvPath -match '^[A-Za-z]:\\?$') {
    Write-Host "ERROR: Refusing to delete root of a drive: $VenvPath" -ForegroundColor Red
    exit 1
}
if ((Resolve-Path $VenvPath -ErrorAction SilentlyContinue).Path -eq (Resolve-Path $JobdeskPath -ErrorAction SilentlyContinue).Path) {
    Write-Host "ERROR: Refusing to delete JobdeskPath itself: $JobdeskPath" -ForegroundColor Red
    exit 1
}

if (Test-Path $VenvPath) {
    Write-Host "  Removing existing venv..."
    Remove-Item -Recurse -Force $VenvPath
}
& py -m venv $VenvPath
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Failed to create virtual environment" -ForegroundColor Red
    exit 1
}

# Use explicit python.exe path — no activation scripts
$Python = Join-Path $VenvPath "Scripts\python.exe"
if (-not (Test-Path $Python)) {
    Write-Host "ERROR: python.exe not found in venv: $Python" -ForegroundColor Red
    exit 1
}
Write-Host "  OK: Virtual environment created" -ForegroundColor Green
Write-Host ""

# Step 3: Install confflow wheel
Write-Host "[3/5] Installing confflow wheel..."
& $Python -m pip install $WheelPath
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Failed to install confflow wheel" -ForegroundColor Red
    exit 1
}
Write-Host "  OK: confflow wheel installed" -ForegroundColor Green
Write-Host ""

# Step 4: Verify confflow version
Write-Host "[4/5] Verifying confflow version..."
$version = & $Python -c "import confflow; print(confflow.__version__)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Failed to import confflow" -ForegroundColor Red
    exit 1
}
if ($version -ne "1.4.0") {
    Write-Host "ERROR: confflow version is $version, expected 1.4.0" -ForegroundColor Red
    exit 1
}
Write-Host "  OK: confflow version is 1.4.0" -ForegroundColor Green
Write-Host ""

# Step 5: Install jobdesk with chem extra
Write-Host "[5/5] Installing jobdesk with chem extra..."
& $Python -m pip install -e "${JobdeskPath}[chem]"
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Failed to install jobdesk" -ForegroundColor Red
    exit 1
}
Write-Host "  OK: jobdesk installed" -ForegroundColor Green
Write-Host ""

# Step 5b: Install test runner (pytest lives in the [dev] extra, not [chem]).
# Install only pytest + pytest-qt explicitly: the full [dev] extra pins
# numpy<2.4 on py3.13, which would downgrade the numpy that confflow pulled
# in and perturb the verified clean-install dependency set.
Write-Host "  Installing test runner (pytest, pytest-qt)..."
& $Python -m pip install "pytest>=8.0" "pytest-qt>=4.4"
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Failed to install test runner" -ForegroundColor Red
    exit 1
}
Write-Host "  OK: test runner installed" -ForegroundColor Green
Write-Host ""

# Step 6: Run verification tests
Write-Host ""
Write-Host "================================================"
Write-Host "Running Phase 3 Tests"
Write-Host "================================================"

Push-Location $JobdeskPath
try {
    & $Python -m pytest tests\test_confflow_results.py tests\test_run_monitor_checkpoint.py tests\test_workflow_spec.py tests\test_gui_settings.py -v
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
Write-Host "  - confflow 1.4.0 wheel installed"
Write-Host "  - jobdesk[chem] installed"
Write-Host "  - Phase 3 tests passed"
Write-Host ""
Write-Host "You may now proceed with vendored subtree deletion."
Write-Host ""
