# 2018/2020 固定研究目录复现

[完整性目录](configs/integrity_catalog_2020.json)和[重执行目录](configs/reexecution_catalog_2020.json)登记 18 组研究任务，包括统一数据集、行情与成交量导入、事件研究、文本基线、压力仿真和历史规则回放。复现需要清单引用的本机数据与原始产物；新克隆仅含源码和配置。

## 源码与字节格式

这份目录声明的 21 个执行源码文件与提交 `2aae60cda386ac772d82a3a63c7609a7ddde2ee9` 的 Git 对象逐字节匹配。后续平台扩展了 `market_data.py` 的混合复权口径声明，并修正 `stress_market.py` 的股份守恒校验。使用扩展后的源码核验此目录会有 8 组版本不匹配；需要在独立源码副本中复现登记版本，保留平台实现和原清单。

源码换行也参与 SHA-256。主分支的 [Git 属性](../.gitattributes)为三类 Agent 原型与此固定目录涉及的 103 个文件明确指定原始格式，其中 92 个为 LF、11 个为 CRLF。协议 JSON 使用其登记格式。分别在 `core.autocrlf=true` 和 `false` 下导出、签出，103 个文件均须保持相同的登记字节。

历史提交的属性规则与主分支不同，以下命令为历史源码包显式关闭自动换行转换。独立源码的 18 组完整性预检通过后才执行生产函数；原始工作簿、行情、标注、配置与参考产物继续按原清单核验。

## 独立重跑

在已恢复本机研究数据的项目根目录运行。结果目录必须不存在或为空。源码副本放在系统临时目录，避免它的输入副本参与主工作区按文件名定位来源的检查。

```powershell
$ErrorActionPreference = "Stop"
$repoRoot = (Get-Location).Path
. (Join-Path $repoRoot "scripts/platform-python.ps1")
$pythonExecutable = (Resolve-PlatformPython -RepoRoot $repoRoot -RequestedPython "").Path
$replayRoot = Join-Path ([IO.Path]::GetTempPath()) ("marketmirror-catalog-" + [guid]::NewGuid().ToString("N"))
$sourceDir = Join-Path $replayRoot "source"
$sourceTar = Join-Path $replayRoot "source.tar"
$outputDir = Join-Path $repoRoot "research_outputs/catalog_reexecution_2018_2020_example"

New-Item -ItemType Directory -Path $sourceDir -Force | Out-Null
git -c core.autocrlf=false -c core.eol=lf archive --format=tar `
  --output $sourceTar 2aae60cda386ac772d82a3a63c7609a7ddde2ee9
if ($LASTEXITCODE -ne 0) { throw "历史源码导出失败" }
& $pythonExecutable -B -m tarfile -e $sourceTar $sourceDir
if ($LASTEXITCODE -ne 0) { throw "历史源码解包失败" }

$annotationDir = Join-Path $sourceDir "research_outputs/semantic_annotation_pilot_2020"
New-Item -ItemType Directory -Path $annotationDir -Force | Out-Null
foreach ($name in @("annotation_manifest.json", "annotation_items.jsonl")) {
  Copy-Item -LiteralPath (Join-Path $repoRoot ("research_outputs/semantic_annotation_pilot_2020/" + $name)) `
    -Destination (Join-Path $annotationDir $name)
}

$configPath = Join-Path $repoRoot "research/configs/reexecution_catalog_2020.json"
try {
  Set-Location $sourceDir
  & $pythonExecutable -u -B -X utf8 -m research.registry.reexecute $configPath --output-dir $outputDir
  if ($LASTEXITCODE -ne 0) { throw "重执行存在失败或差异，请检查生成的报告" }
} finally {
  Set-Location $repoRoot
}
```

两份标注文件按原名复制，供 `keyword_baseline` 的相对来源引用定位；它们仍需匹配原始清单的哈希。其余输入从原项目配置所指向的位置只读加载。每组任务在新目录实际运行，比较结束后只保留重执行报告与凭据，原研究产物不覆盖。流程不调用模型。

## 核验范围与结果

2026-10-20 使用 Python 3.13.9 实际执行全部 18 组任务，包括重建含 429,597 条问答、3,649 家公司和 12,287 份财务快照的统一数据库。原始清单、参考产物和平台执行代码均保留。

| 核验 | 结果 |
| --- | --- |
| 匹配源码下的完整性预检 | 18/18 通过 |
| 实际重执行的严格结果 | 17/18 通过，`text_prediction` 为 `different` |
| 产物比较 | 37 份逐字节一致；2 份仅生成时间不同；1 份 SQLite 仅构建时间元数据不同；1 份 JSON 的实验编号不同 |
| 分发与签出 | 两种 Git 换行配置各检查 103 个文件，哈希差异均为 0 |
| 三类 Agent 原型归档 | 415 个绑定文件通过只读核验 |

`text_prediction` 的 `prediction_panel.csv`、`test_predictions.csv` 和 `prediction_report.md` 逐字节一致。`prediction_results.json` 唯一差异为 `experiment_id`：编号输入包含 `forecast_feature_policy.json` 的绝对路径，独立源码目录改变了该路径。逐字段核对表明其余配置、来源、模型参数、预测、指标和区间全部一致；将路径映射回原项目位置后，可重新计算出原编号。

这项路径诊断单独记录，严格重跑状态仍保留为 `different`，总通过数为 17/18。上述命令在这种独立目录条件下会返回非零状态，完整结果保存在生成的报告中。

## 读取结果

`reexecution_results.json` 记录每组预检、重新生成产物的 SHA-256 和比较结果；`reexecution_report.md` 提供可读汇总，`reexecution_manifest.json` 固定报告与执行器版本。

- `identical`：产物逐字节一致。
- `equivalent_except_generated_at`：仅顶层 JSON 的 `generated_at` 不参与比较。
- `equivalent_except_sqlite_generated_at_metadata`：完整 SQLite 结构和主键排序后的全部表行一致，仅忽略 `dataset_metadata.generated_at` 一行。
- `failed_preflight`、`failed`、`different`：保留为失败或差异，命令返回非零状态。

本流程验证固定输入和登记源码能否再生同一研究内容。原始数据口径、语义判断、投资者行为校准和真实市场效果仍按各研究报告的证据范围判断。三类 Agent 平台的界面与自由情景使用[平台验证流程](../design/README.md#验证依据)。
