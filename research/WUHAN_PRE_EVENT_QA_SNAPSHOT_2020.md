# 武汉通告前公司问答样本

## 样本构造

主样本只用截至北京时间 2020-01-22 23:59:59.999999 已出现的提问定义公司范围。2020 年问答来源中有 20,440 条符合时间与代码条件的提问，来自 2,599 家公司；其中 2,060 家截至截点至少有一条已确认回复，共 14,297 条。之后用固定种子 `marketmirror-wuhan-pit-qna-active-cohort-v1` 对 2,599 家提问活跃公司按 SHA-256 排序，选取 126 家；抽样规则不读取未来回复、行情或事件结果。

在该 126 家样本中有 1,010 条事前提问，755 条回复在截点前确认可用。输入包按每家公司最新的事前可见完整问答生成 105 条条目；另有 21 家只作为事前提问活跃样本保留，但没有可用确认回复，不生成文本信号。条目可用时刻取回复可用时间，未来回复不回填。

此方法取代了先前用下半年 126 公司组合做武汉快照的方案。旧方案只覆盖 91 家，且股票池在事件之后确定；结果留作选择偏差诊断，不再作为主样本。

## 可复现性

固定股票代码见[事前问答活跃公司样本配置](configs/wuhan_qna_active_universe_2020.json)，其中记录了来源哈希、抽样种子、截止时点和抽样规则。生成器会重新从统一问答库提取截至时点的公司集合、按固定哈希排序，并拒绝与配置代码不一致的样本；数据包清单还绑定原始 Excel、SQLite、事件配置、样本配置、代码和输出哈希。

```powershell
python -m research.semantic.event_snapshot research/configs/wuhan_pre_event_pit_snapshot_2020.json `
  --output-dir research_outputs/wuhan_pre_event_pit_snapshot_2020_v3
python -m research.semantic.keyword_baseline research_outputs/wuhan_pre_event_pit_snapshot_2020_v3 `
  --output-dir research_outputs/wuhan_pre_event_pit_keywords_2020_v3
python -m unittest tests.test_event_snapshot -v
```

当前冻结的数据包实验 ID：`7ca4dbad0b33e1eba26235198c01c80220f97a3a1b0909d47571205109217690`。它固定网关 `http://aigw.dlut.edu.cn/v1`、模型 `DeepSeek-V4-Flash-0731-W8A8`、提示 `semantic-prompt-v2` 和温度 `0`，并将提示文件哈希纳入输入清单。105 条原文保存在 Git 忽略的本机 `research_outputs/`，不会提交。关键词基线覆盖 105 条，给出 47 个字面候选和 58 条空预测；它没有方向或语义验证。

## 已执行的模型与复核链路

运行时临时提供 `MARKETMIRROR_LLM_API_KEY`；密钥未写入代码、配置或实验产物。运行器在发送原文前核对数据包冻结的网关、模型、v2 提示、提示哈希和温度，参数不符即停止。105/105 条请求已完成、无请求失败；标准化有 1 条引文无法在可见原文定位，按解析错误保留。完整复核和评分见[武汉语义门槛结果](WUHAN_PRE_EVENT_SEMANTIC_GATE_2020.md)。以下命令记录本次可复现顺序，重新运行须使用新空目录：

```powershell
python -m research.semantic.run_model research_outputs/wuhan_pre_event_pit_snapshot_2020_v3 `
  --output-dir research_outputs/wuhan_pre_event_model_deepseek_v3 `
  --base-url http://aigw.dlut.edu.cn/v1 `
  --model DeepSeek-V4-Flash-0731-W8A8 --temperature 0 `
  --prompt-version semantic-prompt-v2
python -m research.semantic.parse_model_outputs research_outputs/wuhan_pre_event_pit_snapshot_2020_v3 `
  --raw research_outputs/wuhan_pre_event_model_deepseek_v3/model_raw_outputs.jsonl `
  --output-dir research_outputs/wuhan_pre_event_predictions_v3
python -m research.semantic.audit_model_run research_outputs/wuhan_pre_event_pit_snapshot_2020_v3 `
  research_outputs/wuhan_pre_event_model_deepseek_v3 `
  --normalized-dir research_outputs/wuhan_pre_event_predictions_v3 `
  --output research_outputs/wuhan_pre_event_model_audit_v3.json
