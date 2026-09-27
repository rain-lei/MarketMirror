# 2020 下半年语义留出实验（运行前协议）

本协议固定于 2026-09-27，早于本留出包的人工标签和模型输出。目标是检验已经在 2020 上半年开发样本上确定的文本抽取方案，而不是重新寻找更合适的提示或展示案例。128 条上半年样本已用于 v1/v2 开发，不能再算独立测试。

## 留出样本与现有状态

配置为 `configs/semantic_holdout_h2_2020.json`。从同一来源的 2020-07-01 至 2020-12-31 可见问答中，先排除开发包的全部公司、问答 ID 和完全相同的可见文本上下文，再按固定种子、question/reply 阶段和八个字面话题层抽样。选中 128 条（提问 64、回复 64），涉及 126 家公司；实际可见日期为 2020-07-03 至 2020-12-28。与开发包的股票代码、问答 ID、完整可见上下文哈希交集均为零。`train/validation/test` 是沿用抽样器的公司分组名称，这 128 条**全部**是相对上半年开发包的外部留出，不用其中任何一组再次调提示。

抽样前的候选提问行有 163,883 条，按公司或问答身份排除 11,377 条，剩余 152,506 条。选样刻意覆盖较少的话题，不用于估计总体事件发生率；按公司和完整文本排重也不能保证不同公司间不存在语义相似的披露。来源时间是可见时刻的保守代理，尚未独立核对网页发布日志。

配置固定原始 Excel 的 SHA-256 为 `a3a1b0afa2cd13b25a56c89393178d93960cd8867a46a7c469fd567bf82ac812`，提示 `semantic-prompt-v2` 的 SHA-256 为 `cb608b2b36cab16653cc74afcc391e24356655b1661845833242c45e66bb3892`；模型为 `DeepSeek-V4-Flash-0731-W8A8`。本地标注包和关键词对照各有 128 条，两份离线盲审页面已生成。完整性目录 `configs/integrity_catalog_semantic_holdout_h2_2020.json` 对这三项产物核验为 3/3。**目前 0 条人工审定标签、0 条留出模型输出，不存在留出准确率。**

## 固定流程

1. 两名审核者分别使用 `research_outputs/semantic_holdout_h2_2020_interface/reviewer_a.html` 和 `reviewer_b.html`，只看当时可见原文，不看模型预测、对方答案或未来收益。各自完成 128 条；比较一致性并保留分歧，由第三人逐项签署裁定。负责人抽查事件含义和逐字证据。标签格式、事件类别、方向含义及裁定要求见 `semantic/ANNOTATION_GUIDE.md`。
2. 对完整的同一留出包调用固定 v2 提示、固定模型、温度 0，要求 128 条全覆盖；保留每条原始响应、失败和版本哈希。请求失败或结构解析失败计入错误，不能静默删样本。模型结果不供审核者参考。
3. 在金标准形成后，按 `semantic/signal_validation.py` 的既定评分器比较 v2 与固定关键词基线。主要指标为事件有无的 precision/recall/F1 和各事件类别 F1；报告各类别支持数。辅助指标为类型匹配的单事件条目上的方向准确率与证据精确跨度召回，同时列出该子集的分母。多事件对齐、强度校准和行业评分目前没有有效指标，不作准确性声明。
4. 预设的**研究性接入门槛**：双人全量审核并裁定、模型 128 条全覆盖、解析失败不超过 5%、事件检出 F1 至少 0.70 且不低于关键词基线、支持类别的宏平均 F1 至少 0.60。通过只允许做受控 Agent 信号消融实验，不代表真实交易行为已校准或具有监管预测能力。每项必须同时报告分子、分母和不确定性；样本不足时不强行宣称达标。

若在查看留出结果后更改提示、类别规则或门槛，这个样本立即转为开发样本；新的最终评估必须重新选择未接触、独立的公司与时间范围。

## 可复现命令

以下命令在仓库根目录运行，输出目录须为空，原始文本和审核材料始终留在 Git 忽略的 `research_outputs/`：

```powershell
python -m research.semantic.holdout_pack research/configs/semantic_holdout_h2_2020.json --output-dir research_outputs/semantic_holdout_h2_2020
python -m research.semantic.keyword_baseline research_outputs/semantic_holdout_h2_2020 --output-dir research_outputs/semantic_holdout_h2_2020_keyword
python -m research.semantic.review_workflow prepare research_outputs/semantic_holdout_h2_2020 --output-dir research_outputs/semantic_holdout_h2_2020_review
python -m research.semantic.review_interface research_outputs/semantic_holdout_h2_2020 research_outputs/semantic_holdout_h2_2020_review --output-dir research_outputs/semantic_holdout_h2_2020_interface
python -m research.registry.verify_catalog research/configs/integrity_catalog_semantic_holdout_h2_2020.json --output-dir research_outputs/integrity_catalog_semantic_holdout_h2_2020_v2
```

之后由运行者在本机安全地设置 `MARKETMIRROR_LLM_API_KEY`，不写入命令行、代码、报告或 Git。模型运行、双人裁定和评分均尚未执行；各步需要新输出目录，不能覆盖固定材料。
