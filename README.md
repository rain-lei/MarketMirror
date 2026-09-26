# MarketMirror

MarketMirror 正在重建为**可复现、可核验的金融市场冲击研究系统**。当前优先回答：监管问答或事件信息能否为市场基线提供稳定增量，以及三类示意投资者规则在相同冲击下会产生怎样的持仓与风险路径。

项目已经建立数据质量检查、按时点可见的问答数据集、公开行情导入、事件研究、文本预测对照、合成 Agent 机制实验、真实收益路径上的价格接受型回放，以及跨实验完整性和重跑核验。2020 年小样本结果属于**探索性研究**，不能解释为因果效应、可交易策略或已经校准的监管预测。

## 当前状态

| 研究环节 | 已有证据 | 尚未完成 |
|---|---|---|
| 数据层 | 三份 Excel 共 429,597 条问答；全量来源、质量报告和时点导出 | 财务单位、季度含义、标签定义与公开时刻核实 |
| 市场基线 | 2020 年事件窗口、成交活动、其他日期对照；177 条测试预测 | 更广样本、独立行情源和外部预注册验证 |
| Agent | 三类规则、合成账本、两段真实收益路径回放 | 投资者行为参数、订单流与价格冲击校准 |
| 语义抽取 | 128 条待审核样本、双人审核与裁定流程、关键词对照 | 真实双人标注、金标准、LLM 留出评估 |
| 可复现性 | 12 项本地运行完整性检查与独立重跑通过 | 原始文件跨机器获取和公开口径复核 |

详细数值、限制和阶段状态见 [研究进度](research/IMPLEMENTATION_STATUS.md)；研究设计见 [重建方案](RESEARCH_REBUILD_PLAN.md)。

## 本地只读工作台

工作台从通过本机核验的产物生成，页面仅包含汇总指标、图表和公开证据链接，不嵌入问答原文、个人路径或完整数据库。

```powershell
python -m research.workbench.build --output-dir research_outputs/workbench_2020
```

随后直接打开 `research_outputs/workbench_2020/index.html`。它是静态离线页面，无需安装前端依赖或启动后端。已有产物也可通过 `python -m http.server 8765 --bind 127.0.0.1 --directory research_outputs/workbench_2020` 在本机预览。工作台当前只读，不能据此执行新实验。

生成工作台前需具备本机原始来源和 `research_outputs/` 下的运行产物。该目录被 Git 忽略；克隆仓库本身不附带原始 Excel、问答文本或市场下载。完整构建命令见 [研究操作说明](research/README.md)。

## 核验

```powershell
python -m unittest discover -s tests -q
python -m research.registry.verify_catalog research/configs/integrity_catalog_2020.json --output-dir <新的本地目录>
python -m research.registry.reexecute research/configs/reexecution_catalog_2020.json --output-dir <新的本地目录>
```

固定清单的 12 项运行在当前机器上已独立重跑；27 份产物中 25 份逐字节一致，另两份只存在明确限定的生成时间差异。重跑不能证明原始字段的经济含义、历史信息可见时刻、统计显著性或 Agent 的现实行为拟合。

原先的 Vue/FastAPI 静态演示不采用研究数据和验证流程，已从当前分支移除；如需查看可从 Git 历史恢复。新研究系统以 `research/` 为实现入口，不把旧演示的分数或演示账号当成研究能力。
