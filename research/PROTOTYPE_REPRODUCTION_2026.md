# MarketMirror 机制原型：离线复现入口

<!-- prototype-risk-current -->
当前范围与结果（2026-10-05）：交付目标是可解释、可复现的三类预设决策规则仿真。六来源开发链路、同状态对照、有限强度／时长以及全部240不重复原型路径的风险与成交核验已完成；完整重放共290文件逐字节相同。六例已查看且经助手纠正，角色、资产暴露和数值映射为显式假设。见[研究总结](RESEARCH_SUMMARY_2026.md)、[逐项验收](PROTOTYPE_ACCEPTANCE_2026.md)及[离线复现](PROTOTYPE_REPRODUCTION_2026.md)。
原登记三组资源720条件、720配对及36分层已完成全范围核验，十个生产／审计阶段和两个最终命令实际退出0。统一100、原价固定现金股数、原价保持财富组的五项联合开发容差通过数分别18/240、0/240、28/240。股数成交量与名义金额分别导出，见[完整资源结果](TEMPORAL_RESOURCE_UNIT_RESULTS_2022.md)与[实际执行凭据](RESOURCE_BATCH_RECOVERY_2026.md)；保留全部负结果和旧未知退出码，未选新默认。
已有全项目1,143项测试实际通过凭据；恢复执行新增26项相关检查另行保留。当前强调决策可解释、账务与信息时点正确、有限对照及复现。真实预测、高保真拟合、真实投资者训练和工作台不作为本阶段交付条件。原v6正式自动语义入口仍未通过既定门槛。清理残余被拒绝的情况在逐项验收表单列。以下保留有日期的历史阶段记录。
<!-- /prototype-risk-current -->

所有命令在项目根目录运行。已核对本地 Python 版本为 3.12.14；以下入口读取归档，不调用大模型或读取 API Key。案例命令先核对相应协议、来源及原始产物哈希。

## 读取当前结果

```powershell
Set-Location 'D:\rain\Desktop\marketmirrow\MarketMirror'
$taskPython = 'C:/Users/rain_/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
& $taskPython -B -X utf8 -m research.prototype status
& $taskPython -B -X utf8 -m research.prototype verify
& $taskPython -B -X utf8 -m research.prototype case --case wuhan
& $taskPython -B -X utf8 -m research.prototype case --case wuhan --mode no_text
& $taskPython -B -X utf8 -m research.prototype case --case wuhan --kind path
& $taskPython -B -X utf8 -m research.prototype case --case qa2
& $taskPython -B -X utf8 -m research.prototype risk --case high_risk
& $taskPython -B -X utf8 -m research.prototype risk --case wuhan --seed-summary ranges
```

status 显示四个已归档研究的核验状态，未在这条命令里重新核全部原始文件。verify 逐个校验四研究的绑定与产物；实际命令核对 415 个唯一文件，以已完成的独立账本核验为依据，不声称实际市场拟真。

case 默认 kind=fixed：共同现金、股数、价格、风险与记忆，显示三类未执行请求，actual_fills 明确为 null。kind=path 显示原完整路径请求与实际成交，前期账户状态可能不同。两者不可合并。

案例别名：pbc、wuhan、calendar、qa1、qa2、qa3，也接受完整 case_id。种子为 7、11、23、47、89。fixed 支持 reviewed_llm/llm_asset_placebo 的 0.5、1、1.5 倍；no_text/keywords 仅支持 1。--context within_actor 查看各自原账户的信息对照。

risk 读取全部当前 240 条不重复路径的风险摘要，可用 high_risk 或六真实来源别名，--case all 显示全部条件。默认显示五种子中位数，--seed-summary ranges 保留最小/中位/最大；范围不是置信区间。风险输入不是实际跌幅，减仓请求不是减仓完成；无接受订单的成交比例为 null。

## 重算归档研究

示例使用三个尚未存在的新目录；如果目录已经存在，改成新的名称。不会重新请求 LLM，不覆盖既有事实、模型回答、120 基线或敏感性产物。

