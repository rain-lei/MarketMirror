# 武汉事前问答：DeepSeek 预测、助手逐条复核与接入门槛

更新日期：2026-09-28。

固定的[事前问答样本](WUHAN_PRE_EVENT_QA_SNAPSHOT_2020.md)有 126 家公司，其中 105 家在 2020-01-22 截点前有确认回复。已按输入包冻结的 `http://aigw.dlut.edu.cn/v1`、`DeepSeek-V4-Flash-0731-W8A8`、`semantic-prompt-v2` 和温度 0 完成 105/105 条请求。请求失败 0；独立标准化保留 1 条引文不在当时可见原文中的解析错误，没有替模型改写证据。原始响应、标准化输出、模型运行审计均在本机 Git 忽略目录，密钥未写入产物或仓库。

按用户确定的单人复核流程，助手逐条阅读完整提问、公司回复和模型候选，为 105/105 条写入独立理由；只用公司回复中的精确引文支持事件。最终 26 条含事件，共 28 个事件；另 79 条不确认事件。复核指出：仅有提问的传闻、未获批事项、控股股东而非上市公司的动作、旧披露日期、例行股东人数、静态产品清单和仅有适用性而无订单的技术说明，都不能自动升级为公司新事件。该参考由单一 AI 形成，且已见模型候选，**不是独立人工金标准**。

## 冻结评分结果

| 指标 | DeepSeek | 字面关键词对照 | 预设门槛 |
|---|---:|---:|---:|
| 事件检出 TP / FP / FN | 23 / 35 / 3 | 12 / 35 / 14 | — |
| 检出精确率 | 0.397 | 0.255 | — |
| 检出召回率 | 0.885 | 0.462 | — |
| 检出 F1 | **0.548** | 0.329 | ≥ 0.70，且不低于关键词 |
| 有支持类别宏 F1 | **0.318** | 0.082 | ≥ 0.60 |
| 结构/证据解析失败 | 1/105 | 0/105 | ≤ 5% |

六项研究信号检查中，逐条复核完整、模型行完整、解析失败率合格、F1 高于关键词四项通过；检出 F1 和类别宏 F1 两项失败，故 `research_signal_gate.passed=false`。接入器在真实归档比较结果上已实测抛出 `ValueError`，没有生成 `wuhan_pre_event_agent_signals_v3`，有文本 Agent 回放不得作为已通过验证的实验运行。模型比关键词高出的检出 F1 为 0.219；所选 105 家公司的配对自助法 95% 描述区间为 0.090～0.356，但样本并非总体随机或未接触验证集，不能据此宣称外部泛化。

35 条模型误报按模型输出类别分为 `other` 24、`earnings` 7、`regulation` 3、`governance` 1。典型误报包括把股东户数和既有年报披露日标成事件、把“若获许可”当成已获批准、把储备项目和母公司意向书当成上市公司已落地项目，以及把产品可能适用于某领域当作已获订单。3 条漏报均是回复明确确认的线上销售或直播渠道建设。这个诊断说明当前提示更偏向覆盖率，在公司确认的新事项边界上缺乏精度；不能通过修改本批参考标签或降低门槛来放行。

## 可复核产物与后续边界

- 固定模型原始响应：`research_outputs/wuhan_pre_event_model_deepseek_v3/`；原始 JSONL SHA-256 `a7761fbe3f47f7cf0ffa9036da4a136e125915306839e0129be76804d5df9261`。
- 标准化与模型审计：`research_outputs/wuhan_pre_event_predictions_v3/`、`research_outputs/wuhan_pre_event_model_audit_v3.json`。
- 逐条复核工作文件、105 条决定和固化结果：`research_outputs/wuhan_pre_event_review_v3/`、`research_outputs/wuhan_pre_event_assistant_review_v3/`；决定 JSONL SHA-256 `ea155a5cfae4ac8b5b0ba5a5abfa92796ef881f50e2517d960f49023aa3a8ae5`。
- 冻结评分：`research_outputs/wuhan_pre_event_comparison_v3/`；比较结果 JSON SHA-256 `41af8ac3c8ac8cadbf76cb4521430ae1b006f3053b02d46ccda61dd053a6fd12`。清单绑定输入、代码与产物哈希。

若改进提示或公司事件判定器，本批 105 条已被用于错误分析，应明确改作**开发集**。下一次“通过门槛”的判断需要事先冻结新协议，并在不重叠的、事前确定的公司样本上重新运行与逐条复核；不应在这 105 条上反复调规则后把同批分数称为外部验证。即使独立样本通过，Agent 的资金、背景交易与价格反馈参数仍未按真实订单校准，受控消融不等于现实投资者预测或政策因果效应。

独立验证来源池现已先行冻结：[126 家配置](configs/wuhan_qna_independent_validation_universe_2020.json)使用同一事前提问公司框、相同截止时间，取种子 `marketmirror-wuhan-pit-independent-validation-v1-000038`。这是按数字从 000000 起搜索所得**首个与原 126 家零重叠**的哈希样本；搜索只读取事前公司代码和原样本成员，不使用回复文本、模型预测或标签。来源包 `research_outputs/wuhan_pre_event_validation_source_2020_v1/` 有 102 条事前确认回复、24 家空文本公司，实验 ID `e28bce159b5a1b0273016dccb5bbec300d8c3bd222c0fa0e9cfcf21885310738`。[独立来源审计](semantic/audit_wuhan_validation_source.py)重新从 SQLite 构造全部 2,599 家框并验证首个无交集种子、来源与代码哈希、两批公司/问答/可见文本零重叠，产物在 `research_outputs/wuhan_pre_event_validation_source_2020_v1_audit/`。该来源包沿用旧 v2 提示的构建器，只用于冻结公司与文本；新版 v3 协议现已在开发集固定，**尚未在独立批次的回复上运行或评分**。

针对上述误报的[公司事件协议 v3 开发记录](WUHAN_COMPANY_PROTOCOL_V3_DEV.md)现已冻结提示与开发输入，并通过 105 条模拟响应的端到端管线检查。[独立 102 条回复](WUHAN_INDEPENDENT_VALIDATION_V3.md)已在模型输出前完成逐条单一 AI 盲复核；实际 v3 模型尚未运行，因此没有独立模型评分。
