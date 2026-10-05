# 2019 半年报收入地区表与有限来源状态合同

此合同适用于 v2 候选表、独立物理单元格审计和有限收入状态。当前统计及实际复现见[研究记录](../ISSUER_REGIONAL_REVENUE_STATES_2019_HALF.md)，上游规则见[业务证据合同](issuer_business_evidence_2019_half.md)。数值通过不自动认证地域定义、经济方向、风险系数或 Agent 输入。

## 身份、期间和原文

全部 123 家唯一公司始终保留，候选范围由已冻结地区标题索引确定。每家公司保留 `stock_code`、历史简称、选定报告元数据、来源路径/哈希、读取状态和全部表格状态。688018 缺源记录不得删除或用后发报告补齐；没有标题或未检测到表格也不表示零暴露。

原报告期为 `2019_half`、截至 2019-06-30 的半年期间，公告/归档日、修订和回取日分别保存。`available_at_proxy` 必须按报告自身公开日期执行决策时点选择，修订不回灌，当前回取字节不自动成为当年未变历史版本。

## 候选单元格与表范围

原 PDF 页码统一 1-based，表头、范围、单位、标签及金额均保存原文、坐标与源页。当前收入列必须由本期日期和收入角色证明；比较期、成本、支出、毛利和同比不得自动代替。物理表框证明列归属，不按猜测均分列宽，也不在同页匹配到同数值就认定为同一单元格。

Decimal 保存原金额及比例，缺项、横杠、歧义和不明列保持 null。原文真实零值仍为零，负抵销仍为负数。数字的多行拼接仅限同一物理单元格。跨页续表需物理邻接、列边界相容、范围及单位一致；新章节、其他正文、单位变化和前段遗留问题不得静默清除。

`table_scope` 至少区分 `FULL_REVENUE_COMPOSITION`、`MAIN_BUSINESS_SCOPE_ONLY`、`MATERIAL_SEGMENTS_ONLY` 和 `UNKNOWN`。重大分部或主营业务金额不能替代全部收入；母公司、子公司、小计、总部、抵销与地理地区分别保存。其他收入没有明确地区分配时不能归入一个经营地区。

## 独立复核

独立程序使用固定 pdfplumber 版本和物理边框单元格，不导入生产解析、表头或比例函数。候选坐标附近固定 3 点容差内必须存在唯一对应单元格；缺失、歧义、文本不符及异常有显式状态，并继续后续公司。重新核对表头角色、期间、单位、范围、行标签、数值符号、缺失与算术。

`PASS_SOURCE_CELLS_AND_RECONCILIATION` 仅认证本表所选源单元格及适用计算；不认证整个报告或地域含义。`CURRENT_COLUMN_UNVERIFIED_AS_EXPECTED` 明确保留当前列未能证明的事实，不能作为金额通过或空白成功。上游任何读取诊断保留，独立数值一致也不能自动解除它。

## verified 字段

| 字段 | 赋值条件与含义 |
|---|---|
| `reported_current_revenue` | 候选原值；允许保留 QC 记录，不能视同采纳 |
| `source_number_independently_verified` | 唯一对应表通过独立数值检查、公司字面读取无诊断且该表未要求源读取 QC，行金额非空 |
| `verified_current_revenue_reported_units` | 上述条件成立时才为原单位金额，否则 null |
| `verified_current_revenue_yuan` | 上述条件加明确人民币单位证据与有效换算尺度；单独“元”不推断 CNY，否则 null |
| `verified_share_of_reported_table_total` | 来源明示正总额、相关行完整、范围一致、精确闭合、无遗留问题且印刷占比舍入一致；仅占自己的表内总额，否则 null |
| `complete_reported_revenue_table_verified` | 满足来源与独立数值门槛，且 FULL 完整收入构成表闭合；不表示所有行是统一定义的地区 |
| `complete_geographic_allocation_verified` | 当前一律 false，等待地域含义、分配与全覆盖另行核验 |
| `finer_geography_exposure` | 当前一律 null，不把国内/大区/湖北细分到武汉 |
| `stock_return_direction` | unknown，未估计或人工指定冲击方向 |

`component_kind` 分别记录未分地区其他收入、实体范围/小计/抵销和地域标签含义仍待核验的行。即使收入构成闭合，仍不得丢弃其他收入后归一化地区，或把不同层级同时放入分母。主营业务和重大分部的已核验表内比例继续保留范围限制，不能提升为完整发行人暴露。

## 公司状态与版本

有源且至少一个采纳金额为 `LIMITED_REPORTED_REVENUE_FACTS_AVAILABLE`；没有可采纳金额为 `REVENUE_SCOPE_OR_TABLE_COVERAGE_UNRESOLVED`；上游读取有诊断为 `SOURCE_READER_QC_NOT_ADOPTED`；缺源为 `SOURCE_MISSING_NOT_ZERO`。QC 记录的 verified 字段为空，不能填 0、标记为无暴露或删除公司。真实零值与处理计数的零不等同于缺值。

配置、原始附件、上游产物和程序版本均绑定 SHA-256；开始与结束检查来源。候选与独立结果必须按唯一公司/表索引完整连接，重复、遗漏或变更关联须拒绝。三阶段 `--audit-existing` 全量重建到相同字节才确认复现；改变规则须新版本，保留旧候选、失败和 QC。

视觉记录只认证所记载的四份 PDF、八个完整页的有限观察；保留渲染诊断及原始未复核渲染清单，不追溯修改失败历史。助手单人复核不成为独立人工金标准。

本出口 `business_exposure_values_verified=false`、`agent_signal_enabled=false`。后续经来源定义核实的特征必须另外冻结冲击交互、样本、可见性、缺失处理和验证时期，按共同公司日期比较市场基线、问答、非问答及联合输入。不得以数值核验、测试通过或完整收入构成闭合代替增量效果与 Agent 校准。
