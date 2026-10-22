param(
    [string]$Python = "",
    [string]$SourceDir = "data/source/REA data",
    [string]$OutputDir = "outputs/rea_fy2025_26",
    [int]$Workers = 3
)

$ErrorActionPreference = "Stop"

if (-not $Python) {
    $candidate = Get-Command python -ErrorAction SilentlyContinue
    if ($candidate -and $candidate.Source -notlike "*WindowsApps*") {
        $Python = $candidate.Source
    } else {
        $condaCandidate = Join-Path $env:LOCALAPPDATA "anaconda3/envs/pypsa/python.exe"
        if (Test-Path -LiteralPath $condaCandidate) {
            $Python = $condaCandidate
        } else {
            throw "No usable Python was found. Pass -Python with an environment containing requirements-rea.txt."
        }
    }
}

& $Python -m scripts.rea_fy2025_26.parallel_run --source-dir $SourceDir --output-dir $OutputDir --workers $Workers
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $Python -m pytest -q tests/test_rea_fy2025_26.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $Python -m scripts.rea_fy2025_26.validate_outputs --output-dir $OutputDir
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "REA analysis and tests completed: $OutputDir"
