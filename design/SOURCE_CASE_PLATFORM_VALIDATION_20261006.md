# 真实原文案例：平台接入与核验

2026-10-06。平台增加“真实原文案例”只读入口，连接六个已实施的研究开发案例，展示原文、模型原始事实、源文修订、数值假设、决策与实际撮合记录。原始 DeepSeek 请求和市场路径是 2026-10-04 的实验；本次读取没有再次调用模型或重新生产市场结果。

## 读取范围与解释边界

六例为资管政策答记者问、武汉交通通告、春节休市公告及三条固定公司回复。四个条件为无文本、关键词、复核后 LLM 和 LLM 资产暴露置换；每个条件有种子 7、11、23、47、89，共 120 条不同归档路径。原始模型提取 24 条事实，助手修订其中六条；原始与复核版本分别展示，不称为独立准确率验证。

来源文本与归档日历使用真实资料，市场、角色规则、数值幅度和资产暴露为声明的假设。武汉案例首次进入信息截点是第 4 步；页面使用原时钟，不套用普通新建实验的第 5 步。完整路径差异包含此前交易与价格反馈，不解释为固定状态的纯文本因果效应。

无文本条件与同一路径自对照，预期差异为零，只计 18 个不同市场日；其他条件读取两条路径，计 36 个市场日。基线在多个条件视图中重复出现，以下 HTTP 的 240 次路径匹配不是 240 条不同路径。

## 归档与页面行为

- 登记文件固定协议和总清单哈希，校验原 87 项来源与实现绑定、复核身份、摘要及冻结范围。
- 打开所选条件时，核对压缩路径哈希、来源可见性、映射、决策解释、18 步结算及期末钱包、持仓、价格和费用池。缓存命中仍检查原文件哈希。
- 缺少归档时目录不可用；完整性错误返回 503。没有替代路径或填零结果，普通合成实验仍可独立使用。
- 切换案例、条件、种子后，旧响应不能覆盖新选择。刷新失败保留原归档并提示，缺失目录会取消尚未完成的选择。
- 原始与复核事实的引文按 Unicode 字符位置定位。信息回放说明本步采用的复核事实或固定关键词；无文本和关键词条件不冒用 LLM 映射。
- 资产与步骤切换读取原始观察、参与者参数和撮合订单。两组价格使用同一刻度，收益、订单请求、接受与成交分别展示。
- 导出固定所选条件与种子，包含原文、修订、映射、配对统计和两条市场路径。复用原文创建新实验会清除旧分析关联，重新设定情景和策略，不修改原研究归档。

## 实际核验结果

| 检查 | 结果 |
| --- | --- |
| 原始归档全量读取 | 120 条路径、2,160 个市场日、25,920 条决策通过 |
| HTTP 完整条件范围 | 6 案例 × 4 条件 × 5 种子全部返回对应归档 |
| HTTP 返回与原始压缩文件 | 240 次所选 / 基线路径逐字段匹配 |
| 无文本自对照 | 30 条自对照路径、零目标 / 订单 / 成交变化核对通过 |
| 实际决策视图读取 | 12,960 个步骤 × 资产 × 条件视图，38,880 个角色视图核对账户、加权目标和订单数量 |
| 期末收益与订单摘要 | 从钱包、股份、价格及逐笔订单独立重算，与归档一致 |
| 详情函数 | 120 条条件详情生成价格图、三类决策卡及事实；无 NaN / undefined |
| 引文定位 | 原始与复核版本合计 48 处引文定位一致 |
| JSON 导出 | 六个案例导出内容与对应读取结果一致 |
| 新增本步依据展示 | 24 个案例 / 条件、2,592 个时钟视图、288 次活动事实引用核对通过 |
| 原平台存储 | 默认工作区仍有 19 条实验与 1 个批次 |

独立核验记录在 Git 忽略的本地目录：

- `research_outputs/platform_source_case_verification_20261006_v2.json`：全 120 条路径读回。
- `research_outputs/platform_source_case_service_20261006_v1.json`：完整 HTTP、原始账本与视图字段核对。
- `research_outputs/platform_source_case_service_20261006_v2.json`：最终服务全范围及 30 条自对照确认。
- `research_outputs/platform_source_case_input_basis_20261006_v1.json`：本步事实 / 关键词依据展示。

上述进程均实际退出 0。平台回归为 Python 51 项、Node 33 项通过；新增便携测试使用临时合成来源和真实撮合引擎，不把测试数据列为研究样本。检查涵盖缓存后篡改、缺失 / 不完整复核、错误查询、导出、选择竞争、刷新失败、引文位置及新实验关联隔离。

## 复查与界面限制

```powershell
python -B -X utf8 -m design.source_cases --verify-all
python -B -X utf8 -m unittest tests.test_platform_strategies tests.test_platform_analysis_binding tests.test_platform_text tests.test_platform_engine tests.test_platform_server tests.test_platform_scenario_batch tests.test_platform_batches tests.test_platform_source_cases -q
node --test tests/test_platform_strategy_state.cjs tests/test_platform_analysis_state.cjs tests/test_platform_decision_view.cjs tests/test_platform_copy.cjs tests/test_platform_record_loading.cjs tests/test_platform_batch_view.cjs tests/test_platform_case_view.cjs
```

全范围归档命令要求已有原始本地研究资料，新克隆仓库不会附带这些文件。自动化测试可使用临时数据运行。

本轮没有完成浏览器外观、键盘操作或窄屏布局的实际验收。HTTP 和视图函数输出通过不能替代浏览器验收；这里也不声称商业部署、多用户权限或真实市场预测已经完成。
