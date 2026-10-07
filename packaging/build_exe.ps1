# Build the editor into double-clickable Windows executables.
#
#     powershell -ExecutionPolicy Bypass -File packaging\build_exe.ps1
#
# Prereq:  pip install -r requirements.txt pyinstaller
# Output:  dist\StoryGraph\StoryGraph.exe      (GUI, double-click to run)
#          dist\StoryGraph\StoryGraph-CLI.exe  (console, for CI / batch)
#
# ---------------------------------------------------------------------------
# NOTE: this file is deliberately **pure ASCII** (no Chinese characters).
# Windows PowerShell 5.1 reads a BOM-less .ps1 as the system ANSI codepage
# (GBK on a Simplified-Chinese machine). Non-ASCII *comments* then get
# mis-decoded, and a multi-byte character can swallow the following byte --
# which broke the script with "Unexpected token" / "missing terminator".
# The Chinese-language notes live in packaging/DELIVERY-NOTES (see .md file).
# ---------------------------------------------------------------------------
#
# Why a script and not just "remember one PyInstaller command": packaging has
# an ORDER. You must run the tests first, then package, then run the packaged
# exe's own self-check. Skip any step and you can ship something that "looks
# built" but dies on first use -- measured on 2026-10-05: the frozen
# StoryGraph-CLI.exe crashed on `--help` because of GBK console encoding.

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host '=== [1/4] Full test suite ===' -ForegroundColor Cyan
python -m pytest -q
if ($LASTEXITCODE -ne 0) {
    throw "Tests failed (exit $LASTEXITCODE). Refusing to package."
}

Write-Host '=== [2/4] PyInstaller ===' -ForegroundColor Cyan
python -m PyInstaller packaging/storygraph.spec --noconfirm --log-level WARN
if ($LASTEXITCODE -ne 0) { throw "Packaging failed (exit $LASTEXITCODE)" }

$gui = Join-Path $root 'dist\StoryGraph\StoryGraph.exe'
$cli = Join-Path $root 'dist\StoryGraph\StoryGraph-CLI.exe'
foreach ($f in @($gui, $cli)) {
    if (-not (Test-Path $f)) { throw "Missing artifact: $f" }
}

Write-Host '=== [3/4] Self-check with the packaged exe ===' -ForegroundColor Cyan
# IMPORTANT: this runs the PACKAGED exe through the real pipeline (including
# export) -- that is what catches "a module or Qt plugin never made it into
# the bundle". Re-running pytest here would prove nothing.
& $cli selfcheck
if ($LASTEXITCODE -ne 0) {
    throw "Packaged self-check failed (exit $LASTEXITCODE). Do not ship this build."
}

Write-Host '=== [4/4] Size ===' -ForegroundColor Cyan
$sum = (Get-ChildItem (Join-Path $root 'dist') -Recurse -File |
    Measure-Object Length -Sum).Sum
$size = [math]::Round($sum / 1MB, 1)
Write-Host "dist\StoryGraph\ = $size MB"

Write-Host ''
Write-Host 'Build finished.' -ForegroundColor Green
Write-Host "  GUI      : $gui"
Write-Host "  Console  : $cli"
Write-Host 'Ship the WHOLE dist\StoryGraph\ folder (the exes need _internal\).'
