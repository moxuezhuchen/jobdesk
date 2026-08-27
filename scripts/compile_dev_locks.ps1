param(
    [switch]$Check
)

$ErrorActionPreference = "Stop"

# Keep this resolver input deliberately narrow: the checked-in locks are for
# JobDesk's base project plus its dev extra.  The optional chem extra (and its
# ConfFlow/RDKit dependencies) is intentionally not part of this workflow.
$ExpectedUvVersion = "0.11.5"
$ExcludeNewer = "2026-08-19T00:00:00Z"
$Targets = @(
    @{ PythonVersion = "3.11"; LockName = "jobdesk-dev-py311-win_amd64.txt"; NumpyMajor = 1 },
    @{ PythonVersion = "3.12"; LockName = "jobdesk-dev-py312-win_amd64.txt"; NumpyMajor = 1 },
    @{ PythonVersion = "3.13"; LockName = "jobdesk-dev-py313-win_amd64.txt"; NumpyMajor = 2 }
)

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$LockRoot = Join-Path $RepoRoot "requirements\locks"

function Get-UvVersion {
    $versionOutput = (& uv --version).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to execute uv --version."
    }
    if ($versionOutput -notmatch "^uv\s+([0-9]+\.[0-9]+\.[0-9]+)(?:\s|$)") {
        throw "Unexpected uv --version output: $versionOutput"
    }
    return $Matches[1]
}

function Get-CompileCommand {
    param(
        [hashtable]$Target,
        [string]$RelativeLock
    )
    return "uv pip compile pyproject.toml --extra dev --python-version $($Target.PythonVersion) --python-platform windows --generate-hashes --exclude-newer $ExcludeNewer --output-file $RelativeLock"
}

function Add-LockHeader {
    param(
        [string]$Path,
        [hashtable]$Target,
        [string]$RelativeLock,
        [string]$UvVersion
    )

    $body = [System.IO.File]::ReadAllText($Path)
    $command = Get-CompileCommand -Target $Target -RelativeLock $RelativeLock
    $header = @(
        "# JobDesk development lock metadata",
        "# target: Windows x86_64 / Python $($Target.PythonVersion)",
        "# uv-version: $UvVersion",
        "# exclude-newer: $ExcludeNewer",
        "# regenerate: $command",
        ""
    ) -join "`n"
    [System.IO.File]::WriteAllText($Path, $header + $body, [System.Text.UTF8Encoding]::new($false))
}

function Assert-Lock {
    param(
        [string]$Path,
        [hashtable]$Target,
        [string]$RelativeLock,
        [string]$UvVersion
    )

    $text = [System.IO.File]::ReadAllText($Path)
    $requiredHeader = @(
        "# target: Windows x86_64 / Python $($Target.PythonVersion)",
        "# uv-version: $UvVersion",
        "# exclude-newer: $ExcludeNewer",
        "# regenerate: $(Get-CompileCommand -Target $Target -RelativeLock $RelativeLock)"
    )
    foreach ($line in $requiredHeader) {
        if (-not $text.Contains($line)) {
            throw "$RelativeLock is missing required header: $line"
        }
    }

    if ($text -match "(?im)^\s*(?:confflow|rdkit)(?:[<>=!~]|\s|$)") {
        throw "$RelativeLock unexpectedly contains a chem extra dependency."
    }

    $lines = $text -split "`r?`n"
    $packageBlocks = @()
    $current = $null
    foreach ($line in $lines) {
        if ($line -match "^([A-Za-z0-9][A-Za-z0-9_.-]*)==([^\s\\]+)") {
            if ($null -ne $current) {
                $packageBlocks += ,$current
            }
            $current = @{
                Name = $Matches[1].ToLowerInvariant()
                Version = $Matches[2]
                Hashes = @()
            }
            continue
        }
        if ($null -ne $current -and $line -match "^\s+--hash=sha256:[0-9a-f]{64}\s*(?:\\)?$") {
            $current.Hashes += $line
        }
    }
    if ($null -ne $current) {
        $packageBlocks += ,$current
    }
    if ($packageBlocks.Count -eq 0) {
        throw "$RelativeLock did not contain any pinned requirement blocks."
    }
    foreach ($package in $packageBlocks) {
        if ($package.Hashes.Count -eq 0) {
            throw "$RelativeLock package $($package.Name)==$($package.Version) has no sha256 hash."
        }
    }

    $numpy = @($packageBlocks | Where-Object Name -eq "numpy")
    if ($numpy.Count -ne 1) {
        throw "$RelativeLock must contain exactly one numpy pin."
    }
    $numpyMajor = [int]($numpy[0].Version.Split(".")[0])
    if ($numpyMajor -ne $Target.NumpyMajor) {
        throw "$RelativeLock resolved numpy $($numpy[0].Version), expected major $($Target.NumpyMajor)."
    }
}

$actualUvVersion = Get-UvVersion
if ($actualUvVersion -ne $ExpectedUvVersion) {
    throw "This lock workflow requires uv $ExpectedUvVersion, found uv $actualUvVersion."
}

$tempRoot = $null
try {
    if ($Check) {
        $tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("jobdesk-lock-check-" + [guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null
    } else {
        New-Item -ItemType Directory -Force -Path $LockRoot | Out-Null
    }

    foreach ($target in $Targets) {
        $relativeLock = "requirements/locks/$($target.LockName)"
        $existingPath = Join-Path $RepoRoot ($relativeLock -replace "/", "\")
        $outputPath = if ($Check) { Join-Path $tempRoot $target.LockName } else { $existingPath }
        $command = Get-CompileCommand -Target $target -RelativeLock $relativeLock
        $arguments = @(
            "pip", "compile", "pyproject.toml", "--extra", "dev",
            "--python-version", $target.PythonVersion,
            "--python-platform", "windows",
            "--generate-hashes",
            "--exclude-newer", $ExcludeNewer,
            "--custom-compile-command", $command,
            "--output-file", $outputPath
        )
        Write-Host "Generating $relativeLock with uv $actualUvVersion"
        & uv @arguments
        if ($LASTEXITCODE -ne 0) {
            throw "uv pip compile failed for Python $($target.PythonVersion)."
        }
        Add-LockHeader -Path $outputPath -Target $target -RelativeLock $relativeLock -UvVersion $actualUvVersion
        Assert-Lock -Path $outputPath -Target $target -RelativeLock $relativeLock -UvVersion $actualUvVersion

        if ($Check) {
            if (-not (Test-Path -LiteralPath $existingPath -PathType Leaf)) {
                throw "Expected checked-in lock is missing: $relativeLock"
            }
            $expected = [System.IO.File]::ReadAllBytes($existingPath)
            $actual = [System.IO.File]::ReadAllBytes($outputPath)
            if (-not [System.Linq.Enumerable]::SequenceEqual($expected, $actual)) {
                throw "$relativeLock is stale; run scripts/compile_dev_locks.ps1 to regenerate it."
            }
        }
    }
}
finally {
    if ($null -ne $tempRoot -and (Test-Path -LiteralPath $tempRoot)) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
}

if ($Check) {
    Write-Host "Checked-in Windows dev locks match deterministic uv $actualUvVersion regeneration."
} else {
    Write-Host "Generated and validated all checked-in Windows dev locks."
}
