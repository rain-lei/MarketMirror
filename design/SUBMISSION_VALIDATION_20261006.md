# 实验提交、页面切换与恢复核验

2026-10-06。本轮完善新建实验的异步提交流程，使用生产 JavaScript 状态函数、真实 HTTP 服务和实际撮合引擎验证。没有再次调用大模型，没有修改冻结研究样本、角色模型或情景映射。

## 实际复现的问题

修复前的延迟响应检查确认：离开新建页后，结果仍会强制打开分析页；新记录插入数组会改变原选择；失败处理访问已卸载的表单并抛出空元素异常；两次点击会发送两次保存请求。原始检查记录保留在 `research_outputs/platform_submission_before_20261006_v1.json`。

## 最终行为

- 每份草稿独立记录提交状态。提交时固定原文、分析关联和策略快照；后续编辑不会改动已保存配置。
- 同一待处理草稿不能重复提交。按钮重新渲染后仍保留等待状态；另一份新草稿可以独立运行。
- 只有原确认页、原草稿、原配置均未改变，且期间没有离开页面，完成后才自动打开结果。其他情况保存到实验空间并通知，不切换当前页面或选择。
- 后台完成仅更新实验记录。失败处理不依赖旧表单，重跑完成也不重新渲染另一份草稿。
- 新建请求携带唯一提交编号；响应丢失后用同编号重试会返回原配置及最新运行状态。相同编号、不同配置会被拒绝，既有结果不会被覆盖。
- 已保存的失败记录从结果页恢复运行；不重新创建实验。服务端已完成的记录读取归档结果，不重复运行。身份、结果版本或依据哈希不一致时不接受为成功结果。
- 不带提交编号的原调用方式仍保留。新建页面的提交编号只存在于当前页面状态；刷新浏览器后从实验空间恢复已保存记录，尚未提交的草稿不提供跨刷新恢复。

## 验证范围与结果

Python 平台回归 58 项通过，新增 7 项检查包括真实 HTTP、配置冲突、工作区参数变化、重启后重复提交和八个同步竞争的保存请求。Node 平台回归 57 项通过，新增 16 项覆盖延迟响应、双击、两份草稿、丢失响应、配置冻结、选择恢复、依据哈希与缺失结果。

另在独立工作区启动前台服务 `http://127.0.0.1:8772/`，使用生产前端函数实际请求服务：

| 场景 | 实际结果 |
| --- | --- |
| 保存响应延迟，期间进入案例页并建立另一份草稿 | 原实验完成，当前页面、草稿和选择保持不变 |
| 同一确认页连续提交两次 | 一次保存、一次运行 |
| 服务端保存后模拟响应丢失，列表读取暂不可用 | 同编号重试；只有一份配置、一次运行 |
| 服务端完成后模拟运行响应丢失 | 从真实导出确认成功；打开结果未再次运行 |
| 重跑响应延迟，期间编辑新草稿 | 草稿保留，原实验第二次运行的两组市场路径与第一次一致 |
| 八个同编号 HTTP 保存请求并发 | 八次响应相同，仅建立一份记录 |

独立工作区共五条记录：四条已完成、一条仅保存；真实引擎运行五次，结果账本审计均通过。归档编号、原文哈希、策略哈希与结果依据一致。测试文字明确为合成示例，不计入真实研究样本。

执行记录及工作区均在 Git 忽略目录：

- `research_outputs/platform_submission_service_20261006_v1.json`：六项真实 HTTP / 前端状态核验及结果标识。
- `research_outputs/platform_submission_20261006_v1/`：上述五条配置和不可变结果版本。
- `research_outputs/platform_submission_service_20261006_v1.cjs`：本次实际服务核验脚本。
- `research_outputs/platform_submission_main_service_20261006_v1.json`：更新后的 8770 服务读取全部 19 条原记录、1 个批次与 6 个真实原文案例；所提供脚本与当前文件逐字节相同。

命令：

```powershell
python -B -X utf8 -m unittest tests.test_platform_strategies tests.test_platform_analysis_binding tests.test_platform_text tests.test_platform_engine tests.test_platform_server tests.test_platform_scenario_batch tests.test_platform_batches tests.test_platform_source_cases tests.test_platform_submission -q
node --test tests/test_platform_strategy_state.cjs tests/test_platform_analysis_state.cjs tests/test_platform_decision_view.cjs tests/test_platform_copy.cjs tests/test_platform_record_loading.cjs tests/test_platform_batch_view.cjs tests/test_platform_case_view.cjs tests/test_platform_submission.cjs
```

真实 HTTP 与状态函数检查不能替代浏览器验收。本轮未完成浏览器外观、键盘操作和窄屏布局的实际验收。
