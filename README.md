# MarketMirror

当前复核流程已按用户要求改为助手逐条复核，不再等待双人审核或第三人裁定。结果与复现命令见 [AI 复核记录](research/AI_REVIEW_H2_2020.md)；语义已接入 126 公司回放，最新完成 [多资产组合机制实验](research/SEMANTIC_PORTFOLIO_H2_2020.md)。

MarketMirror 正在重建为**可复现、可核验的金融市场冲击研究系统**。当前优先回答：监管问答或事件信息能否为市场基线提供稳定增量，以及三类示意投资者规则在相同冲击下会产生怎样的持仓与风险路径。

项目已经建立数据质量检查、按时点可见的问答数据集、公开行情导入、事件研究、文本预测对照、合成 Agent 机制实验、真实收益路径上的价格接受型回放，以及跨实验完整性和重跑核验。2018 资管新规和 2020 武汉通告的小样本结果属于**探索性研究**，不能解释为因果效应、可交易策略或已经校准的监管预测。

## 当前状态

| 研究环节 | 已有证据 | 尚未完成 |
|---|---|---|
| 数据层 | 三份 Excel 共 429,597 条问答；全量来源、质量报告和时点导出 | 财务单位、季度含义、标签定义与公开时刻核实 |
| 市场基线 | 2018/2020 事件窗口、成交活动和日期对照；2020 年 177 条测试预测及问答可见时间延迟敏感性；第二行情源对固定窗口的收益口径核对 | 更广样本、同定义复权行情、真实公开时刻和外部预注册验证 |
| Agent | 三类规则、126 公司语义回放、记忆/延迟敏感性、有限集合竞价、自身价格反馈、有限背景需求；新增 42 个三资产篮子、672 条组合路径及现金分配固定订单诊断，独立重跑和审计通过 | 常规资金路径未触发截断，98/1/1 极端配置的一日快照已触发；需推进多日内生资金约束并校准行为、流动性、持仓与价格形成 |
| 语义抽取 | 下半年 128 条完成 AI 逐条复核，54 个参考事件；模型检出 F1 0.891、关键词 0.510、类别宏 F1 0.659 | 分数为单一 AI 参考一致性；真实文本增益与独立泛化效果仍待验证 |
| 可复现性 | 主目录 18 项与容量相关 6 项本地完整性检查、独立重跑通过 | 原始文件跨机器获取和公开口径复核 |

详细数值、限制和阶段状态见 [研究进度](research/IMPLEMENTATION_STATUS.md)；文本基线的时间假设检查见[可见时间延迟敏感性](research/VISIBILITY_LAG_2020.md)。语义协议开发结果见 [v2 实验说明](research/SEMANTIC_PROTOCOL_V2.md)，独立测试约定、首次结果与盲审交接分别见[下半年留出协议](research/SEMANTIC_HOLDOUT_H2_2020.md)、[模型运行报告](research/SEMANTIC_HOLDOUT_MODEL_H2_2020.md)、[历史盲审交接](research/SEMANTIC_REVIEW_HANDOFF_H2_2020.md)；新增历史节点见 [2018 实验说明](research/OBSERVED_PILOT_2018.md)，回放规模限制与重算结果见 [容量诊断](research/CAPACITY_DIAGNOSTIC.md)和[容量约束回放](research/PARTICIPATION_REPLAY.md)，历史路径与假设价格冲击的桥接实验见 [反事实敏感性](research/OBSERVED_COUNTERFACTUAL.md)和[滞后成交额敏感性](research/LAGGED_IMPACT.md)；研究设计见 [重建方案](RESEARCH_REBUILD_PLAN.md)。

新增 [多资产共享资金与机构组合实验](research/SEMANTIC_PORTFOLIO_H2_2020.md)：全部 126 公司按固定规则分成 42 篮子，4 个情景、两档响应和文本消融共 672 条路径、83,328 条组合日账本。两次运行的四份产物逐字节一致，全量独立审计通过。当前资金规模下分钱包与共享钱包结果相同，20% 单资产目标上限下实际持仓仍有超限。

随后完成 [现金可转移性诊断](research/SEMANTIC_WALLET_ALLOCATION_DIAGNOSTIC.md)：冻结源订单，在 672 个预设快照上比较共享钱包、等额分账户和轮换的 80/10/10、98/1/1 现金分配。80/10/10 没有触发截断；98/1/1 一日回放中出现截断和改价。两次重放逐字节一致；这是单日机制压力检查，不代表长期效应。当前继续完善仿真实验，工作台可视化暂不在本阶段范围内。

## 本地研究工作台

新增 [有限背景交易需求](research/SEMANTIC_BACKGROUND_H2_2020.md)：126 家公司、8 个固定情景、两档策略报价响应，4,032 条路径及 499,968 条完整日账本。背景现金和股票有限，每个预算都有同资源闲置对照；成交分为策略之间、策略与背景、背景之间。当前 197 项测试通过。5 基点背景无文本成交全发生在背景之间；25 基点才出现策略成交，参数仍未校准。

