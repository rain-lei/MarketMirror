# MarketMirror

### 三类 Agent · 市场情景仿真 · 可追溯决策

MarketMirror 是一个本地运行的可视化金融仿真实验平台。输入一段政策、公告或公司问答，设置情景变量，比较**激进型、保守型、机构型**三类 Agent 如何调整仓位、提交订单和形成成交。

项目关注可解释、可复现的行为差异。当前 Agent 使用预设决策规则；DeepSeek 辅助提取原文事实，情景参数由用户设置。平台并非真实市场预测系统。

[快速启动](#快速启动) · [平台使用说明](design/README.md) · [研究与复现](research/README.md) · [历史进度](research/README_HISTORY_20261005.md)

## 可以做什么

| 功能 | 当前实现 |
| --- | --- |
| 实验空间 | 新建、搜索、复制实验，自动保存草稿，刷新后继续，查看运行状态与失败重试 |
| 文本分析 | 可选 DeepSeek 事实提取，展示原文引文、位置及分析快照 |
| 模型设置 | 查看后端模型与本机凭据状态，手动检测实际连接，查看具体失败原因 |
| 三类 Agent | 带范围标注的彩色滑块与精确数值输入，调整敏感度、基础权重和风险预算，每个实验保存独立参数 |
| 决策预览 | 策略页选择消息与风险情景，参数变化后用现有引擎重算三类策略的分值、目标仓位与拟调仓量 |
| 对照仿真 | 使用相同初始条件与种子运行有消息 / 无消息两组市场 |
| 批量对照 | 页面创建四情景 × 多种子批次，查看进度、策略范围图、失败原因与报告 |
| 真实原文案例 | 查看六个归档案例的来源、模型原始提取、复核修订及四条件 × 五种子决策路径 |
| 历史行情实验 | 查看 300294 的真实收益回放，比较无文本、关键词和复核后 LLM，追溯当日决策使用的原文 |
| 决策回放 | 带步骤刻度、消息作用区间和前后步按钮的时间轴，同时比较两组同一步的三类策略 |
| 结果归档 | 导出实验配置、文本依据、参数标识和撮合账本 |

## 三类 Agent

| 角色 | 设计侧重 |
| --- | --- |
| 激进型 | 对信息变化更敏感，允许更高的仓位和风险暴露 |
| 保守型 | 更重视不确定性与确认条件，控制仓位和交易频率 |
| 机构型 | 在信息响应之外考虑组合配置、集中度和再平衡规则 |

三个 Agent 共用参数化决策引擎，角色差异来自各自的敏感度、风险预算、确认和再平衡规则，目前没有分别训练三个投资者模型。DeepSeek-V4-Flash-0731-W8A8 用于提取原文事实，仓位和订单由策略规则计算；新建情景的方向和不确定性由用户设定，归档研究案例使用登记的事实映射。

这些差异是可检查的模型假设，不代表已通过真实投资者数据训练。目标仓位改变也不意味着一定产生订单或成交；平台分别展示各阶段结果。策略配置页展示原文理解、决策偏好和交易执行之间的关系。

参数可以拖动滑块或直接输入。权重与风险预算按百分比显示，例如输入风险预算 `0.83%` 会保存为内部数值 `0.0083`，不会取整到原滑块刻度。空值或越界值保留最后有效参数，并阻止保存或进入确认步骤；工作区默认、实验草稿和已存档实验各自保留参数。

策略页提供**同状态决策预览**：选择正向消息、负向消息、高不确定性或高波动，也可以编辑市场信号、作用范围和共同观察状态。每类账户使用相同的资金、持仓、价格、波动假设和决策前记忆，分别计算有消息与无文本结果；修改未保存参数也会自动重算。预览展示目标仓位、拟调仓幅度和约束原因，实际订单与成交需运行完整实验。该入口不调用 LLM，也不创建或修改实验记录。

## 快速启动

持续开发版本位于 **`codex/research-rebuild`** 分支；以下命令获取当前开发版本。

准备 Python 3.10 或更高版本，在 PowerShell 或终端执行：

```powershell
git clone --branch codex/research-rebuild https://github.com/rain-lei/MarketMirror.git
cd MarketMirror
python -m pip install -r requirements-research.txt
.\scripts\start-platform.ps1
```

打开 **http://127.0.0.1:8770/**。已有仓库时，在项目根目录执行启动脚本即可。脚本会检查端口并在当前终端前台运行服务；更新代码后重启服务，再刷新页面。也可以直接执行 `python -B -X utf8 -m design.server --port 8770`。

当前本地平台使用 Python 标准库 HTTP 服务和原生 HTML/CSS/JavaScript，无需前端构建。共享研究模块依赖 `openpyxl`，由上述安装命令提供。跳过文本分析即可运行合成市场实验；不需要原始 Excel 或历史研究产物。

### 可选：启用文本分析

在仓库根目录创建 `.env.local`，填入自己的网关密钥：

```dotenv
MARKETMIRROR_LLM_API_KEY=your_api_key
```

平台读取本机保存的配置。当前接入网关为 `http://aigw.dlut.edu.cn/v1`，模型为 `DeepSeek-V4-Flash-0731-W8A8`；需要对应网关的可用凭据。`.env.local` 不提交到 Git，密钥不会返回浏览器。

打开侧栏底部的**模型设置**可查看后端实际配置；读取和刷新配置不调用模型。点击**检测连接**才发送一段固定合成文字，显示请求及引文结构是否通过、检测时间和耗时。检测不发送当前草稿、不创建实验或分析记录，也不证明事实判断或市场预测准确性；检测结果在服务重启或本机凭据改变后清除。

## 使用流程

1. **输入消息**：填写原文与发布时间，可选择提取事实并检查引用依据。
2. **设置情景**：指定信号、不确定性、持续步数、种子与策略参数。
3. **运行对照**：两组共享初始条件，分别运行有消息和无消息市场。
4. **解释差异**：切换资产与决策步，检查三类 Agent 的仓位、订单和成交。
5. **保存结果**：实验自动保存在本机，可下载完整 JSON 归档。

实验数据位于 `research_outputs/platform_workspace/`，不随仓库分发。首页默认打开实验空间，只列本机记录；结果分析优先选择已有撮合实验。内置虚构消息位于“示例模板”，点击复用进入新建流程，保存并运行后生成自己的结果；模板本身不展示虚构收益或成交账本。

记录加载使用与配置匹配的结果快照，并限制同时读取数量。刷新失败时保留上次成功数据并提示；过期请求不能覆盖新选择，结果版本或依据不一致的记录会显示读取失败。

运行期间可以切换页面或新建另一份草稿。已提交的原文、参数和模型关联保持固定，结果保存到实验空间；离开确认页后完成不会强制跳转。同一草稿等待期间防止重复提交；保存响应丢失后重试沿用同一编号，已保存的失败实验从原记录恢复运行。

草稿在当前浏览器标签页自动保存，刷新后恢复原文、参数和创建步骤。实验空间的**继续草稿**可以返回原编辑位置；模型事实按原分析编号从本机重新读取，不因刷新重复调用模型。提交状态从本机实验记录确认，已完成的实验打开原结果。该缓存用于当前标签页，不是跨浏览器同步；浏览器无法保存时页面会明确提示。

要检验种子敏感性，打开左侧**批量对照**。输入种子、总步数及资金，四种固定情景共享每个种子的无消息基线；页面显示三类策略的均值与最小最大范围。单项失败后继续后续项，重试只处理未完成项；每项可以打开当时归档的决策账本，并下载批次报告。该入口使用人工设定情景，不调用 LLM。

要查看已实施的真实文本实验，打开**真实原文案例**。本机归档涵盖资管政策答记者问、武汉交通通告、春节休市公告和三条公司回复；可切换无文本、关键词、复核后 LLM 与资产暴露置换条件，再查看各步骤的仓位、订单和成交。引文可以定位到完整原文，原始模型判断与助手修订分别展示，并可导出所选路径。读取旧归档不重新调用模型，也不重跑市场。

普通结果与真实原文案例均提供**同一步决策对照**：切换资产和步骤，同时查看两组的目标权重、判断分值与订单执行差异。普通结果页上方的**查看同一步决策对照**可直接跳到时间轴和对照区。收益及权重差保留微小非零值，缺失账本字段不补零。两条路径的账户状态可能已不同，对照包含先前交易与价格反馈。

三个回放入口共用时间轴：拖动圆形滑块、点击刻度或前后步按钮查看决策，也可直接跳到消息进入时点。橙色区间标出登记的消息作用期；真实原文案例及历史行情实验读取各自归档的日期和来源时钟，普通情景使用假设步数。无文本条件仍沿用相同的消息时钟用于对照，但不接收文本输入。

打开**历史行情实验**可查看已完成的 300294 单案例开发实验：真实公司回复、当时 DeepSeek 的完整返回、逐条来源复核、三条件 × 三类策略 × 30 个交易日的结果，以及每一天的分值、目标仓位、请求、成交和费用。方向为零但有不确定性扣减时仍显示实际使用的引文；点击可以定位公司回复原文。订单以归一化指数单位展示，读取归档不发起新模型请求。

这些案例需要原有本地研究归档，新克隆仓库不会附带原文和完整实验结果；缺少归档时页面明确提示。研究产物的恢复与复现见[复现说明](research/PROTOTYPE_REPRODUCTION_2026.md)。本机完整核验命令为 `python -B -X utf8 -m design.source_cases --verify-all`。

## 当前边界

2026-10-06 已完成一轮[真实公司回复与真实历史行情实验](research/REAL_OBSERVED_EXPERIMENT_20261006.md)：重新调用 DeepSeek，三条件 × 三类 Agent × 30 个交易日，共 270 次决策，离线重放及账务核验通过。本轮 LLM 条件收益均低于无文本对照，报告保留负结果及逐日解释；页面的**历史行情实验**入口读取并展示这份归档。

- 新建及批量实验采用三资产合成情景，消息从第 5 步作用于资产 A；保存的发布时间不等于历史交易日回放。真实原文案例读取各自归档日历与信息截点，市场价格仍为合成数据。
- 历史行情实验使用真实历史收益，价格不受 Agent 交易影响；成交为简化的指数单位回放，尚未纳入整手、涨跌停排队、滑点、盘口和市场冲击。这是已查看的单案例开发实验，不能证明预测能力或策略普遍有效。
- LLM 提取结果只作为设定情景的参考，不自动转换为价格冲击。引文匹配通过也不代表语义判断一定正确。
- 研究目录保留历史实验、负结果与原有验证门槛；平台可用不等于历史预测或高保真验证通过。
- 当前服务仅监听本机，适合个人研究和演示，尚未提供多用户部署与权限管理。

## 项目结构

```text
design/          可视化平台、HTTP 服务、实验存储与仿真适配
research/        数据处理、语义实验、Agent 与市场引擎、研究报告
tests/           平台与研究模块测试
research_outputs/ 本地生成数据与运行产物（Git 忽略）
```

研究数据导入另需相应依赖与来源文件，详见[研究操作说明](research/README.md)。

## 开发验证

在仓库根目录执行平台相关检查。JavaScript 测试需要支持 `node --test` 的 Node.js：

```powershell
.\scripts\verify-platform.ps1
```

脚本会依次运行 Python 平台回归、Node 状态与视图回归、JavaScript 语法检查；检测到本机研究归档时，还会核验 120 条真实原文路径，以及历史行情实验的 270 次决策与文本依据。新克隆没有相应的 `research_outputs/` 归档时明确跳过相应核验，页面显示缺少归档，不补充演示数据。也可以按下面的命令分别执行：

```powershell
python -B -X utf8 -m unittest tests.test_platform_strategies tests.test_platform_strategy_preview tests.test_platform_analysis_binding tests.test_platform_text tests.test_platform_engine tests.test_platform_server tests.test_platform_scenario_batch tests.test_platform_batches tests.test_platform_source_cases tests.test_platform_observed_experiments tests.test_platform_submission tests.test_platform_model_connection tests.test_platform_draft_recovery -q
node --test tests/test_platform_strategy_state.cjs tests/test_platform_strategy_preview.cjs tests/test_platform_parameter_controls.cjs tests/test_platform_analysis_state.cjs tests/test_platform_decision_view.cjs tests/test_platform_copy.cjs tests/test_platform_record_loading.cjs tests/test_platform_batch_view.cjs tests/test_platform_case_view.cjs tests/test_platform_observed_view.cjs tests/test_platform_replay_control.cjs tests/test_platform_submission.cjs tests/test_platform_comparison.cjs tests/test_platform_model_view.cjs tests/test_platform_draft_cache.cjs
python -B -X utf8 -m design.source_cases --verify-all
python -B -X utf8 -m design.observed_experiments --verify-all
```

具体实验结论、运行条件和限制见[研究总结](research/RESEARCH_SUMMARY_2026.md)、[平台验证记录](design/PLATFORM_VALIDATION_20261005.md)、[批量对照核验](design/BATCH_PLATFORM_VALIDATION_20261006.md)、[真实原文案例核验](design/SOURCE_CASE_PLATFORM_VALIDATION_20261006.md)、[真实原文端到端实验核验](design/REAL_SOURCE_EXPERIMENT_VALIDATION_20261006.md)与[干净克隆可用性冒烟核验](design/FRESH_CLONE_SMOKE_VALIDATION_20261006.md)。

首页、模板分组及记录加载的实际核验见[实验读取核验](design/EXPERIMENT_LOADING_VALIDATION_20261006.md)。

后台提交、页面切换及丢失响应恢复见[实验提交核验](design/SUBMISSION_VALIDATION_20261006.md)。

两组同一步的三类策略及微小差异展示见[决策对照核验](design/DECISION_COMPARISON_VALIDATION_20261006.md)。

历史行情归档、仅有不确定性时的引文、共用回放时间轴与 Agent 模型说明见[历史行情平台接入核验](design/OBSERVED_PLATFORM_VALIDATION_20261006.md)。

参数单位换算、精确编辑、无效输入保护及实际引擎快照见[策略参数编辑核验](design/PARAMETER_EDITOR_VALIDATION_20261006.md)。

后端配置、连接检测与真实模型请求见[模型连接核验](design/MODEL_CONNECTION_VALIDATION_20261006.md)。

草稿刷新恢复、实际浏览器流程与结果对照直达入口见[草稿与浏览器核验](design/DRAFT_RECOVERY_VALIDATION_20261006.md)。同状态决策预览、实际引擎计算与交互验收见[决策预览核验](design/STRATEGY_PREVIEW_VALIDATION_20261007.md)。最新平台回归为 Python 91 项、Node 111 项通过。
