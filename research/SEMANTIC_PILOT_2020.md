# DeepSeek 真实语义抽取初测

后续复核说明：本页保留初次运行及旧标准化器的历史计数。补充原文末尾边界检查后，v1 原始响应在新目录重验为 **94 条通过、34 条失败、0 条有效事件**。旧版唯一通过的事件包含超出原文末尾的索引，曾被 Python 切片截断而误判为匹配。原始响应没有改动；现行结果及 v2 协议对照见 [协议改进说明](SEMANTIC_PROTOCOL_V2.md)。

运行日期：2026-09-27。网关为用户指定的 `http://aigw.dlut.edu.cn/v1`，请求模型为 `DeepSeek-V4-Flash-0731-W8A8`，温度为 0，提示版本为 `semantic-prompt-v1`。模型名称来自网关调用配置，没有独立核验远端权重版本；固定温度不保证远端重跑得到相同内容。

## 样本与运行

样本包包含 2020 上半年 128 条企业互动问答可见文本，涉及 123 家公司；提问、回复各 64 条。它不是监管机构政策文本集。提问阶段没有未来回复，回复阶段仅包含截至该阶段可见的原文。样本按公司分组及八个字面话题层抽取，不能用输出频次估计全市场事件发生率。

包 ID：`7c4c27c371b98f5002e3ca4694d8becc058b1b78f44230e6f4493686207b4ecf`。

先试跑 2 条，两条通过格式与证据校验；随后在同目录恢复完整运行。初次全量运行有 3 条请求失败，单独补跑后最终 128 条均取得模型文本。原始响应文件 SHA-256 为 `182d79a57a3748bf860147e4f727e9c061b17f5413524365fbeac47e8d08b777`。网关密钥没有写入配置、响应或版本控制。

## 严格校验结果

| 项目 | 条数 |
|---|---:|
| 取得原始响应 | 128 |
| 最终请求失败 | 0 |
| 结构与证据校验通过 | 95 |
| 通过且为空事件 | 94 |
| 通过且包含事件 | 1 |
| 解析失败 | 33 |

失败中，31 条为证据文本与索引不匹配，1 条为证据来源/位置不合法，1 条为 JSON 语法错误。解析器保留错误记录，失败条目不当作有效空事件，也没有修补模型输出。

| 可见阶段 | 样本 | 通过 | 失败 | 通过且有事件 |
|---|---:|---:|---:|---:|
| 提问 | 64 | 60 | 4 | 0 |
| 回复 | 64 | 35 | 29 | 1 |

原始响应提出的证据中，76 个跨度的引用文本能在可见原文中唯一找到，但字符索引不正确；另有 2 个索引正确的跨度、2 个引用重复且索引错误的跨度、1 个来源/引用不合法的跨度。这按提出的跨度计数，包含已整条拒绝的事件，不能与有效事件数相加。

这次运行说明当前协议让模型计算精确字符位置存在明显失败。下一版应保留原始引用，让本地程序按独立版本化规则定位，并拒绝不存在或无法消除歧义的引用；不得把它当作对本次失败结果的无记录修复。

## 能证明什么

本次证明指定网关能实际返回文本，原始输出能绑定样本与提示版本，解析失败能够被识别并保留。它没有证明事件抽取准确率、市场预测增益或三类投资者的真实决策。

人工双人审核与裁定仍为 0。94 条有效空响应是否漏检、事件方向是否正确，以及 `intensity` 和 `uncertainty` 是否有合理含义，都尚未评估。这些数值不是经校准的概率。当前通过的事件也没有接入 Agent 决策。

## 本地产物与复核

以下目录均被 Git 忽略，原文及原始模型响应只保存在本地：

- `research_outputs/semantic_model_deepseek_v1/`：原始响应和逐条检查点清单。
- `research_outputs/semantic_model_deepseek_v1_normalized/`：旧标准化器的历史结果；现行审计应使用 `semantic_model_deepseek_v1_revalidated/`。
- `research_outputs/semantic_model_deepseek_v1_diagnostics/`：脱敏 JSON/Markdown 诊断与清单。
- `research_outputs/workbench_2018_2020_llm/`：含实际请求及解析计数的研究工作台。

保存的响应可以离线重复标准化；远端再次调用属于新运行，不能承诺相同输出。复核命令：

```powershell
python -m research.semantic.audit_model_run research_outputs/semantic_annotation_pilot_2020 `
  research_outputs/semantic_model_deepseek_v1 `
  --normalized-dir research_outputs/semantic_model_deepseek_v1_revalidated

python -m research.semantic.diagnose_model research_outputs/semantic_annotation_pilot_2020 `
  research_outputs/semantic_model_deepseek_v1 research_outputs/semantic_model_deepseek_v1_revalidated `
  --output-dir <新的诊断目录>
```

改进提示和定位协议后应建立新版本及新运行目录，再固定方案开展独立人工审核和留出评价。当前样本已用于发现协议问题，不能将它宣称为未接触过的最终测试集。
