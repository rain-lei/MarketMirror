param(
    [switch]$SkipSourceCases
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$python = (Get-Command python -ErrorAction Stop).Source
$node = (Get-Command node -ErrorAction Stop).Source

$pythonTests = @(
    "tests.test_platform_strategies",
    "tests.test_platform_analysis_binding",
    "tests.test_platform_text",
    "tests.test_platform_engine",
    "tests.test_platform_server",
    "tests.test_platform_scenario_batch",
    "tests.test_platform_batches",
    "tests.test_platform_source_cases",
    "tests.test_platform_submission",
    "tests.test_platform_model_connection",
    "tests.test_platform_draft_recovery"
)

$nodeTests = @(
    "tests/test_platform_strategy_state.cjs",
    "tests/test_platform_analysis_state.cjs",
    "tests/test_platform_decision_view.cjs",
    "tests/test_platform_copy.cjs",
    "tests/test_platform_record_loading.cjs",
    "tests/test_platform_batch_view.cjs",
    "tests/test_platform_case_view.cjs",
    "tests/test_platform_submission.cjs",
    "tests/test_platform_comparison.cjs",
    "tests/test_platform_model_view.cjs",
    "tests/test_platform_draft_cache.cjs"
)

Write-Host "[1/4] Python platform tests"
& $python -B -X utf8 -m unittest @pythonTests -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[2/4] Node state and view tests"
& $node --test @nodeTests
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[3/4] JavaScript syntax"
& $node --check design/app.js
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if ($SkipSourceCases) {
    Write-Host "[4/4] Source-case archive verification skipped by request"
} elseif (Test-Path "research_outputs/platform_workspace") {
    Write-Host "[4/4] Source-case archive verification"
    & $python -B -X utf8 -m design.source_cases --verify-all
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} else {
    Write-Host "[4/4] Source-case archive verification skipped: local archive is not present"
}

Write-Host "Platform verification passed."