```powershell
& $taskPython -B -X utf8 -m research.simulation.run_source_case_fixed_state --output-dir research_outputs/replay_fixed_state_example_v2
& $taskPython -B -X utf8 -m research.simulation.audit_source_case_fixed_state --output-dir research_outputs/replay_fixed_state_example_v2
& $taskPython -B -X utf8 -m research.simulation.run_source_case_duration --output-dir research_outputs/replay_duration_example_v2
& $taskPython -B -X utf8 -m research.simulation.audit_source_case_duration --output-dir research_outputs/replay_duration_example_v2
& $taskPython -B -X utf8 -m research.simulation.run_prototype_risk_diagnostics --output-dir research_outputs/replay_risk_example_v2
& $taskPython -B -X utf8 -m research.simulation.audit_prototype_risk_diagnostics --output-dir research_outputs/replay_risk_example_v2
```

固定状态及时长会生成同一注册范围的新产物；风险命令从原 240 路账本重算指标，不新增市场路径。配置核对冻结来源、代码与测试凭据；任一科学绑定不一致就停止。独立核验凭据独占写入，同一个新目录只核验一次；再次重放使用另一个新目录。

实际全范围重放证据：[固定状态/时长 98 文件](../research_outputs/mechanism_prototype_full_replay_20261004_v1.json)、[风险 5 文件](../research_outputs/prototype_risk_full_replay_20261004_v1.json)，全部产物逐字节一致。命令示例的新目录仅为防止覆盖，参数名称变化没有改注册范围。

## 完整重放原 180 条路径

原探针的规划文档版本差异现可用独立输入副本解决。以下入口保持原科学代码和协议，自动生产、独立回读并逐字节比较全部 60 探针及 120 原文路径；不调用模型。目录必须尚未存在。

```powershell
& $taskPython -B -X utf8 -m research.replay_prototype_archives --output-dir research_outputs/replay_original_paths_example_v1
```

实际四阶段全部退出 0，187 科学文件相同，见[完整重放说明](PROTOTYPE_FULL_REPLAY_2026.md)。加上前述固定状态、时长和风险，共 290 相同文件；当前全部 240 条不重复完整路径都有实际重放证据。新审计凭据记录新生产进程，单独保存，未假称与旧进程凭据一致。

## 后台资源对照的只读进度

```powershell
& $taskPython -B -X utf8 -m research.resource_progress
```

该命令核对实际退出凭据、当前绑定及原生进程身份，区分生产完成和核验完成；不会执行新仿真或调用模型。封存条件数不能替代完整核验，旧整批与本轮逐条件凭据分别显示。原720条件的恢复方式和当前完成证据见[资源恢复记录](RESOURCE_BATCH_RECOVERY_2026.md)。

## 证据与版本边界

[研究总结](RESEARCH_SUMMARY_2026.md)、[逐项验收](PROTOTYPE_ACCEPTANCE_2026.md)、[真实来源链路](SOURCE_TEXT_TO_AGENT_DECISIONS_2026.md)、[固定状态敏感性](MECHANISM_SENSITIVITY_RESULTS_2026.md)、[风险与成交](PROTOTYPE_RISK_DIAGNOSTICS_2026.md)。当前索引为[v2 证据清单](configs/mechanism_prototype_evidence_2026_v2.json)，命令实际退出见[八条入口凭据](../research_outputs/mechanism_prototype_cli_smoke_20261004_v2.json)。v1 清单、命令凭据及旧 CLI 字节快照均保留，不改写历史结果。

旧探针协议绑定的非执行范围文档已更新。新风险协议明确使用其确切旧字节快照，没有更改旧科学绑定；原探针旧命令检查现用文档仍会拒绝这个版本差异，不能把它宣称为未经处理即可重跑。新风险、固定状态、时长命令已有完整实际复现证据。

原720资源条件的全部作业及最终核验已独立完成，见[资源结果](TEMPORAL_RESOURCE_UNIT_RESULTS_2022.md)，与六来源案例的范围分别报告。三类角色、市场、资产暴露和冲击幅度为显式假设；真实预测能力和原v6正式门槛未由这些命令证明。

## 重新导出已完成资源结果

这条命令只读取已完成的实际凭据及720条件，不执行新仿真、不请求模型；目的目录与报告文件必须尚不存在。

```powershell
& $taskPython -B -X utf8 -m research.export_resource_unit_results --output research_outputs/resource_result_reexport --report research/RESOURCE_RESULT_REEXPORT.md
```

全量表保存720条件、720配对和36分层，分别提供成交股数及整数最小货币单位的单边成交额。未定义统计在CSV中留空；参照[首次实际导出凭据](../research_outputs/resource_unit_result_export_job_20261005_v1.json)。
