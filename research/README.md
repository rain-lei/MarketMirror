# 研究操作与复现

研究目录提供来源处理、文本事实复核、Agent 机制、对照实验和归档重放。可视化平台的安装和操作见[首页](../README.md)与[平台说明](../design/README.md)。

所有命令在项目根目录运行，使用已安装 `requirements-research.txt` 的 Python。原始工作簿、完整问答、模型响应和市场账本保存在 Git 忽略的 `research_outputs/`；仓库包含实现、配置、合同和报告。缺少对应输入时不能仅凭配置取得实验结果。

## 数据构建

设置三份工作簿所在目录，先检查结构和质量：

```powershell
$sourceDir = "D:\path\to\workbooks"
$sources = @(
  (Join-Path $sourceDir "金融数据示例.xlsx"),
  (Join-Path $sourceDir "irm_szse_all_recent3m.xlsx"),
  (Join-Path $sourceDir "用户与公司问答(高频,按季度拆分)-2020.xlsx")
)
python -m research.data_pipeline.profile_sources --output-dir research_outputs @sources
```

提取待核对财务快照，再构建统一数据库：

```powershell
python -m research.data_pipeline.extract_financial `
  (Join-Path $sourceDir "用户与公司问答(高频,按季度拆分)-2020.xlsx") `
  --output-dir research_outputs/financial_2020
python -m research.data_pipeline dataset @sources `
  --financial-db research_outputs/financial_2020/financial_unverified.sqlite `
  --output-dir research_outputs/unified
```

问答来源 ID 由文件内容哈希、工作表和行号组成。不同来源的相同行号不会冲突；内容重复只作审计，不自动合并。来源、别名、原始值和质量状态均保留，构建完成后核对计数、外键和数据库完整性。

财务快照的单位、变换、财报期间和披露时间未全面核实，保持 `unverified`，不直接进入预测特征。来源“季度”只作分桶提示，不当作财报季度。字段和关系见[数据合同](data_contracts/README.md)。

## 截止时点特征

```powershell
python -m research.data_pipeline features research_outputs/unified/dataset.sqlite `
  --window-start "2020-01-01T00:00:00+08:00" `
  --as-of "2020-01-23T23:59:59+08:00" `
  --output research_outputs/unified/qa_asof_2020-01-23.csv
```

可用 `--stock-code` 限定公司。出口仅使用截止时刻内的提问和已可见回复；日期精度回复从次日零点起视为可用。标签、财务值、用户字段、最终回复状态不进入白名单特征。输出 CSV 和来源/规则/代码哈希清单；公开时间仍为来源时间代理。实测范围见[特征隔离审计](ASOF_FEATURE_ISOLATION_AUDIT_2026.md)。

## 行情与事件计算

标准行情需要明确股票、基准、交易日历、收益单位和复权口径，收益统一为小数。缺失日、重复日及不完整窗口单列，不补价格或用零替代失败。事件保留名义日期、来源公开时点与实际交易日对齐。

```powershell
python -m research.data_pipeline market research_outputs/observed_2020/download/market_import.json `
  --output-dir research_outputs/observed_2020/prepared
python -m research.baselines.run_experiments research/configs/observed_pilot_2020.json `
  --output-dir research_outputs/observed_2020/results
```

运行前准备配置声明的行情及证据文件，重算使用新的输出目录。事件研究、日期对照、成交和文本预测是不同问题，各自按固定配置执行。真实行情口径核对见[第二来源报告](INDEPENDENT_QUOTES_2018_2020.md)，历史时间边界见[信息对齐说明](EVENT_VISIBILITY_ALIGNMENT_2018_2020.md)。

## 机制实验与来源归档

不依赖模型密钥的有限合成压力实验：

```powershell
python -m research.simulation.stress_market research/configs/synthetic_stress_v1.json `
  --output-dir research_outputs/synthetic_stress_example
```

输出有文本和无文本两组的逐步账本及配置哈希。合成信号、流动性、角色参数和冲击均为显式假设，运行成功不构成投资者校准或市场预测证据。

有本地六来源归档时，可只读核验并检查三类决策：

```powershell
python -B -X utf8 -m research.prototype verify
python -B -X utf8 -m research.prototype case --case wuhan --kind fixed
python -B -X utf8 -m research.prototype case --case wuhan --kind path
python -B -X utf8 -m research.prototype risk --case high_risk
```

