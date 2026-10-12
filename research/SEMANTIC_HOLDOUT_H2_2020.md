# 2020 下半年语义留出实验（运行前协议）

本文件记录固定样本设计；复核与评分采用 [AI 参考流程](AI_REVIEW_H2_2020.md)。

本协议固定于 2026-09-27，早于本留出包的人工标签和模型输出。目标是检验已经在 2020 上半年开发样本上确定的文本抽取方案，而不是重新寻找更合适的提示或展示案例。128 条上半年样本已用于 v1/v2 开发，不能再算独立测试。

## 留出样本与现有状态

配置为 `configs/semantic_holdout_h2_2020.json`。从同一来源的 2020-07-01 至 2020-12-31 可见问答中，先排除开发包的全部公司、问答 ID 和完全相同的可见文本上下文，再按固定种子、question/reply 阶段和八个字面话题层抽样。选中 128 条（提问 64、回复 64），涉及 126 家公司；实际可见日期为 2020-07-03 至 2020-12-28。与开发包的股票代码、问答 ID、完整可见上下文哈希交集均为零。`train/validation/test` 是沿用抽样器的公司分组名称，这 128 条相对上半年开发包按来源身份隔离。复核协议变更后作为探索性样本，不用其中任何一组再次调提示并声称留出验证。

抽样前的候选提问行有 163,883 条，按公司或问答身份排除 11,377 条，剩余 152,506 条。选样刻意覆盖较少的话题，不用于估计总体事件发生率；按公司和完整文本排重也不能保证不同公司间不存在语义相似的披露。来源时间是可见时刻的保守代理，尚未独立核对网页发布日志。

配置固定原始 Excel 的 SHA-256 为 `a3a1b0afa2cd13b25a56c89393178d93960cd8867a46a7c469fd567bf82ac812`，提示 `semantic-prompt-v2` 的 SHA-256 为 `cb608b2b36cab16653cc74afcc391e24356655b1661845833242c45e66bb3892`；网关为 `http://aigw.dlut.edu.cn/v1`，模型为 `DeepSeek-V4-Flash-0731-W8A8`。本地标注包和关键词对照各有 128 条，两份离线盲审页面已生成。完整性目录 `configs/integrity_catalog_semantic_holdout_h2_2020.json` 对标注包、关键词对照、盲审页面和字面重叠审计核验为 4/4。**目前 0 条人工审定标签、0 条留出模型输出，不存在留出准确率。**

在人工标注和留出模型调用前，另用 Unicode NFKC、去标点空白、同来源段落字符三元组 Jaccard 检查开发包与留出包的字面近重复。128 条留出样本各自最近的开发条目中，最高相似度为 0.3637；达到 0.5 的为 0 条。审计结果、条目 ID 和输入/代码哈希留在 `research_outputs/semantic_holdout_h2_2020_overlap/`，不导出问答原文。此诊断不能识别语义改写、共同事实或模型预训练中的信息；也不改变已固定样本。

## 执行与评分

同一包的模型原始响应、确定性标准化、关键词对照及助手逐条参考分别归档。每条保留可见原文、来源、证据位置和理由，空事件也须审核。模型和关键词比较使用同一批完整样本，失败计入错误，不删除困难条目。

门槛为覆盖完整、解析失败不超过 5%、事件检出 F1 至少 0.70 且不低于关键词、支持类别宏 F1 至少 0.60。通过只允许对应协议的 Agent 对照，不证明真实交易行为或监管预测。单一 AI 参考且曾见模型候选，分数称为一致性；样本支持不足的类别和多事件匹配边界单列。

按公司配对重抽样采用种子 `20260927`、5,000 次和描述性百分位 95% 区间；不能修复抽样偏差。实际模型覆盖与标准化见[模型记录](SEMANTIC_HOLDOUT_MODEL_H2_2020.md)，复核、评分和信号命令见[AI 复核记录](AI_REVIEW_H2_2020.md)。

## 样本构建与重查

使用新输出目录，保留原始包：

```powershell
python -m research.semantic.holdout_pack research/configs/semantic_holdout_h2_2020.json --output-dir research_outputs/semantic_holdout_example
python -m research.semantic.keyword_baseline research_outputs/semantic_holdout_example --output-dir research_outputs/semantic_holdout_example_keyword
python -m research.semantic.holdout_overlap research_outputs/semantic_annotation_pilot_2020 research_outputs/semantic_holdout_example --output-dir research_outputs/semantic_holdout_example_overlap
```

重构包验证样本身份和来源，不自动产生新评估成绩。更改提示、类别规则或评分门槛须登记新任务和未查看样本，不能沿用已经消费的包重复调优后再称最终验证。
