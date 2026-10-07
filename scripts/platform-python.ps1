function Resolve-PlatformPython {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [string]$RequestedPython = ""
    )

    $candidates = [System.Collections.Generic.List[string]]::new()
    if ($RequestedPython) {
        if (Test-Path -LiteralPath $RequestedPython -PathType Leaf) {
            $candidates.Add((Resolve-Path -LiteralPath $RequestedPython).ProviderPath)
        } else {
            $command = Get-Command $RequestedPython -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
            if (-not $command) { throw "找不到指定的 Python：$RequestedPython。请使用可执行文件的完整路径。" }
            $candidates.Add($command.Source)
        }
    } else {
        $candidates.Add((Join-Path $RepoRoot ".venv/Scripts/python.exe"))
        foreach ($environmentRoot in @($env:VIRTUAL_ENV, $env:CONDA_PREFIX)) {
            if ($environmentRoot) { $candidates.Add((Join-Path $environmentRoot "python.exe")) }
            if ($environmentRoot) { $candidates.Add((Join-Path $environmentRoot "Scripts/python.exe")) }
        }
        foreach ($command in @(Get-Command python -CommandType Application -All -ErrorAction SilentlyContinue)) {
            $candidates.Add($command.Source)
        }
        foreach ($command in @(Get-Command conda.bat, conda.exe -CommandType Application -All -ErrorAction SilentlyContinue)) {
            $directory = Split-Path -Parent $command.Source
            if ((Split-Path -Leaf $directory) -in @("condabin", "Scripts")) {
                $directory = Split-Path -Parent $directory
            }
            $candidates.Add((Join-Path $directory "python.exe"))
        }
    }

    $probe = @'
import json, re, sys
result = {'executable': sys.executable, 'python_version': sys.version.split()[0]}
try:
    if sys.version_info < (3, 10):
        raise RuntimeError('Python 3.10 or newer is required')
    import openpyxl
    result['openpyxl_version'] = openpyxl.__version__
    match = re.match(r'^(\d+)\.(\d+)\.(\d+)$', openpyxl.__version__)
    if not match or not ((3, 1, 0) <= tuple(map(int, match.groups())) < (4, 0, 0)):
        raise RuntimeError('openpyxl >=3.1.0,<4.0.0 is required')
    result['ok'] = True
except Exception as error:
    result.update(ok=False, error=str(error))
print(json.dumps(result))
'@

    $failures = [System.Collections.Generic.List[string]]::new()
    $seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($candidate in $candidates) {
        if (-not $seen.Add($candidate) -or -not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        try {
            $probeOutput = & $candidate -B -X utf8 -c $probe 2>&1
            if ($LASTEXITCODE -ne 0) { throw "解释器检查失败（退出码 $LASTEXITCODE）。" }
            $details = ($probeOutput -join "`n") | ConvertFrom-Json
            if (-not $details.ok) { throw $details.error }
            Write-Host "Python $($details.python_version): $($details.executable) · openpyxl $($details.openpyxl_version)"
            return [pscustomobject]@{
                Path = [string]$details.executable
                Version = [string]$details.python_version
                OpenpyxlVersion = [string]$details.openpyxl_version
            }
        } catch {
            $reason = "$candidate : $($_.Exception.Message)"
            $failures.Add($reason)
            if (-not $RequestedPython) { Write-Host "跳过不可用环境：$reason" -ForegroundColor Yellow }
        }
    }

    $summary = if ($failures.Count) { $failures -join "`n" } else { "未找到可执行的 Python。" }
    throw "没有满足平台依赖的 Python 环境。需要 Python 3.10+ 和 requirements-research.txt 中的依赖。`n$summary`n请用目标 Python 执行 -m pip install -r requirements-research.txt，然后通过 -Python 指定同一解释器。"
}
