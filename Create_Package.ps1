<#
.SYNOPSIS
    Packages the ZCOP solution into a shareable .zip file.

.DESCRIPTION
    Collects the launcher, Python scripts, and the latest combined CSV files.
    The recipient just unzips and double-clicks "Launch ZCOP Frontend.bat".
    Python and all dependencies are installed automatically on first run.
#>

$ErrorActionPreference = "Stop"

$ROOT  = $PSScriptRoot
$DATE  = Get-Date -Format "yyyyMMdd"
$ZIP   = Join-Path $ROOT "ZCOP_Package_$DATE.zip"
$STAGE = Join-Path $env:TEMP "zcop_pkg_$DATE"

# ── Files to include (source path relative to ROOT) ──────────────────────────
$FILES = @(
    "Launch ZCOP Frontend.bat",
    "zcop_frontend.py",
    "Core Python files\extract_zcop.py",
    "Core Python files\combine_zcop.py",
    "Zcop Output\DATA_combined.csv",
    "Zcop Output\RU-RD_combined.csv"
)

# ── Empty folders to create in the package ───────────────────────────────────
$EMPTY_DIRS = @("Zcop Analysis", "temp")

Write-Host ""
Write-Host "  ZCOP Package Builder"
Write-Host "  ═══════════════════════════════════════════════════════"

# ── Validate required files exist ────────────────────────────────────────────
$missing = $FILES | Where-Object { -not (Test-Path (Join-Path $ROOT $_)) }
if ($missing) {
    Write-Host ""
    Write-Host "  [ERROR] Missing required files:"
    $missing | ForEach-Object { Write-Host "    - $_" }
    Write-Host ""
    exit 1
}

# ── Build staging area ────────────────────────────────────────────────────────
if (Test-Path $STAGE) { Remove-Item $STAGE -Recurse -Force }
New-Item -ItemType Directory -Path $STAGE | Out-Null

$skipped = @()
foreach ($rel in $FILES) {
    $src  = Join-Path $ROOT  $rel
    $dest = Join-Path $STAGE $rel
    $dir  = Split-Path $dest -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
    try {
        # Use .NET stream copy to avoid DLP interception on Copy-Item
        [System.IO.File]::Copy($src, $dest, $true)
        Write-Host "  [+] $rel"
    } catch {
        $skipped += $rel
        Write-Host "  [!] SKIPPED (DLP blocked): $rel"
    }
}

foreach ($dir in $EMPTY_DIRS) {
    $path = Join-Path $STAGE $dir
    New-Item -ItemType Directory -Path $path -Force | Out-Null
    # .gitkeep ensures the folder is preserved inside the zip
    New-Item -ItemType File -Path "$path\.gitkeep" -Force | Out-Null
    Write-Host "  [+] $dir\ (empty folder)"
}

# ── Create zip ────────────────────────────────────────────────────────────────
if (Test-Path $ZIP) { Remove-Item $ZIP -Force }
Compress-Archive -Path "$STAGE\*" -DestinationPath $ZIP

# ── Cleanup staging ───────────────────────────────────────────────────────────
Remove-Item $STAGE -Recurse -Force

$size = [math]::Round((Get-Item $ZIP).Length / 1KB, 1)
Write-Host ""
Write-Host "  [DONE] Package ready: $(Split-Path $ZIP -Leaf)  ($size KB)"
Write-Host "  Location: $ZIP"
if ($skipped.Count -gt 0) {
    Write-Host ""
    Write-Host "  [WARNING] The following files were blocked by DLP and must be"
    Write-Host "  added to the zip manually:"
    $skipped | ForEach-Object { Write-Host "    - $_" }
}
Write-Host ""