`fixed` 为共同状态下未执行的请求，`path` 为完整路径的实际请求与成交；两者不混用。全部归档重算、五种子及历史输入版本见[复现说明](PROTOTYPE_REPRODUCTION_2026.md)。真实收益单案例的条件和命令见[实验记录](REAL_OBSERVED_EXPERIMENT_20261006.md)。

## AI 复核与语义接入

按 [assistant_review_v1](configs/assistant_review_policy.json) 逐条核对可见原文、事件和精确证据，每条写判断理由，空事件也须复核。参考标签由单一 AI 形成，记录其非独立状态。复核、评分和信号接入命令见[复核结果](AI_REVIEW_H2_2020.md)，判断合同见[标注说明](semantic/ANNOTATION_GUIDE.md)。

事实格式与引文校验不代表语义准确率。评分称为与 AI 参考的一致性；达到固定门槛才允许相应协议的自动信号。不同抽取版本、不同样本和复核后开发案例分别报告，原武汉 v6 类别门槛未通过。不得将已查看样本用于改提示后再次称为留出验证。

## 归档研究摘要

`workbench/` 为固定清单的核验摘要与受控重执行工具，使用原研究数据和归档；平台自由情景入口在 `design/`。

```powershell
python -m research.workbench.build --output-dir research_outputs/workbench_example
python -m research.registry.verify_catalog research/configs/integrity_catalog_2020.json `
  --output-dir research_outputs/integrity_example
python -m research.registry.reexecute research/configs/reexecution_catalog_2020.json `
  --output-dir research_outputs/reexecution_example
```

使用新的目录，保留原产物。摘要构建要求执行源码与清单登记的版本一致，任一哈希不符即停止。清单核验只验证所列输入、代码和输出，重执行比较明确记录逐字节一致或限定的时间戳差异，不自动证明研究结论。

2018/2020 固定目录的执行源码版本、输入准备和独立重跑命令见[固定目录复现](CATALOG_REPRODUCTION_2018_2020.md)。Git 属性保留已登记输入的原始换行格式；使用匹配的源码与数据进行哈希核验。

## 验证

平台验证使用首页的基础依赖。全套研究测试还涉及 NumPy 数值复核和 PDF/披露读取，使用 Python 3.11+ 并安装仓库已有的研究依赖：

```powershell
python -m pip install -r requirements-policy-rates.txt -r requirements-issuer-disclosures.txt
```

```powershell
.\scripts\verify-platform.ps1
python -B -X utf8 -m unittest discover -s tests -q
```

平台脚本在有归档时核验 120 条原文市场路径和历史行情实验的 270 次决策；新克隆缺少归档时明确跳过。研究全套测试覆盖数据时点、源记录、引用、评分、参数、市场执行与独立审计。运行证据有日期与适用范围，单元测试不代替浏览器布局验收或真实市场效果评估。

## 研究索引

| 问题 | 文档 |
| --- | --- |
| 架构与实现 | [系统边界](../RESEARCH_REBUILD_PLAN.md)、[实现入口](IMPLEMENTATION_STATUS.md) |
| 六来源机制链路与验收 | [研究总结](RESEARCH_SUMMARY_2026.md)、[原文链路](SOURCE_TEXT_TO_AGENT_DECISIONS_2026.md)、[逐项证据](PROTOTYPE_ACCEPTANCE_2026.md) |
| 同状态、时长和风险 | [敏感性](MECHANISM_SENSITIVITY_RESULTS_2026.md)、[时长](MECHANISM_SENSITIVITY_RESULTS_2026.md)、[风险](PROTOTYPE_RISK_DIAGNOSTICS_2026.md) |
| 模型参考与失败门槛 | [下半年 AI 复核](AI_REVIEW_H2_2020.md)、[武汉 v6 评估](WUHAN_INDEPENDENT_EVALUATION_V6.md) |
| 真实历史收益 | [公司回复实验](REAL_OBSERVED_EXPERIMENT_20261006.md) |
| 资金与资源条件 | [资金诊断](SEMANTIC_WALLET_ALLOCATION_DIAGNOSTIC.md)、[资源全量结果](TEMPORAL_RESOURCE_UNIT_RESULTS_2022.md) |
| 多资产与公开因素 | [组合机制](SEMANTIC_PORTFOLIO_H2_2020.md)、[外部因素](NON_QA_FACTOR_RESEARCH_2026.md) |

各报告保留预定条件、零效应、负结果、失败和来源缺口。实验规模不等同于独立市场样本量；真实投资者参数、严格历史版本、预测效果与监管应用仍须独立证据。
