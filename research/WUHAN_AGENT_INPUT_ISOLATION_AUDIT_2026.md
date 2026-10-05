# 武汉事前问答进入 Agent 的实际输入核验

**结论：当前可执行的公司回复关键词机制实验没有使用事后回复、投资者提问命中、来源季度或未核实财务字段作为 Agent 文本信号。模型语义信号仍未放行。** 这只证明现有受控机制实验的输入边界，不证明 DeepSeek 预测能力或真实市场校准。

从原统一 SQLite 独立重建 2020-01-22 23:59:59.999999（中国时间）的来源截面：事前提问涉及 2,599 家公司，按冻结哈希规则选中 126 家；这些公司有 1,010 条合格提问、755 条截点前可见回复。逐条比较问答 ID、股票、提问与回复时间、原文片段及哈希后，Agent 输入包的 105 条回复全部对应各公司截点前最新的合格问答；另 21 家没有可见回复，不补入未来文本。输入包字段白名单不含来源季度、财务快照或其他财务值。复算程序和只含计数/哈希的本地记录分别是 [`semantic/audit_wuhan_snapshot_source.py`](semantic/audit_wuhan_snapshot_source.py) 与 Git 忽略的 `research_outputs/wuhan_snapshot_source_audit_v1.json`。

冻结字面关键词预测的 47 条命中中，37 条证据在公司回复，10 条仅在投资者提问。组合实验只把前 37 家的**零方向**关注度惩罚接入 Agent；`0.25` 和 `1.0` 是手设机制参数。对当前代码重新运行的 168 条关键词组合路径、5,880 个组合日、17,640 次资产观察，独立程序逐条核对 `text_signal=0`、回复关键词来源、`available_at <= signal_cutoff_date`、`signal_cutoff_date < execution_reference_date < trade_date`、不确定度及证据 ID；其中 5,180 次资产观察带回复关键词惩罚。见 [`semantic/audit_wuhan_agent_inputs.py`](semantic/audit_wuhan_agent_inputs.py) 和 Git 忽略的 `research_outputs/wuhan_agent_input_isolation_audit_v2.json`。

旧无文本/关键词归档的输入与产物哈希均未漂移，但八个仿真或行情模块源码已更改，旧归档的严格源码校验因此拒绝用当前代码重审。为恢复**当前代码可复核性**，在新目录重跑 84 条无文本基线及 168 条关键词路径，并分别完成独立的逐日账本审计：无文本 2,940 个组合日、8,820 次资产竞价，关键词 5,880 个组合日、17,640 次资产竞价，来源、角色决策、价格反馈、撮合、资金股份和汇总均通过。新旧对应压缩账本及逐路径 JSON 的 SHA-256 完全相同；汇总中经济指标相同，实验 ID、运行 ID 等来源身份字段随新代码/配置变化。新配置为 [`configs/wuhan_pre_event_pit_portfolio_keyword_2020_v2.json`](configs/wuhan_pre_event_pit_portfolio_keyword_2020_v2.json)，正式新归档分别在 Git 忽略的 `research_outputs/wuhan_pit_portfolio_no_text_2020_v3/` 与 `research_outputs/wuhan_pit_portfolio_keyword_attention_2020_v2/`，对应 `_audit/` 目录保存完整审计。

旧 105 条 DeepSeek 预测的归档比较仍有两项门槛失败：检出 F1 ≥ 0.70 与支持类别宏 F1 ≥ 0.60；实测 `verify_gate` 拒绝，`wuhan_pre_event_agent_signals_v3` 不存在。关键词机制不能替代语义 Agent 验证。来源时间是数据库的可用时点代理，未独立证明首次公开时间；原样本按事前提问活跃度选出，也不代表全部上市公司。

在仓库根目录复核：

```powershell
python -m research.semantic.audit_wuhan_snapshot_source --audit-existing
python -m research.semantic.audit_wuhan_agent_inputs --audit-existing
python -m research.simulation.audit_wuhan_portfolio_baseline `
  research_outputs/wuhan_pit_portfolio_no_text_2020_v3 `
  --config research/configs/wuhan_pre_event_pit_portfolio_no_text_2020.json
python -m research.simulation.audit_wuhan_portfolio_keyword `
  research_outputs/wuhan_pit_portfolio_keyword_attention_2020_v2 `
  --config research/configs/wuhan_pre_event_pit_portfolio_keyword_2020_v2.json
```