```

助手已逐条复核全部 105 条，检查事件类型、方向、回复证据、否认语境和时间可见性，并为每条写明具体理由；问题中的猜测没有升级为公司确认事件。复核决定在 `research_outputs/wuhan_pre_event_review_v3/decisions.jsonl`，已由 `research.semantic.assistant_review` 固化。之后按固定门槛与关键词比较，结果**未通过**：模型检出 F1 0.548，类别宏 F1 0.318。下列前两项命令已执行；最后的适配器命令仅为门槛通过时的原定步骤，本次没有执行，接入器函数已实测拒绝生成信号：

```powershell
python -m research.semantic.assistant_review research_outputs/wuhan_pre_event_pit_snapshot_2020_v3 `
  --decisions research_outputs/wuhan_pre_event_review_v3/decisions.jsonl `
  --output-dir research_outputs/wuhan_pre_event_assistant_review_v3
python -m research.semantic.compare_holdout research_outputs/wuhan_pre_event_pit_snapshot_2020_v3 `
  --ai-review-dir research_outputs/wuhan_pre_event_assistant_review_v3 `
  --raw-model-dir research_outputs/wuhan_pre_event_model_deepseek_v3 `
  --normalized-model-dir research_outputs/wuhan_pre_event_predictions_v3 `
  --keyword-dir research_outputs/wuhan_pre_event_pit_keywords_2020_v3 `
  --output-dir research_outputs/wuhan_pre_event_comparison_v3
# 未执行：当前门槛失败，适配器会拒绝
python -m research.semantic.agent_signal_adapter research_outputs/wuhan_pre_event_pit_snapshot_2020_v3 `
  research_outputs/wuhan_pre_event_predictions_v3/model_predictions.jsonl `
  research_outputs/wuhan_pre_event_comparison_v3 `
  --output-dir research_outputs/wuhan_pre_event_agent_signals_v3
```

原计划门槛通过后，按[固定武汉语义回放配置](configs/wuhan_pre_event_pit_semantic_replay_2020.json)运行 126 家公司的配对路径。当前门槛失败，**不得把下面的语义回放命令当成已执行或可放行路径**。配置仍保留完整 126 家；21 家无回复公司的空文本约束已做合成测试，待独立验证通过后再运行：

```powershell
python -m research.simulation.semantic_historical_replay `
  research/configs/wuhan_pre_event_pit_semantic_replay_2020.json `
  --output-dir research_outputs/wuhan_pit_semantic_replay_2020_v1
```

这一步设计为历史收益上的价格接受型单股票消融，尚不共享现金或在公司间撮合；目前只有无文本路径及独立的[回复关键词机制对照](WUHAN_PRE_EVENT_KEYWORD_PORTFOLIO_2020.md)实际运行。不能用关键词机制路径代替未通过门槛的语义信号。

助手复核是单一 AI 参考；评分表达与该参考的一致程度，不是独立人工准确率。当前模型运行及逐条复核已完成，但适配器门槛失败，故语义 Agent 信号与有文本回放没有执行。即使未来适配器通过，也只表示可以做受控机制比较，不证明投资者行为已校准，更不构成预测、因果或监管预警证据。

## 解释边界与下一步

- 这是“截止日前出现过投资者提问”的公司队列，不是全部上市公司总体；问答来源可能遗漏低关注公司。
- 哈希抽样是可复现且不看结果的样本选择，但并非事前预注册；总体外推仍不成立。
- 来源时间只是可用性代理，没有首次公开或修订日志；它不能证明市场恰在该时刻看到文本。
- 当前 v3 输入包已冻结 DeepSeek 模型、v2 提示和哈希；105 条预测与助手逐条复核均已完成。固定评分的两项关键检查失败，见[完整门槛报告](WUHAN_PRE_EVENT_SEMANTIC_GATE_2020.md)。
- 同一组 126 家公司的行情基线现已完成：固定 120 日估计窗下 123 家有可用 CAR，3 家因晚近 IPO 不能形成全样本估计均值；无文本规则回放覆盖 126 家的 35 个共同交易日。详情和时点/行情口径限制见[武汉样本行情基线](WUHAN_PRE_EVENT_MARKET_BASELINE_2020.md)。
- 当前已有这批问答的 DeepSeek 预测和逐条参考标签，但**没有通过门槛的文本 Agent 信号**；适配器已实测拒绝接入。新协议须在不重叠的事前样本上验证，不能调低现有门槛。Agent 参数和市场行为仍未校准。

后续独立样本的模型预测仍须逐条由助手按原文和精确证据复核；AI 一致性分数不能称为独立人工准确率。即使未来仿真通过，也只支持受控机制比较，不构成预测能力、因果效应或监管预警有效性的证据。
