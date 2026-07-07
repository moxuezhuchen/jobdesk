$ErrorActionPreference = "Stop"

$patterns = @(
  "814n",
  "814new",
  "100\.112\.123\.8",
  "159\.65\.130\.195",
  "frpc",
  "frps",
  "auth\.token",
  "secretKey",
  "Tailscale",
  "superpowers",
  "agentic workers",
  "C:\\Users\\moxue",
  "JOBDESK_TEST_SERVERS_YAML='C:\\Users\\moxue"
)

$paths = @("README.md", "CHANGELOG.md", "pyproject.toml", "docs", "src", "tests", ".github", "packaging", "scripts")
$existing = $paths | Where-Object { Test-Path -LiteralPath $_ }
$regex = ($patterns -join "|")

$hits = rg -n --hidden `
  -g "!.git/**" `
  -g "!.pytest_tmp*/**" `
  -g "!.mypy_cache/**" `
  -g "!.ruff_cache/**" `
  -g "!build/**" `
  -g "!dist/**" `
  -g "!docs/superpowers/**" `
  -e $regex `
  $existing 2>$null

if ($LASTEXITCODE -eq 0) {
  $realHits = @($hits | Where-Object { $_ -notmatch '^scripts[\\/]+check_public_tree\.ps1:' })
  if ($realHits.Count -gt 0) {
    $realHits
    throw "Public tree contains private/internal patterns."
  }
  Write-Output "public-tree-ok"
  exit 0
}

if ($LASTEXITCODE -ne 1) {
  throw "rg failed while scanning public tree."
}

Write-Output "public-tree-ok"
