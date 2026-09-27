# 2020 下半年样本：AI 逐条复核与 Agent 接入

2026-09-27，按用户要求取消双人审核和第三人裁定，改为单一 AI 逐条复核。当前协议为 [assistant_review_v1](configs/assistant_review_policy.json)，具体判断规则见 [复核说明](semantic/ANNOTATION_GUIDE.md)。旧协议和空白双人审核包仅作历史记录，不再是当前实验的前置条件。

## 实际完成

重新阅读 128 条当时可见的文本，分别记录判断理由；确认 44 条含事件，共 54 个事件：经营与业绩 5、监管 15、治理 9、现金流与融资 5、其他明确事项 20。提问与回复各 64 条。每个事件都有回复原文的精确字符位置，全部条目通过来源哈希、完成状态、逐条理由和证据校验。

本次事件通道只使用公司确认的信息，因此投资者单方面猜测不作为已确认事件。问题阶段各条仍有单独说明；未来可另建情绪或关注主题通道。回复中的否认覆盖提问假设；例行股东人数和报告日期不直接作为经营变化；未商业化、产品适用性和股东卖出也不自动映射为经营损失或收益。

复核曾接触模型候选，不能称为独立人工金标准。本批材料在协议改变后用于探索性开发；以下分数是与本次 AI 参考的一致性。没有把协议变更包装成原预注册实验通过。

## 固定 DeepSeek 输出的评分

模型响应、v2 提示、标准化方法和关键词对照均沿用已冻结版本，未以复核标签替换待评分的模型输出。

| 指标 | 实际值 |
|---|---:|
| 完整复核 | 128 / 128 |
| 模型解析失败 | 0 / 128 |
| 模型事件检出 F1 | 0.8913 |
| 关键词事件检出 F1 | 0.5096 |
| 模型类别宏 F1（有支持类别） | 0.6593 |
| 类型匹配的单事件方向一致率 | 19 / 25 = 0.7600 |
| 事件检出 TP / FP / FN | 41 / 7 / 3 |

126 家公司的配对重抽样（5,000 次、种子 20260927）给出 F1 差值 +0.3818，描述性 95% 区间为 [0.2896, 0.4785]。话题分层抽样及单一 AI 参考的偏差不由该区间消除。

完整 AI 复核、模型全覆盖、解析错误率不超过 5%、检出 F1 至少 0.70、不低于关键词及类别宏 F1 至少 0.60，六项检查均通过。保留数值质量门槛，替换复核人员要求。

已生成 128 条 Agent 信号行，其中 16 条非零，其余为零信号。信号来自原始固定模型输出的数值映射，仍保留模型误差；复核参考用于评价，没有伪装成模型预测。信号文件与实际评分的预测文件哈希绑定，替换预测或改动参考会被拒绝。

## 本地产物与复现

以下目录均在 Git 忽略范围，原始问答、证据引用和逐条理由不会进入仓库：

- `research_outputs/assistant_review_decisions_h2_2020/`：逐条决策及生成记录。
- `research_outputs/semantic_h2_2020_assistant_review/`：参考标签、报告与来源清单。
- `research_outputs/semantic_h2_2020_ai_scored_v2/`：评分与输入、代码、结果哈希。
- `research_outputs/semantic_h2_2020_ai_signals_v2/`：Agent 信号与接入检查。
- `research_outputs/workbench_2018_2020_semantic_replay_v17/`：当前工作台与脱敏摘要。

每次运行使用新的空目录，保留已有证据：

```powershell
python -m research.semantic.assistant_review research_outputs/semantic_holdout_h2_2020 --decisions research_outputs/assistant_review_decisions_h2_2020/decisions.jsonl --output-dir <新复核目录>
python -m research.semantic.compare_holdout research_outputs/semantic_holdout_h2_2020 --ai-review-dir <新复核目录> --raw-model-dir research_outputs/semantic_holdout_h2_2020_model --normalized-model-dir research_outputs/semantic_holdout_h2_2020_normalized --keyword-dir research_outputs/semantic_holdout_h2_2020_keyword --output-dir <新评分目录>
python -m research.semantic.agent_signal_adapter research_outputs/semantic_holdout_h2_2020 research_outputs/semantic_holdout_h2_2020_normalized/model_predictions.jsonl <新评分目录> --output-dir <新信号目录>
```

后续已补齐这批 126 家公司的同期行情并完成有文本/无文本真实收益回放，详见 [回放记录](SEMANTIC_REPLAY_H2_2020.md)。原三股行情（000001、000002、600519）与本批公司没有重叠，本次使用各公司自己的行情。投资者行为校准与监管预测验证仍未完成。

验证结果：161 项测试全部通过，包含 AI 复核到评分到信号的端到端链路、参考改动拒绝和评分后预测替换拒绝；最终工作台与真实信号流的来源核验通过。

后续接入修正：当前适配器 v4 与 `semantic_h2_2020_ai_signals_v5/` 在固定模型输出上压制 4 个提问事件及 2 个没有回复证据的事件，并使有效空事件不施加不确定性惩罚。当前保留 15 条方向信号及 29 条仅事件不确定性记录。上文 16 条非零信号对应初次适配的历史结果；原模型评分未替换，接入修正和结果见 [真实回放记录](SEMANTIC_REPLAY_H2_2020.md)。
