# 2020 下半年留出包：人工盲审交接

> 历史记录：2026-09-27 用户已取消双人审核和第三人裁定。当前采用 [AI 逐条复核流程](AI_REVIEW_H2_2020.md)，以下原协议状态不再是当前接入条件。

目前固定留出包有 128 条、126 家公司；指定模型的 128 条响应已经归档，但**不得交给审核者 A、B**。两人的空白任务页面已在本机生成，并在浏览器中检查了 A/B 不同任务顺序、提问阶段仅见提问、回复阶段见已可见回复、逐条导航与未开始计数。新增的[审核准备报告](../research_outputs/semantic_holdout_h2_2020_readiness_v2/review_readiness_report.md)又核对了来源绑定、256 条空白标签行、页面载荷脱敏和 SHA-256；该检查没有产生任何人工标签，当前完成数仍为 0/128。

两位独立审核者分别只接收以下一个本地页面，不互换页面或草稿：

- A：`research_outputs/semantic_holdout_h2_2020_interface/reviewer_a.html`
- B：`research_outputs/semantic_holdout_h2_2020_interface/reviewer_b.html`

页面可在本机浏览器直接打开。每人填写与对方不同的审核者 ID，对全部 128 条决定 `已完成`（允许 `events: []`）或 `待复核`。只依据页面显示的当时可见问答；不看模型输出、另一位的答案、未来收益或公司级违规标签。事件的类别、方向、强度和逐字证据规则见[标注说明](semantic/ANNOTATION_GUIDE.md)。定期点击“下载当前标签 JSONL”，离开页面前再下载一次；页面不会把草稿上传或自动保存到服务器。草稿仅在本地保存，不提交 Git。

两人都提交最终 JSONL 后，由研究执行者从仓库根目录运行（`<A最终文件>`、`<B最终文件>` 指各自实际导出的文件；输出目录必须是新的空目录）：

```powershell
python -m research.semantic.review_workflow compare research_outputs/semantic_holdout_h2_2020 --reviewer-a <A最终文件> --reviewer-b <B最终文件> --output-dir research_outputs/semantic_holdout_h2_2020_review_comparison
```

比较器先核对两份文件的样本身份、逐字证据、审核者 ID 和覆盖数，再报告一致率、分歧与待审项。若未达到双人 128/128 完成，先由原审核者各自补齐，重新比较到新的空目录；不能用模型预测补空白。第三位裁定者在双方交卷以后才查看分歧和各自答案。复制比较目录的 `adjudication_template.jsonl` 到单独文件，逐项填写 128 条裁定、使用与 A/B 不同的裁定者 ID，并为每条分歧写明理由。一致项也须显式签署，负责人随后抽查语义及证据。

裁定完成后运行：

```powershell
python -m research.semantic.review_workflow finalize research_outputs/semantic_holdout_h2_2020 --comparison-dir research_outputs/semantic_holdout_h2_2020_review_comparison --final-labels <裁定文件> --output-dir research_outputs/semantic_holdout_h2_2020_gold
python -m research.semantic.compare_holdout research_outputs/semantic_holdout_h2_2020 --comparison-dir research_outputs/semantic_holdout_h2_2020_review_comparison --gold-dir research_outputs/semantic_holdout_h2_2020_gold --raw-model-dir research_outputs/semantic_holdout_h2_2020_model --normalized-model-dir research_outputs/semantic_holdout_h2_2020_normalized --keyword-dir research_outputs/semantic_holdout_h2_2020_keyword --output-dir research_outputs/semantic_holdout_h2_2020_scored
```

只有独立双人审核、第三人裁定与固定模型/关键词比较均完成后，才能报告留出语义准确率及预先规定的研究性接入门槛。格式与身份字段的自动检查不能证明审核者真实独立，也不能代替负责人对事件含义的抽查。现有模型运行的覆盖统计见[留出模型报告](SEMANTIC_HOLDOUT_MODEL_H2_2020.md)。
