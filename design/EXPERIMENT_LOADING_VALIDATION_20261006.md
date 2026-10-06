# 实验空间与结果读取核验

2026-10-06。首次加载分析页曾默认停在虚构示例，即使本机已有撮合结果。旧读取器还同时发出全部详情请求，再单独读取结果，在重跑发布时可能混合不同时间的记录；大并发下实测出现连接失败。

## 最终行为

- 默认首页为实验空间。本机记录与三条虚构消息模板分别展示，导航计数不含模板；模板复用进入新建流程，运行后才生成结果。
- 结果分析首次选择优先使用已有撮合记录，保留用户选择的有效本机实验。未选择可读记录时显示操作入口；加载中不填入示例图表。
- 移除手写的示例收益、仓位、成交图与相关交互代码。既有历史预览文件仍保留，并以明确标签读取。
- 读取使用服务端 JSON 导出的记录 / 结果快照，同一实验的版本及已存在的来源、参数和分析哈希不得矛盾。旧版未保存这些字段时维持兼容，不宣称补齐了历史标识。
- 每批最多四个快照请求。读取失败保留已知记录并显示失败 ID；未加载记录不补零，过期刷新不覆盖新状态。新建实验使正在等待的旧列表失效，避免刚创建的记录被旧响应移除。
- 成功读取新列表后，已从服务端列表移除的旧记录不继续显示。服务连接队列增加至 32，缓解多个页面请求同时到达时的短时拥塞。

## 实际检查

默认工作区的 19 条记录均可单独读取，其中 17 条为撮合结果、2 条为历史预览。旧读取器的一次观测只读到 17 条且默认选中 `EXP-026`；另一次 19 个并发请求中，7 条报 `fetch failed`。保留原文件，没有将连接失败认定为数据损坏，也没有重新运行这些实验。

新读取器连续三次加载 19/19 条记录，最大并发为 4，零请求失败；默认选中已有撮合实验 `4bf25e843f8341d4832742c0483a2c34`。最终服务的 19 个并发快照请求全部返回成功，模板不进入本机表格，原六来源案例目录仍可用。

本地执行记录在 Git 忽略目录：`platform_landing_before_20261006_v1.json`、`platform_landing_after_20261006_v1.json`、`platform_landing_final_20261006_v1.json`，均位于 `research_outputs/`。最终工具实际退出 0，没有新增模型请求或市场重跑。

平台 Python 51 项、Node 41 项通过。记录加载的 11 项 Node 检查覆盖初次选择、模板动作、四请求并发上限、迟到列表、新建时旧请求失效、失败刷新保留、删除记录、错误身份 / 版本 / 来源、缺失结果、旧格式与损坏记录恢复。JavaScript 语法和 Git 差异空白检查通过。

```powershell
python -B -X utf8 -m unittest tests.test_platform_strategies tests.test_platform_analysis_binding tests.test_platform_text tests.test_platform_engine tests.test_platform_server tests.test_platform_scenario_batch tests.test_platform_batches tests.test_platform_source_cases -q
node --test tests/test_platform_strategy_state.cjs tests/test_platform_analysis_state.cjs tests/test_platform_decision_view.cjs tests/test_platform_copy.cjs tests/test_platform_record_loading.cjs tests/test_platform_batch_view.cjs tests/test_platform_case_view.cjs
```

这轮检查使用实际 HTTP 数据和视图函数，未完成浏览器外观与完整交互验收。它证明读取和展示的数据边界，不证明市场预测、投资者训练或多用户商业部署已经完成。
