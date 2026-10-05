# 武汉事前 v3：独立公司样本与模型前盲复核

更新日期：2026-09-29。开发样本上的旧提示有较多误报，因此先冻结[公司事件协议 v3](WUHAN_COMPANY_PROTOCOL_V3_DEV.md)，再从另 126 家与开发批次零重叠的公司派生 102 条事前可见回复。独立包 `research_outputs/wuhan_pre_event_independent_protocol_v3_v1/` 的实验 ID 为 `bffe747dc54024dffe32cb7a3c15ca208813a919e2b1d5befa2e376998e1425b`，预检绑定原 Excel、SQLite、公司样本、原文、提示和构建代码哈希。来源审计已验证公司代码、问答 ID 和可见文本哈希均与开发批次零重叠。

在本批 v3 模型尚无运行清单时，助手逐条阅读了完整提问和公司回复，只把回复亲自确认的公司事项作为参考事件；每条有单独理由，事件须有回复中的唯一精确引文和位置。专用[复核政策](configs/wuhan_validation_review_policy_v3.json)在复核前固定，同旧版信号门槛数值完全一致。用户要求由助手认真复核，不采用双人审核；本批是**对 v3 模型输出盲法的单一 AI 参考**，不是人工金标准。

102/102 条均完成复核：20 条含事件，20 个事件；`other` 13、`governance` 4、`earnings` 3，`regulation` 与 `liquidity` 无参考事件，因此后续有支持类别宏 F1 只覆盖前三类。明确排除了例行股东人数、旧财报解释、静态产品适用性、参股公司自身客户、提问中的传闻、未确定融资和没有可识别进度的宽泛表态。对确认事项，分别保留投产、近期投资、实际合作、可识别的研发试验进度、业绩初测、减值和具体项目中标。标签由[归档入口](semantic/wuhan_validation_review.py)验证全部 102 条理由与回复引文，并锁定输入、政策、代码与结果哈希；`research_outputs/wuhan_pre_event_validation_blind_review_v1/reference_labels.jsonl` 的 SHA-256 是 `d4b1127e21390abcbc84c21d01cceab8358a54544ce4e9fad40df44707d79637`。逐条原文和工作文件只在 Git 忽略的本地 `research_outputs/`。

不依赖模型的字面关键词对照在这份参考上检出 TP/FP/FN 为 11/34/9，精确率 0.244、召回率 0.550、F1 0.338；这是待比较的固定基线，不是 v3 结果。[独立评分入口](semantic/score_wuhan_v3_validation.py)已固定为同一 102 条样本、原始模型审计、逐条 AI 参考和原有检出/类别门槛，并再次核查公司来源不重叠以及复核归档早于模型运行。此前用 102 条**模拟空事件响应**验证了程序路径，模拟 F1 为 0；这不反映模型表现。

2026-09-29 已完成冻结的 v3 模型运行。首次请求有 14 条 HTTP 失败；从原始检查点恢复后，102/102 条均完成，最终请求失败与解析失败均为 0。独立审计确认 102 条覆盖完整、参考归档早于运行且证据只取自公司回复。模型事件检出 TP/FP/FN 为 14/9/6，精确率 0.609、召回率 0.700、F1 0.651；关键词基线 F1 为 0.338；支持类别宏 F1 为 0.611。七项门槛有六项通过，只有检出 F1 ≥ 0.70 未通过。原始响应 SHA-256 为 `78ab17188037cb833d1b8450bcbaa95d9abacc1aa25ea4a0911a13f5ada74ad8`，预测 SHA-256 为 `d815cc268d22d443f9d495e6e594532043f4a2f060d472e473cecd8be623ed31`，评分文件 SHA-256 为 `390a7a0758c6f7705ab52339e292aebd686d500ccefb3b035e9724851911cd90`。结果保存在 Git 忽略的 `research_outputs/wuhan_v3_validation_retry_20260929_002351/`。

**此次独立验证未达到放行门槛。** 接入器没有生成正式语义信号，也没有运行 84 对文本/无文本组合路径；之前的模拟端到端结果只证明程序流程可执行。该验证集现已使用，不能据此调提示词或阈值；后续改进只能在开发样本上进行，之后再建立新的未查看留出集。

同一 126 家公司已完成[行情来源与无文本仿真基线](WUHAN_VALIDATION_MARKET_BASELINE_2020.md)，供将来通过文本门槛后进行同池对照；基线不使用这批参考标签，也不能代替模型验证。

[独立 v3 信号接入器](semantic/wuhan_v3_signal_adapter.py)与[同池组合配对入口](simulation/wuhan_v3_portfolio_ablation.py)现已就绪。接入器先从来源重新计算独立评分，要求盲复核顺序、零重叠、完整覆盖、解析失败率、检出 F1、关键词对照及支持类别宏 F1 七项门槛全部通过；任何一项失败，均不会创建信号输出目录。组合入口再次核验信号、126 家公司身份和已归档无文本基线，让 102 家有回复公司按可见时间进入 Agent，24 家无可见回复公司保持零信号；两条路径逐篮子共用相同参数，每条无文本路径须与已归档基线精确一致，双路径账本逐日重建。用**程序依据 AI 参考构造的模拟模型响应**曾完整试跑 84 对路径、5,880 个双路径组合日，验证了门控、来源绑定和配对程序；试跑使用临时目录，未保存为正式结果，其数值不可解释为模型效果。

本地一键入口先核验冻结样本；API Key 保存在 Git 忽略的 `.env.local`，按 demo 约定以本地明文保存，后续运行自动读取，不再提示输入；更换密钥时使用 `--replace-api-key`。网关地址按 demo 配置为 `http://aigw.dlut.edu.cn/v1`，实验结果目录不包含密钥。

```powershell
$py = 'C:\ProgramData\miniconda3\python.exe'
& $py -m research.semantic.run_wuhan_v3_validation
```

只有主动更换本机凭据时才需要重新遮蔽输入：

```powershell
& $py -m research.semantic.run_wuhan_v3_validation --replace-api-key
```

入口会把输出保存到带时间戳的 `research_outputs/wuhan_v3_validation_*` 目录，依次完成模型调用、标准化、独立评分与来源审计。评分门槛失败时，保留原始结果和评分，但不创建信号与组合结果；通过后，自动生成信号并与冻结无文本基线进行配对仿真及账本复核。

```powershell
# 将路径替换为首次启动打印的 output_root。
& $py -m research.semantic.run_wuhan_v3_validation --output-root 'C:\完整路径\research_outputs\wuhan_v3_validation_时间戳' --resume
```

只有模型请求阶段中断时才用 `--resume`；它会重核已提交响应、来源与冻结模型参数，并重试失败项。即使以后在该 AI 参考上通过门槛，结论也只允许说“独立公司样本上的文本抽取一致性达标”，不能称为人工标注准确率、收益预测有效性或监管风险预警已经验证。
