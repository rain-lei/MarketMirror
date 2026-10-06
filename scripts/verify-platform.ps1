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
    "tests.test_platform_strategy_preview",
    "tests.test_platform_analysis_binding",
    "tests.test_platform_text",
    "tests.test_platform_engine",
    "tests.test_platform_experiment_scenario",
    "tests.test_platform_server",
    "tests.test_platform_scenario_batch",
    "tests.test_platform_batches",
    "tests.test_platform_source_cases",
    "tests.test_platform_observed_experiments",
    "tests.test_platform_submission",
    "tests.test_platform_model_connection",
    "tests.test_platform_draft_recovery"
)

$nodeTests = @(
    "tests/test_platform_strategy_state.cjs",
    "tests/test_platform_strategy_preview.cjs",
    "tests/test_platform_experiment_scenario.cjs",
    "tests/test_platform_parameter_controls.cjs",
    "tests/test_platform_analysis_state.cjs",
    "tests/test_platform_decision_view.cjs",
    "tests/test_platform_copy.cjs",
    "tests/test_platform_record_loading.cjs",
    "tests/test_platform_batch_view.cjs",
    "tests/test_platform_case_view.cjs",
    "tests/test_platform_observed_view.cjs",
    "tests/test_platform_replay_control.cjs",
    "tests/test_platform_submission.cjs",
    "tests/test_platform_comparison.cjs",
    "tests/test_platform_model_view.cjs",
    "tests/test_platform_draft_cache.cjs"
)

Write-Host "[1/5] Python platform tests"
& $python -B -X utf8 -m unittest @pythonTests -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[2/5] Node state and view tests"
& $node --test @nodeTests
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[3/5] JavaScript syntax"
& $node --check design/app.js
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $node --check design/observed-view.js
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $node --check design/decision-view.js
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $node --check design/strategy-state.js
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $node --check design/strategy-preview.js
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $node --check design/experiment-scenario.js
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if ($SkipSourceCases) {
    Write-Host "[4/5] Source-case archive verification skipped by request"
} else {
    $registryPath = Join-Path $repoRoot "design/source-case-registry.json"
    $archiveAvailable = $false
    if (Test-Path $registryPath) {
        $registry = Get-Content -Raw -Encoding UTF8 $registryPath | ConvertFrom-Json
        if ($registry.archive_directory) {
            $archivePath = Join-Path $repoRoot ([string]$registry.archive_directory)
            $archiveAvailable = Test-Path $archivePath
        }
    }
    if ($archiveAvailable) {
        Write-Host "[4/5] Source-case archive verification"
        & $python -B -X utf8 -m design.source_cases --verify-all
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } else {
        Write-Host "[4/5] Source-case archive verification skipped: local archive is not present"
    }
}

$observedRegistryPath = Join-Path $repoRoot "design/observed-experiment-registry.json"
$observedArchiveAvailable = $false
if (Test-Path $observedRegistryPath) {
    $observedRegistry = Get-Content -Raw -Encoding UTF8 $observedRegistryPath | ConvertFrom-Json
    foreach ($observedEntry in $observedRegistry.experiments) {
        $observedManifestPath = Join-Path (Join-Path $repoRoot ([string]$observedEntry.run_directory)) "result_manifest.json"
        if (Test-Path $observedManifestPath) { $observedArchiveAvailable = $true }
    }
}
if ($observedArchiveAvailable) {
    Write-Host "[5/5] Observed-return archive and source receipt verification"
    & $python -B -X utf8 -m design.observed_experiments --verify-all
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} else {
    Write-Host "[5/5] Observed-return archive verification skipped: local archive is not present"
}

Write-Host "Platform verification passed."
