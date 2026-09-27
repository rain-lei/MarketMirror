# 2020 下半年语义留出包：首次固定模型运行

本报告记录[运行前固定的留出协议](SEMANTIC_HOLDOUT_H2_2020.md)之后的首次模型结果；不修改该协议的样本、提示、模型或评分门槛。2026-09-27 对全部 128 条留出问答调用指定网关的 `DeepSeek-V4-Flash-0731-W8A8`，温度 `0`，提示 `semantic-prompt-v2`。运行前核对提示 SHA-256 为配置冻结值 `cb608b2b36cab16653cc74afcc391e24356655b1661845833242c45e66bb3892`；来源包和模型 ID、提示版本、温度、网关地址均在原始运行清单中保存。原始响应与问答文本只在 Git 忽略的本地 `research_outputs/`，API 密钥未写入运行清单、报告或仓库。

| 检查 | 首次结果 |
|---|---:|
| 固定留出包 | 128 条，126 家公司 |
| 原始模型响应 | 128/128 |
| 网关请求失败 | 0 |
| 确定性结构及逐字证据校验通过 | 128/128 |
| 有效空事件响应 | 80 |
| 有效含事件响应 | 48，合计 57 个事件 |
| 原始响应提出的唯一精确引用 | 92 个引用跨度 |
| 独立双人审核和裁定 | 0/128 |

审计器逐条重新从原始响应标准化，核对样本身份和文本哈希、提示、模型及产物哈希；原始/标准化覆盖均完整，解析失败为 0。完整性目录 `configs/integrity_catalog_semantic_holdout_model_h2_2020.json` 固定原始、标准化和诊断三份清单，核验为 `3/3`。通用目录核对清单/代码/输出字节；输入之间的来源绑定另由 `semantic/audit_model_run.py` 独立检查。远端服务的实际权重版本未能独立核实，另一次 API 调用不保证逐字节相同。

本地文件：

- `research_outputs/semantic_holdout_h2_2020_model/model_run_manifest.json` 与 `model_raw_outputs.jsonl`：原始响应及检查点。
- `research_outputs/semantic_holdout_h2_2020_normalized/normalization_manifest.json` 与 `model_predictions.jsonl`：确定性标准化结果。
- `research_outputs/semantic_holdout_h2_2020_diagnostics/diagnostics.md`：不含问答原文的覆盖与失败诊断。

128/128 **只表示请求、结构和引用校验通过**，不能解释为 100% 语义正确。80 条空事件可能漏检，57 个事件的类别、方向和事实判断均未有人审金标准。样本按话题分层，事件数不能估计全市场发生率；提示开发样本与留出样本来自同一 2020 问答来源，也不能据此宣称跨年份泛化。预设的事件检出 F1、类别 F1 和关键词比较仍不可计算，因此研究性 Agent 接入门槛**尚无法判定，也不得视为通过**；模型输出未进入交易决策或监管预警。

下一步需要两名独立审核者分别完成 `research_outputs/semantic_holdout_h2_2020_interface/reviewer_a.html` 和 `reviewer_b.html` 的 128 条盲审，再由第三人逐条裁定、负责人抽查。形成金标准后，按运行前协议执行 `semantic/compare_holdout.py`，如实报告模型与固定关键词基线的指标及不确定性。不能用本次预测帮助标注者裁定。
