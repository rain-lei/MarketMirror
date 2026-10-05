# MarketMirror：原始探针和开发案例的完整离线重放

2026-10-04。此次实际重跑原注册的 60 条合成探针和 120 条原文开发案例，未新增实验条件或在线模型请求。四个生产和独立核验进程均退出 0；全部 187 个原始科学产物逐字节相同。由此补齐旧探针因规划文档更新而无法直接重跑的问题；当前工作区文件和旧协议均未被还原或修改。

## 版本处理与运行方法

旧探针协议的 53 个绑定包含一个非执行的范围文档。其历史确切字节此前已保存；新重放配方显式记录原路径、旧哈希及快照位置。入口先校验全部 292 个绑定，再把原探针输入复制到独立子目录，包括原科学代码、原协议、背景来源、原规划文档和事件 schema。56 个复制文件逐一校验。原生产命令在该目录运行，无需更改冻结校验逻辑或现用规划文件。

原文案例在现用项目中执行原冻结生产程序，读取原始模型事实与复核记录，不重新请求 DeepSeek。两个核验阶段分别复用原独立审计脚本的确切函数，重新检查全部新生成原始账本、时间、协方差、角色解释、资金股数费用和所有配对；未调用生产指标函数生成参考答案。独立审计仍由同一助手负责，不是双人复核。

```powershell
Set-Location 'D:\rain\Desktop\marketmirrow\MarketMirror'
$taskPython = 'C:/Users/rain_/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
& $taskPython -B -X utf8 -m research.replay_prototype_archives --output-dir research_outputs/replay_original_paths_example_v1
```

输出目录必须尚未存在；再次执行改用新的名称。入口一次完成两研究的生产、独立审计和逐字节比较。失败、进程实际退出码和部分目录保留，不把未完成写成成功。实际执行记录见[完整重放凭据](../research_outputs/replay_original_180_paths_20261004_v1/replay_receipt.json)与[外层进程退出](../research_outputs/prototype_original_replay_process_20261004_v1.json)，冻结配方见[原 180 路重放协议](configs/prototype_archive_replay_2026_v1.json)。

## 完整核验范围

| 原研究 | 实际重跑 | 独立回读 | 与原归档逐字节相同 |
| --- | --- | --- | --- |
| 六合成探针 | 全部 60 路；生产 PID 31564 退出 0 | 审计 PID 33828 退出 0；1,080 步、3,240 竞价、12,960 决策、30 配对 | 60 压缩完整路径、历史背景、冻结计划、摘要和清单，共 64 文件 |
| 六原文开发案例 | 四条件五种子共 120 路；生产 PID 30784 退出 0 | 审计 PID 25900 退出 0；2,160 步、6,480 竞价、25,920 决策、120 配对 | 120 压缩完整路径、冻结计划、摘要和清单，共 123 文件 |

新审计凭据另存，包含本次实际生产 PID；它们与旧审计的进程来源不同，不宣称审计凭据本身逐字节一致。187 文件指原清单中的全部产物及清单本身，没有遗漏零效应案例或不利结果。核验：[60 路审计](../research_outputs/replay_original_180_paths_20261004_v1/probes/replay_independent_verification.json)、[120 路审计](../research_outputs/replay_original_180_paths_20261004_v1/source_cases/replay_independent_verification.json)。

另已验证重复指定现有重放目录会退出 1，全部 256 个已有文件保持不变，见[独占输出检查](../research_outputs/prototype_original_replay_exclusive_output_20261004_v1.json)。这是预期的拒绝覆盖，不是实验失败。

此前[固定状态及信息时长重放](../research_outputs/mechanism_prototype_full_replay_20261004_v1.json)为 98 相同文件，[风险重算](../research_outputs/prototype_risk_full_replay_20261004_v1.json)为 5 相同文件；合计 290 文件。合成探针 60 路、原文基线 120 路和新增时长 60 路，当前全部 240 条不重复完整市场路径均已有实际重放与独立审计证据。固定状态计算和风险重算同时另有实际重放证据。

## 结论及剩余工作

此次确认的是离线操作性、计算一致性和版本可追溯。重复原条件不增加独立真实事件数，也不改变原 v6 类别宏 F1 0.561 未达到 0.60 的事实；角色、合成市场、响应幅度和资产暴露仍为假设。

原 2022 资源对照前两组各 96 个新条件的生产及全量审计均实际退出 0，第三组继续从已封存条件恢复；旧消失进程的未知退出码仍保留。全部 720 条件及最终汇总审计尚未完成，不计入这里已通过的范围。当前不追加高保真行情拟合或工作台。研究结论见[总结](RESEARCH_SUMMARY_2026.md)，当前边界见[逐项验收](PROTOTYPE_ACCEPTANCE_2026.md)。
