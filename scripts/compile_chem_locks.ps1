param(
    [switch]$Check,
    [string]$CacheDirectory
)

$ErrorActionPreference = "Stop"

$ExpectedUvVersion = "0.11.5"
$ExcludeNewer = "2026-08-19T00:00:00Z"
$Targets = @(
    @{ PythonVersion = "3.11"; LockName = "jobdesk-chem-py311-win_amd64.txt" },
    @{ PythonVersion = "3.12"; LockName = "jobdesk-chem-py312-win_amd64.txt" },
    @{ PythonVersion = "3.13"; LockName = "jobdesk-chem-py313-win_amd64.txt" }
)

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ArtifactRelative = ".matrix-artifacts/confflow-2.1.6-py3-none-any.whl"
$ArtifactPath = Join-Path $RepoRoot ($ArtifactRelative -replace "/", "\")
$ArtifactDirectory = Split-Path -Parent $ArtifactPath
$LockRoot = Join-Path $RepoRoot "requirements\locks"
$ManifestPath = Join-Path $LockRoot "jobdesk-chem-wheel-manifest.json"
$ConstraintRelative = "requirements/inputs/jobdesk-chem-confflow.txt"
$NumpyOverrideRelative = "requirements/inputs/jobdesk-chem-numpy-override.txt"
$ConstraintPath = Join-Path $RepoRoot ($ConstraintRelative -replace "/", "\")
$NumpyOverridePath = Join-Path $RepoRoot ($NumpyOverrideRelative -replace "/", "\")

if ([string]::IsNullOrWhiteSpace($CacheDirectory)) {
    $CacheDirectory = Join-Path $RepoRoot ".matrix-logs\uv-cache-chem"
} elseif (-not [System.IO.Path]::IsPathRooted($CacheDirectory)) {
    $CacheDirectory = Join-Path $RepoRoot $CacheDirectory
}
$CacheDirectory = [System.IO.Path]::GetFullPath($CacheDirectory)

if (-not (Test-Path -LiteralPath $ArtifactPath -PathType Leaf)) {
    throw "Required local ConfFlow wheel is missing: $ArtifactRelative"
}
New-Item -ItemType Directory -Force -Path $CacheDirectory | Out-Null
New-Item -ItemType Directory -Force -Path $LockRoot | Out-Null

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

function Read-WheelMetadata {
    param([string]$Path)

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [System.IO.Compression.ZipFile]::OpenRead($Path)
    try {
        $entry = $archive.Entries |
            Where-Object { $_.FullName -match "\.dist-info/METADATA$" } |
            Select-Object -First 1
        if ($null -eq $entry) {
            throw "The wheel has no dist-info/METADATA entry: $Path"
        }
        $stream = $entry.Open()
        $memory = [System.IO.MemoryStream]::new()
        try {
            $stream.CopyTo($memory)
            $bytes = $memory.ToArray()
        } finally {
            $memory.Dispose()
            $stream.Dispose()
        }
        $text = [System.Text.Encoding]::UTF8.GetString($bytes)
        $getValue = {
            param([string]$Field)
            $match = [regex]::Match($text, "(?m)^" + [regex]::Escape($Field) + ":\s*(.+)$")
            if (-not $match.Success) {
                throw "Wheel METADATA is missing $Field"
            }
            return $match.Groups[1].Value.Trim()
        }
        $requiresDist = @([regex]::Matches($text, "(?m)^Requires-Dist:\s*(.+)$") | ForEach-Object {
                $_.Groups[1].Value.Trim()
            })
        return [pscustomobject]@{
            Name = & $getValue "Name"
            Version = & $getValue "Version"
            RequiresPython = & $getValue "Requires-Python"
            RequiresDist = $requiresDist
            Bytes = $bytes
        }
    } finally {
        $archive.Dispose()
    }
}

function Get-Sha256Hex {
    param([byte[]]$Bytes)

    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        $hash = $algorithm.ComputeHash($Bytes)
    } finally {
        $algorithm.Dispose()
    }
    return ([System.BitConverter]::ToString($hash) -replace "-", "").ToLowerInvariant()
}

function Write-Utf8NoBom {
    param([string]$Path, [string]$Text)

    [System.IO.File]::WriteAllText($Path, $Text, [System.Text.UTF8Encoding]::new($false))
}

function Write-LockHeader {
    param(
        [string]$Path,
        [hashtable]$Target,
        [string]$WheelSha256,
        [string]$MetadataSha256,
        [string]$UvVersion
    )

    $body = [System.IO.File]::ReadAllText($Path)
    $header = @(
        "# JobDesk chemistry lock metadata",
        "# target: Windows x86_64 / Python $($Target.PythonVersion)",
        "# uv-version: $UvVersion",
        "# exclude-newer: $ExcludeNewer",
        "# confflow-artifact: $ArtifactRelative",
        "# confflow-sha256: $WheelSha256",
        "# confflow-metadata-sha256: $MetadataSha256",
        "# source: exact published ConfFlow v2.1.6 wheel copied locally; production endpoint unchanged",
        "# regenerate: powershell -ExecutionPolicy Bypass -File scripts/compile_chem_locks.ps1",
        ""
    ) -join "`n"
    Write-Utf8NoBom -Path $Path -Text ($header + $body)
}

function Get-LockPackages {
    param([string]$Path)

    $packages = @()
    $current = $null
    foreach ($line in [System.IO.File]::ReadAllLines($Path)) {
        if ($line -match "^([A-Za-z0-9][A-Za-z0-9_.-]*)==([^\s\\]+)") {
            if ($null -ne $current) {
                $packages += ,$current
            }
            $current = [pscustomobject]@{
                Name = $Matches[1].ToLowerInvariant()
                Version = $Matches[2]
                Hashes = @()
            }
            continue
        }
        if ($null -ne $current -and $line -match "^\s+--hash=sha256:([0-9a-f]{64})\s*(?:\\)?$") {
            $current.Hashes += $Matches[1]
        }
    }
    if ($null -ne $current) {
        $packages += ,$current
    }
    return $packages
}

function Assert-Lock {
    param(
        [string]$Path,
        [hashtable]$Target,
        [string]$WheelSha256,
        [string]$MetadataSha256,
        [string]$UvVersion,
        [string]$ConfflowVersion
    )

    $text = [System.IO.File]::ReadAllText($Path)
    foreach ($required in @(
            "# target: Windows x86_64 / Python $($Target.PythonVersion)",
            "# uv-version: $UvVersion",
            "# exclude-newer: $ExcludeNewer",
            "# confflow-artifact: $ArtifactRelative",
            "# confflow-sha256: $WheelSha256",
            "# confflow-metadata-sha256: $MetadataSha256"
        )) {
        if (-not $text.Contains($required)) {
            throw "$($Target.LockName) is missing required metadata: $required"
        }
    }
    if ($text -match "(?im)^\s*[A-Za-z0-9_.-]+\s*@\s*(?:file|https?)://") {
        throw "$($Target.LockName) contains an unportable direct URL requirement."
    }

    $packages = @(Get-LockPackages -Path $Path)
    if ($packages.Count -eq 0) {
        throw "$($Target.LockName) did not contain pinned requirement blocks."
    }
    foreach ($package in $packages) {
        if (@($package.Hashes).Count -eq 0) {
            throw "$($Target.LockName) package $($package.Name)==$($package.Version) has no sha256 hash."
        }
    }

    $confflow = @($packages | Where-Object { $_.Name -eq "confflow" })
    if ($confflow.Count -ne 1 -or $confflow[0].Version -ne $ConfflowVersion) {
        throw "$($Target.LockName) must pin confflow==$ConfflowVersion."
    }
    if (@($confflow[0].Hashes | Where-Object { $_ -eq $WheelSha256 }).Count -ne 1) {
        throw "$($Target.LockName) does not carry the exact local ConfFlow wheel SHA-256."
    }

    $numpy = @($packages | Where-Object { $_.Name -eq "numpy" })
    if ($numpy.Count -ne 1) {
        throw "$($Target.LockName) must contain exactly one numpy pin."
    }
    if ([int]($numpy[0].Version.Split(".")[0]) -lt 2) {
        throw "$($Target.LockName) resolved numpy $($numpy[0].Version), but the wheel requires numpy>=2.2.6."
    }
}

$uvVersion = Get-UvVersion
if ($uvVersion -ne $ExpectedUvVersion) {
    throw "This workflow requires uv $ExpectedUvVersion, found uv $uvVersion."
}

$wheelHash = (Get-FileHash -LiteralPath $ArtifactPath -Algorithm SHA256).Hash.ToLowerInvariant()
$metadata = Read-WheelMetadata -Path $ArtifactPath
$metadataHash = Get-Sha256Hex -Bytes $metadata.Bytes
if ($metadata.Name -ne "confflow" -or $metadata.Version -ne "2.1.6") {
    throw "Unexpected local wheel identity: $($metadata.Name) $($metadata.Version)"
}
if ($metadata.RequiresPython -ne ">=3.10") {
    throw "Unexpected ConfFlow Requires-Python: $($metadata.RequiresPython)"
}
$numpyRequirement = @($metadata.RequiresDist | Where-Object { $_ -match "^numpy>=([0-9]+\.[0-9]+\.[0-9]+)($|\s*;)" }) | Select-Object -First 1
if ($null -eq $numpyRequirement) {
    throw "The local wheel METADATA has no unconditional numpy lower bound."
}
$numpyRequirement = $numpyRequirement -replace ";.*$", ""
$confflowVersion = $metadata.Version
if (-not (Test-Path -LiteralPath $ConstraintPath -PathType Leaf)) {
    throw "Chemistry input constraint is missing: $ConstraintRelative"
}
if (-not (Test-Path -LiteralPath $NumpyOverridePath -PathType Leaf)) {
    throw "Chemistry numpy override is missing: $NumpyOverrideRelative"
}
$constraintText = [System.IO.File]::ReadAllText($ConstraintPath).Trim()
if ($constraintText -ne "confflow==$confflowVersion") {
    throw "$ConstraintRelative does not match the local wheel version $confflowVersion."
}
$numpyOverrideText = [System.IO.File]::ReadAllText($NumpyOverridePath).Trim()
if ($numpyOverrideText -ne $numpyRequirement) {
    throw "$NumpyOverrideRelative does not match the local wheel METADATA requirement $numpyRequirement."
}

$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("jobdesk-chem-lock-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null
$previousCache = $env:UV_CACHE_DIR
$env:UV_CACHE_DIR = $CacheDirectory
try {
    foreach ($target in $Targets) {
        $relativeLock = "requirements/locks/$($target.LockName)"
        $existingPath = Join-Path $RepoRoot ($relativeLock -replace "/", "\")
        $outputPath = if ($Check) {
            Join-Path $tempRoot $target.LockName
        } else {
            $existingPath
        }
        $customCommand = "powershell -ExecutionPolicy Bypass -File scripts/compile_chem_locks.ps1"
        $uvArgs = @(
            "pip", "compile", "pyproject.toml",
            "--extra", "dev",
            "--extra", "chem",
            "--constraints", $constraintPath,
            "--overrides", $numpyOverridePath,
            "--find-links", $ArtifactDirectory,
            "--python-version", $target.PythonVersion,
            "--python-platform", "windows",
            "--generate-hashes",
            "--exclude-newer", $ExcludeNewer,
            "--custom-compile-command", $customCommand,
            "--cache-dir", $CacheDirectory,
            "--output-file", $outputPath
        )
        $logPath = Join-Path $tempRoot ("uv-" + $target.PythonVersion.Replace(".", "") + ".log")
        $previousErrorAction = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            & uv @uvArgs *> $logPath
            $uvExitCode = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $previousErrorAction
        }
        if ($uvExitCode -ne 0) {
            throw "uv pip compile failed for Python $($target.PythonVersion). See $logPath"
        }
        Write-LockHeader -Path $outputPath -Target $target -WheelSha256 $wheelHash -MetadataSha256 $metadataHash -UvVersion $uvVersion
        Assert-Lock -Path $outputPath -Target $target -WheelSha256 $wheelHash -MetadataSha256 $metadataHash -UvVersion $uvVersion -ConfflowVersion $confflowVersion

        if ($Check) {
            if (-not (Test-Path -LiteralPath $existingPath -PathType Leaf)) {
                throw "Expected checked-in chem lock is missing: $relativeLock"
            }
            $expectedBytes = [System.IO.File]::ReadAllBytes($existingPath)
            $actualBytes = [System.IO.File]::ReadAllBytes($outputPath)
            if (-not [System.Linq.Enumerable]::SequenceEqual($expectedBytes, $actualBytes)) {
                throw "$relativeLock is stale; run scripts/compile_chem_locks.ps1 to regenerate it."
            }
        }
    }

    $manifest = [ordered]@{
        schema = "jobdesk.chem-lock.v1"
        artifact = [ordered]@{
            filename = [System.IO.Path]::GetFileName($ArtifactPath)
            relative_path = $ArtifactRelative
            sha256 = $wheelHash
            metadata_sha256 = $metadataHash
            name = $metadata.Name
            version = $metadata.Version
            requires_python = $metadata.RequiresPython
            requires_dist = @($metadata.RequiresDist)
            source = "exact published ConfFlow v2.1.6 wheel copied locally; production endpoint unchanged"
        }
        resolver = [ordered]@{
            uv_version = $uvVersion
            exclude_newer = $ExcludeNewer
            platform = "windows"
            architecture = "x86_64"
            inputs = @("pyproject.toml [base,dev,chem]", "local wheel METADATA")
            numpy_override = $numpyRequirement
        }
        locks = @($Targets | ForEach-Object {
                [ordered]@{
                    python = $_.PythonVersion
                    path = "requirements/locks/$($_.LockName)"
                }
            })
    }
    $manifestText = ($manifest | ConvertTo-Json -Depth 8) + "`n"
    if ($Check) {
        if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
            throw "Expected chem wheel manifest is missing: requirements/locks/jobdesk-chem-wheel-manifest.json"
        }
        $expectedManifest = [System.IO.File]::ReadAllText($ManifestPath)
        if ($expectedManifest -ne $manifestText) {
            throw "The chem wheel manifest is stale; run scripts/compile_chem_locks.ps1 to regenerate it."
        }
    } else {
        Write-Utf8NoBom -Path $ManifestPath -Text $manifestText
    }
} finally {
    if ($null -eq $previousCache) {
        Remove-Item Env:UV_CACHE_DIR -ErrorAction SilentlyContinue
    } else {
        $env:UV_CACHE_DIR = $previousCache
    }
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
}

if ($Check) {
    Write-Host "Checked-in Windows chem locks and local-wheel manifest are current."
} else {
    Write-Host "Generated and validated Windows chem locks for Python 3.11, 3.12, and 3.13."
}
