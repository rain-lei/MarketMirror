# 可视化批量对照：实现与核验

2026-10-06。平台增加“批量对照”页面，通过实际 HTTP 接口创建和异步执行四情景 × 多种子实验，展示进度、三类策略范围图、逐项结果、冻结参数与报告。这些情景是人工假设，不使用真实公告、不调用 LLM，也不证明历史预测或真实投资者行为。

## 执行与归档行为

- 每个种子共用无消息基线；创建批次时冻结已保存的三类参数及撮合版本。
- 单项失败继续后续项；失败、待运行和中断项不填零、不参与均值。重试仅处理未完成项。
- 页面关闭后后台继续；服务重启恢复中断状态，可继续未完成项。运行中及已完成批次不允许重复启动。
- 批次固定每项结果 ID 及路径哈希。单个实验后续重跑不会改变批次统计或“查看决策”的归档；损坏归档拒绝读取。
- 切换批次或发出新刷新请求后，迟到响应不能覆盖当前选择。读取失败保留上次数据并提示可能未更新。轮询只刷新结果区域，保留正在编辑的表单、统计展开状态及结果区焦点。

## 实际完整批次

批次 ID：`e1e4ccf73dbc4ea097e5f20ab33eab72`。通过 HTTP 创建，启动接口返回 202；四情景 × 种子 1、7、19，单项 18 步、消息持续 6 步、每类现金 100 万模型元。工作区当时已保存的策略参数被完整复制。

12/12 项完成，24 条市场路径共 432 个市场日经过引擎账本审计。另一路计算直接读取全部归档文件，核对：

| 核验对象 | 数量与结果 |
| --- | --- |
| 每日决策账户集合 | 5,184 条决策记录读回，账户集合一致 |
| 最后账户钱包、股份及价格重构角色财富 | 72 个角色路径收益与原摘要一致 |
| 有消息减无消息收益差 | 36 项与批次一致 |
| 逐笔订单请求、接受和成交股数 | 36 项全程总数与批次一致 |
| 同一种子的无消息基线哈希 | 3 个种子，各情景一致 |
| HTTP 跨种子均值、最小和最大值 | 12 行从完整原始结果独立重算一致 |
| 中文报告 | HTTP 下载成功，完成数 12/12 |

首次独立校验脚本误用三个策略模板名匹配实际十二个账户名，漏计订单并退出失败。改用归档的逐步决策账户集合后，以上核验全部通过；原失败记录保留，没有重跑或修改实验结果。

完整产物在 Git 忽略的 `research_outputs/platform_visual_batch_20261006_v1/`，含原始 workspace、报告、`verification.json`、首次 `verification_failure.json`。核验后复制到默认平台工作区，所有复制文件逐字节核对一致，发布记录在 `publication.json`。这些本机数据不随 GitHub 代码分发。

## 自动化检查与界面限制

平台 Python 回归 44 项、Node 状态与视图回归 24 项通过。新增测试真实调用撮合引擎，验证参数冻结、运行中可查询、并发重复拒绝、单项故障隔离、只重试失败项、服务中断恢复、不可变归档与篡改拒绝；HTTP 测试覆盖创建、启动、读取、报告和静态视图资源。前端检查覆盖选择竞争、迟到响应、缺失与真实零值、种子输入，以及使用归档版本打开决策。

本轮没有完成浏览器外观和完整交互验收。HTTP、JavaScript 语法及视图函数测试不能替代此项；不将上述自动化检查称为浏览器验收。当前仍需验证实际渲染、键盘操作与桌面/窄屏布局。

```powershell
python -B -X utf8 -m unittest tests.test_platform_strategies tests.test_platform_analysis_binding tests.test_platform_text tests.test_platform_engine tests.test_platform_server tests.test_platform_scenario_batch tests.test_platform_batches -q
node --test tests/test_platform_strategy_state.cjs tests/test_platform_analysis_state.cjs tests/test_platform_decision_view.cjs tests/test_platform_copy.cjs tests/test_platform_record_loading.cjs tests/test_platform_batch_view.cjs
```
