# MarketMirror

### 三类 Agent · 市场情景仿真 · 可追溯决策

MarketMirror 是一个本地运行的可视化金融仿真实验平台。输入一段政策、公告或公司问答，设置情景变量，比较**激进型、保守型、机构型**三类 Agent 如何调整仓位、提交订单和形成成交。

项目关注可解释、可复现的行为差异。当前 Agent 使用预设决策规则；DeepSeek 辅助提取原文事实，情景参数由用户设置。平台并非真实市场预测系统。

[快速启动](#快速启动) · [平台使用说明](design/README.md) · [研究与复现](research/README.md) · [历史进度](research/README_HISTORY_20261005.md)

## 可以做什么

| 功能 | 当前实现 |
| --- | --- |
| 实验空间 | 新建、搜索、复制实验，查看运行状态，失败后重试 |
| 文本分析 | 可选 DeepSeek 事实提取，展示原文引文、位置及分析快照 |
| 三类 Agent | 调整文本敏感度、基础股票权重和风险预算，每个实验保存独立参数 |
| 对照仿真 | 使用相同初始条件与种子运行有消息 / 无消息两组市场 |
| 批量对照 | 页面创建四情景 × 多种子批次，查看进度、策略范围图、失败原因与报告 |
| 真实原文案例 | 查看六个归档案例的来源、模型原始提取、复核修订及四条件 × 五种子决策路径 |
| 决策回放 | 查看信息输入、评分、目标仓位，以及订单请求、接受与实际成交 |
| 结果归档 | 导出实验配置、文本依据、参数标识和撮合账本 |

## 三类 Agent

| 角色 | 设计侧重 |
| --- | --- |
| 激进型 | 对信息变化更敏感，允许更高的仓位和风险暴露 |
| 保守型 | 更重视不确定性与确认条件，控制仓位和交易频率 |
| 机构型 | 在信息响应之外考虑组合配置、集中度和再平衡规则 |

这些差异是可检查的模型假设，不代表已通过真实投资者数据训练。目标仓位改变也不意味着一定产生订单或成交；平台分别展示各阶段结果。

## 快速启动

当前平台代码位于 **`codex/research-rebuild`** 分支，默认 `main` 仍是旧项目。

准备 Python 3.10 或更高版本，在 PowerShell 或终端执行：

```powershell
git clone --branch codex/research-rebuild https://github.com/rain-lei/MarketMirror.git
cd MarketMirror
python -m pip install -r requirements-research.txt
python -B -X utf8 -m design.server --port 8770
```

打开 **http://127.0.0.1:8770/**。已有仓库时，在项目根目录执行最后一条启动命令即可。

当前本地平台使用 Python 标准库 HTTP 服务和原生 HTML/CSS/JavaScript，无需前端构建。共享研究模块依赖 `openpyxl`，由上述安装命令提供。跳过文本分析即可运行合成市场实验；不需要原始 Excel 或历史研究产物。

### 可选：启用文本分析

在仓库根目录创建 `.env.local`，填入自己的网关密钥：

```dotenv
MARKETMIRROR_LLM_API_KEY=your_api_key
```

平台读取本机保存的配置。当前接入网关为 `http://aigw.dlut.edu.cn/v1`，模型为 `DeepSeek-V4-Flash-0731-W8A8`；需要对应网关的可用凭据。`.env.local` 不提交到 Git，密钥不会返回浏览器。

## 使用流程

1. **输入消息**：填写原文与发布时间，可选择提取事实并检查引用依据。
2. **设置情景**：指定信号、不确定性、持续步数、种子与策略参数。
3. **运行对照**：两组共享初始条件，分别运行有消息和无消息市场。
4. **解释差异**：切换资产与决策步，检查三类 Agent 的仓位、订单和成交。
5. **保存结果**：实验自动保存在本机，可下载完整 JSON 归档。

实验数据位于 `research_outputs/platform_workspace/`，不随仓库分发。首页默认打开实验空间，只列本机记录；结果分析优先选择已有撮合实验。内置虚构消息位于“示例模板”，点击复用进入新建流程，保存并运行后生成自己的结果；模板本身不展示虚构收益或成交账本。

记录加载使用与配置匹配的结果快照，并限制同时读取数量。刷新失败时保留上次成功数据并提示；过期请求不能覆盖新选择，结果版本或依据不一致的记录会显示读取失败。

运行期间可以切换页面或新建另一份草稿。已提交的原文、参数和模型关联保持固定，结果保存到实验空间；离开确认页后完成不会强制跳转。同一草稿等待期间防止重复提交；保存响应丢失后重试沿用同一编号，已保存的失败实验从原记录恢复运行。

要检验种子敏感性，打开左侧**批量对照**。输入种子、总步数及资金，四种固定情景共享每个种子的无消息基线；页面显示三类策略的均值与最小最大范围。单项失败后继续后续项，重试只处理未完成项；每项可以打开当时归档的决策账本，并下载批次报告。该入口使用人工设定情景，不调用 LLM。

要查看已实施的真实文本实验，打开**真实原文案例**。本机归档涵盖资管政策答记者问、武汉交通通告、春节休市公告和三条公司回复；可切换无文本、关键词、复核后 LLM 与资产暴露置换条件，再查看各步骤的仓位、订单和成交。引文可以定位到完整原文，原始模型判断与助手修订分别展示，并可导出所选路径。读取旧归档不重新调用模型，也不重跑市场。

这些案例需要原有本地研究归档，新克隆仓库不会附带原文和完整实验结果；缺少归档时页面明确提示。研究产物的恢复与复现见[复现说明](research/PROTOTYPE_REPRODUCTION_2026.md)。本机完整核验命令为 `python -B -X utf8 -m design.source_cases --verify-all`。

## 当前边界

- 新建及批量实验采用三资产合成情景，消息从第 5 步作用于资产 A；保存的发布时间不等于历史交易日回放。真实原文案例读取各自归档日历与信息截点，市场价格仍为合成数据。
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
python -B -X utf8 -m unittest tests.test_platform_strategies tests.test_platform_analysis_binding tests.test_platform_text tests.test_platform_engine tests.test_platform_server tests.test_platform_scenario_batch tests.test_platform_batches tests.test_platform_source_cases tests.test_platform_submission -q
node --test tests/test_platform_strategy_state.cjs tests/test_platform_analysis_state.cjs tests/test_platform_decision_view.cjs tests/test_platform_copy.cjs tests/test_platform_record_loading.cjs tests/test_platform_batch_view.cjs tests/test_platform_case_view.cjs tests/test_platform_submission.cjs
```

具体实验结论、运行条件和限制见[研究总结](research/RESEARCH_SUMMARY_2026.md)、[平台验证记录](design/PLATFORM_VALIDATION_20261005.md)、[批量对照核验](design/BATCH_PLATFORM_VALIDATION_20261006.md)与[真实原文案例核验](design/SOURCE_CASE_PLATFORM_VALIDATION_20261006.md)。

首页、模板分组及记录加载的实际核验见[实验读取核验](design/EXPERIMENT_LOADING_VALIDATION_20261006.md)。

后台提交、页面切换及丢失响应恢复见[实验提交核验](design/SUBMISSION_VALIDATION_20261006.md)。
