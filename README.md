# MarketMirror

MarketMirror 正在重建为**可复现、可核验的金融市场冲击研究系统**。当前优先回答：监管问答或事件信息能否为市场基线提供稳定增量，以及三类示意投资者规则在相同冲击下会产生怎样的持仓与风险路径。

项目已经建立数据质量检查、按时点可见的问答数据集、公开行情导入、事件研究、文本预测对照、合成 Agent 机制实验、真实收益路径上的价格接受型回放，以及跨实验完整性和重跑核验。2018 资管新规和 2020 武汉通告的小样本结果属于**探索性研究**，不能解释为因果效应、可交易策略或已经校准的监管预测。

## 当前状态

| 研究环节 | 已有证据 | 尚未完成 |
|---|---|---|
| 数据层 | 三份 Excel 共 429,597 条问答；全量来源、质量报告和时点导出 | 财务单位、季度含义、标签定义与公开时刻核实 |
| 市场基线 | 2018/2020 事件窗口、成交活动和日期对照；2020 年 177 条测试预测 | 更广样本、独立行情源和外部预注册验证；新增 2018 诊断待纳入主目录与工作台 |
| Agent | 三类规则、合成账本、三段真实收益路径回放 | 投资者行为参数、订单流与价格冲击校准 |
| 语义抽取 | DeepSeek v2 的 128 条真实响应中 127 条结构/证据通过、1 条引用歧义；v1 重验为 94 条通过；双人审核流程、独立离线审核页面与关键词对照 | 真实双人标注、金标准和新的留出样本评估 |
| 可复现性 | 18 项本地运行完整性检查与独立重跑通过 | 原始文件跨机器获取和公开口径复核 |

详细数值、限制和阶段状态见 [研究进度](research/IMPLEMENTATION_STATUS.md)；语义协议开发结果见 [v2 实验说明](research/SEMANTIC_PROTOCOL_V2.md)；新增历史节点见 [2018 实验说明](research/OBSERVED_PILOT_2018.md)；研究设计见 [重建方案](RESEARCH_REBUILD_PLAN.md)。

## 本地研究工作台

工作台从通过本机核验的产物生成，页面仅包含汇总指标、图表和公开证据链接，不嵌入问答原文、个人路径或完整数据库。

```powershell
python -m research.workbench.build --output-dir research_outputs/workbench_2018_2020_llm_v3
```

直接打开生成的 `index.html` 可离线查看摘要。要从页面选择固定实验重新执行，在仓库根目录启动仅监听本机的服务，再打开 `http://127.0.0.1:8766/`：

```powershell
python -m research.workbench.serve --site-dir research_outputs/workbench_2018_2020_llm_v3
```

页面一次只能重跑固定清单中的一项；页面会显示并校验该运行的固定数据版本和执行版本，每次保存所选配置、版本、运行状态、产物比较和哈希。页面还会展示带来源哈希的财务字段口径字典，把可观察结构和未核实经济含义分开；如果存在 DeepSeek 运行目录，还会显示模型运行完整性、解析状态和评分门槛。页面顶部的“下载摘要报告”链接与图表使用同一份白名单汇总生成 `report.md`，可随运行目录归档。也可运行 `python -m research.workbench.run observed_event`。页面上的事件、股票筛选只改变摘要展示，不会修改实验参数；自定义数据或模型版本仍未接入。生成目录须为新空目录，已有页面可直接使用。

生成工作台前需具备本机原始来源和 `research_outputs/` 下的运行产物。该目录被 Git 忽略；克隆仓库本身不附带原始 Excel、问答文本或市场下载。完整构建命令见 [研究操作说明](research/README.md)。

## 核验

```powershell
python -m unittest discover -s tests -q
python -m research.registry.verify_catalog research/configs/integrity_catalog_2020.json --output-dir <新的本地目录>
python -m research.registry.reexecute research/configs/reexecution_catalog_2020.json --output-dir <新的本地目录>
```

固定清单的 18 项运行在当前机器上已独立重跑；41 份产物中 38 份逐字节一致，另三份只存在明确限定的生成时间差异。重跑不能证明原始字段的经济含义、历史信息可见时刻、统计显著性或 Agent 的现实行为拟合。

原先的 Vue/FastAPI 静态演示不采用研究数据和验证流程，已从当前分支移除；如需查看可从 Git 历史恢复。新研究系统以 `research/` 为实现入口，不把旧演示的分数或演示账号当成研究能力。
