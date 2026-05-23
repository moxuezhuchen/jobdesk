Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

# Use the installed GUI entry point (Windows GUI subsystem, no console).
# For debug with console output: python -m jobdesk_app.gui.app
jobdesk-gui