已完成 [模拟价格反馈与主体差异](research/SEMANTIC_FEEDBACK_H2_2020.md)：126 家公司、7 个固定情景、两档报价响应，3,528 条路径及 437,472 条完整日账本。374,976 条内生观测从各自过去价格复算；其零背景内生无文本对照全部没有成交。

已完成 [有限双边集合竞价](research/SEMANTIC_AUCTION_H2_2020.md)：126 公司各 12 个交易主体，756 条市场路径、93,744 条完整日账本，价格由实际成交形成。全部账本独立重建复核，四份产物重跑一致。模型参数尚未校准。

Agent 区域另展示 [文本记忆与公开延迟敏感性](research/SEMANTIC_MEMORY_H2_2020.md) 的全部 20 种设置；每项均值覆盖 126 家公司，全部无文本逐日对照相同。

第二行情源的日收益差异、九组 CAR 重算和收益口径限制见 [2018/2020 行情核对](research/INDEPENDENT_QUOTES_2018_2020.md)；下半年样本的当前 AI 复核、评分与信号状态见 [AI 复核报告](research/AI_REVIEW_H2_2020.md)。

工作台从通过本机核验的产物生成，页面仅包含汇总指标、图表和公开证据链接，不嵌入问答原文、个人路径或完整数据库。历史事件区展示不同收益口径的第二行情源 CAR 敏感性；文本预测区展示来源可见时间额外延迟 0/1/3/7 日的对照。Agent 区域新增 126 公司真实收益语义对照，显示 15 家方向信号、29 家仅不确定性和 82 家无文本作用；结果、摘要和报告已独立重跑一致。该区域还展示经过六项独立重跑核验的资金规模与容量情景摘要、两项固定流动性冲击情景，以及两项滞后成交额容量/冲击敏感性实验；1%/5% 比例、事件强度和冲击系数都未经校准。

```powershell
python -m research.workbench.build --output-dir research_outputs/workbench_2018_2020_background_v22
```

直接打开生成的 `index.html` 可离线查看摘要。要从页面选择固定实验重新执行，在仓库根目录启动仅监听本机的服务，再打开 `http://127.0.0.1:8766/`：

```powershell
python -m research.workbench.serve --site-dir research_outputs/workbench_2018_2020_background_v22
```

页面一次只能重跑固定清单中的一项；页面会显示并校验该运行的固定数据版本和执行版本，每次保存所选配置、版本、运行状态、产物比较和哈希。页面还会展示带来源哈希的财务字段口径字典，把可观察结构和未核实经济含义分开；开发样本和下半年留出模型的运行状态分开显示，留出 128/128 格式通过不代表语义准确率。页面顶部的“下载摘要报告”链接与图表使用同一份白名单汇总生成 `report.md`，可随运行目录归档。也可运行 `python -m research.workbench.run observed_event`。页面上的事件、股票筛选只改变摘要展示，不会修改实验参数；自定义数据或模型版本仍未接入。生成目录须为新空目录，已有页面可直接使用。

生成工作台前需具备本机原始来源和 `research_outputs/` 下的运行产物。该目录被 Git 忽略；克隆仓库本身不附带原始 Excel、问答文本或市场下载。完整构建命令见 [研究操作说明](research/README.md)。

## 核验

```powershell
python -m unittest discover -s tests -q
python -m research.registry.verify_catalog research/configs/integrity_catalog_2020.json --output-dir <新的本地目录>
python -m research.registry.reexecute research/configs/reexecution_catalog_2020.json --output-dir <新的本地目录>
python -m research.registry.reexecute research/configs/reexecution_catalog_capacity_series.json --output-dir <另一个新的本地目录>
python -m research.registry.verify_catalog research/configs/integrity_catalog_observed_counterfactual.json --output-dir <再一个新的本地目录>
python -m research.registry.verify_catalog research/configs/integrity_catalog_lagged_impact.json --output-dir <另一新的本地目录>
python -m research.registry.verify_catalog research/configs/integrity_catalog_visibility_lag_2020.json --output-dir <另一新的本地目录>
python -m research.registry.verify_catalog research/configs/integrity_catalog_independent_quotes.json --output-dir <新的本地目录>
python -m research.registry.verify_catalog research/configs/integrity_catalog_semantic_holdout_model_h2_2020.json --output-dir <另一新的本地目录>
```

主目录固定清单的 18 项运行在当前机器上已独立重跑；41 份产物中 38 份逐字节一致，两个事件 JSON 仅生成时间不同，统一 SQLite 的逻辑内容一致但物理字节及构建时间元数据不同。容量相关的另 6 项运行也已独立重跑，12 份产物逐字节一致。两项历史收益加假设冲击情景另经完整性核验，并在新目录重跑，四份结果/报告逐字节一致。重跑不能证明原始字段的经济含义、历史信息可见时刻、统计显著性或 Agent 的现实行为拟合。

原先的 Vue/FastAPI 静态演示不采用研究数据和验证流程，已从当前分支移除；如需查看可从 Git 历史恢复。新研究系统以 `research/` 为实现入口，不把旧演示的分数或演示账号当成研究能力。
