# 语义证据定位协议 v2 实验

运行日期：2026-09-27。使用同一份 2020 上半年 128 条互动问答样本、同一网关、请求模型 `DeepSeek-V4-Flash-0731-W8A8` 和温度 0，分别独立调用 `semantic-prompt-v1` 与 `semantic-prompt-v2`。这批样本已用于发现并改进协议，因此下列对照属于开发实验，不是未接触的最终测试。

v1 要求模型同时输出原文引用和字符索引。v2 只要求逐字引用，由本地程序在该条目当时可见的 `question` 或 `reply` 中寻找**唯一精确匹配**，再计算 Python 字符位置。引用不存在、重复出现、来源不可见或事件结构错误时，整条结果保留为解析失败；不会做模糊匹配、改写引用或把失败当作空事件。标准化后的输出仍使用统一的 `evidence_spans` 合同。

## 对照结果

| 提示版本 | 原始响应 | 结构与证据通过 | 失败 | 有效空事件条目 | 有效事件条目 | 有效事件数 |
|---|---:|---:|---:|---:|---:|---:|
| v1，按补充边界检查重验 | 128 | 94 | 34 | 94 | 0 | 0 |
| v2，唯一逐字引用 | 128 | 127 | 1 | 88 | 39 | 48 |

v2 唯一失败是引用在可见原文中重复，无法确定所指位置。提问阶段 64 条全部通过，其中 3 条有事件；回复阶段 64 条中 63 条通过、1 条失败，36 条有事件。相同条目之间的状态转换为：有效空→有效空 87 条、有效空→有效事件 7 条、失败→有效空 1 条、失败→有效事件 32 条、失败→失败 1 条。

v1 最初的标准化器报告 95 条通过、33 条失败。复核发现其中一条证据 `end` 超过原文末尾，Python 切片自动截断后误判匹配。边界检查已补充；旧原始响应不变，在新的 `semantic_model_deepseek_v1_revalidated/` 目录重新标准化为 94 条通过、34 条失败。旧计数仅作为历史运行记录，当前对照和工作台均使用重验结果。

**通过格式与证据检查并不证明事件判断正确。** v2 输出更多事件，可能只是协议允许更多模型判断落地，也可能含有误报；没有独立人工裁定标签，不能计算准确率、召回率或增量预测效果。`intensity` 与 `uncertainty` 仍是未校准判断，不作为交易概率。模型服务端的实际权重版本未独立核实，独立调用即使温度相同也可能产生不同文本。现有样本是分层抽样，事件比例不代表全市场发生率。

## 可复核产物

原文、原始模型响应和标准化结果均在 Git 忽略的本地 `research_outputs/` 下：

- `semantic_model_deepseek_v1/`：保留旧版原始响应与运行清单。
- `semantic_model_deepseek_v1_revalidated/`：旧原始响应按新边界检查重新标准化。
- `semantic_model_deepseek_v2/`：新提示的 128 条原始响应，最终请求失败 0。
- `semantic_model_deepseek_v2_normalized/`：新协议标准化结果。
- `semantic_model_deepseek_v2_diagnostics/`：脱敏覆盖与失败类别统计。
- `semantic_protocol_v1_v2_comparison/`：两版逐条状态转换、审计及输入/代码哈希。

v2 原始响应 SHA-256：`4a08a95fc9a0ec206d1932cf0d34ef7cd1cf45930dc9d9dc33d9341b563f4dc7`。对照器会先审计两版的来源绑定、提示哈希、逐条标准化结果与清单，再输出不含原文的摘要。

```powershell
python -m research.semantic.compare_model_runs research_outputs/semantic_annotation_pilot_2020 `
  research_outputs/semantic_model_deepseek_v1 research_outputs/semantic_model_deepseek_v1_revalidated `
  research_outputs/semantic_model_deepseek_v2 research_outputs/semantic_model_deepseek_v2_normalized `
  --output-dir <新的本地对照目录>
```

下一研究门槛是由两名独立审核者完成标注和裁定，再在**新的、预先固定的留出样本**上评估语义正确性。2018 政策节点尚没有相应时点的已核实文本信号；三类 Agent 尚无投资者交易证据校准。本次 v2 输出不能直接证明高保真市场仿真或监管预警能力。
