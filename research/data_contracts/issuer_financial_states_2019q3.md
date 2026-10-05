# 2019 三季报当前公司状态合同

本合同约束正式报告的数值、来源和时间，不修改旧工作簿的未核实财务快照。当前队列为 123 家机制开发公司，与 fresh-v3 126 家不同。来源与实际复核范围见[财务状态报告](../ISSUER_FINANCIAL_STATES_2019Q3.md)。

## 输入与验收

输入是[原文及字段协议](../configs/issuer_q3_balance_sheets_2019.json)绑定的 123 份官方原始 PDF，报告期末 2019-09-30，快照截止 2020-01-22 23:59:59+08:00。证券/组织、附件、报告期间、版本和日期证据沿用[披露合同](issuer_formal_disclosures_2019_2020.md)，原始哈希必须一致。

生产层只有在一个发行人恰有一个完整当前非母公司表、列日期/范围明确、单位唯一、所有当前/比较及合并/母公司的总额可核账时选定。编制日期不是列日期；年初调整、变动说明及母公司表不得替代当前合并表。重复或不完整候选留为明确 QC 原因。

独立层不调用生产函数。使用 pdfplumber 实际边框单元格核对 121 份报表，另对两份无边框银行表使用完整页面复核的列锚点加新文本读取。字段数值、缺项、单位、范围日期与同证券同附件的关联均须通过。状态出口拒绝失败复核、错误证券、错误原文哈希或其他候选的复核记录。

## 公司状态字段

| 字段 | 定义 |
|---|---|
| `stock_code`, `historical_short_name`, `industry_code` | 冻结来源队列的证券身份和行业，不由最后一条数据覆盖 |
| `status` | `INDEPENDENT_EXTRACTION_AGREEMENT` 表示两个读取方式一致，不表示审计意见、真实财务无误或经济模型验证 |
| `scope`, `period_end` | 所选当前列的原文口径与报告期末；平安银行保持 `issuer_as_stated`，其他所选当前列为 `consolidated` |
| `report_metadata`, `available_at_proxy` | 所选正式版本及次日零点可用代理；修订版不回溯到更早原版日期 |
| `source_pdf_path`, `source_pdf_sha256` | 原始字节位置与哈希；原文不可覆盖 |
| `reported_unit`, `multiplier_to_yuan`, `reported_unit_evidence` | 原文单位、整数换算系数和页码/坐标/文字；金额用 Decimal 字符串 |
| `currency`, `currency_basis` | CNY 及明确人民币声明或中国正式财报元单位的惯例依据；惯例不冒称逐份明确币种声明 |
| `amounts` | 16 个当前字段槽位，分别保留 `status`、原始数值字符串、归一金额字符串 |
| `field_source_evidence`, `column_header_evidence` | 原文标签、页面/坐标、当前与比较单元格，以及实际表头和重述等限定；证据中的比较值不成为旧时点数据 |
| `ratios` | 明确命名的分子/分母、状态和值；无股票方向、事件系数或风险等级 |
| `generic_nonfinancial_ratios_applicable` | 仅普通非金融合并口径可用通用比率；金融机构不套用 |

`NUMERIC` 必须为原文明示数字，允许原文明确的零；`ROW_ABSENT`、`REPORTED_BLANK`、`REPORTED_DASH` 的两个数值字段均为 null。歧义、错位、无法解析或源冲突不成为明示数字。拒绝 NaN、无限值及零/负分母伪比率。不得通过 `or 0`、均值或隐式类型转换填补缺项。

四个总额为资产、负债、全部权益、负债权益总计；其余为货币资金、流动资产、流动负债、短期借款、长期借款、应付债券、一年内到期非流动负债、租赁负债、应收账款、存货、券商货币资金中的客户存款、银行现金及存放中央银行款项。缺少某行不证明相应业务或余额不存在。

比率包括总负债/资产、货币资金/资产、流动资产/流动负债、货币资金/流动负债、短期借款/资产、长期借款/资产、一年内到期非流动负债/资产、应收账款/资产、存货/资产。分子或分母未明示时为 `MISSING_SOURCE_AMOUNT`；金融机构为 `NOT_APPLICABLE_FINANCIAL_INSTITUTION`。它们均保持 null，不转为零。

银行存款不能直接作为普通企业有息债务；券商货币资金包括客户资金，不能等同自由资金。总负债不能命名为总有息债务，货币资金不能命名为已验证可自由使用现金，一年内到期非流动负债不能命名为纯短期借款。没有输出不完整分项相加的“总债务”。

## 时点查询与下游约束

[`state_asof`](../data_pipeline/issuer_financial_states.py)只接受带时区时刻。质量状态未通过或 `available_at_proxy > asof` 时返回 None，整家缺失；实际有值的比率与缺失比率均不能提前进入该时点。000761 的选定更新版从 2019-12-08 才可用，是数据中的时间约束，不是生产代码的证券特例。

当前出口只保存本份报告的当前列；比较列和“经重述”值不能重新包装成更早公开快照。`comparison_columns_used_as_earlier_publication_snapshots=false`、`original_pdf_bytes_certified_unchanged_historically=false`、`unrestricted_money_funds_verified=false`、`complete_interest_bearing_debt_verified=false`、`agent_signal_enabled=false` 均显式保存。

下游要按决策信息截止调用 as-of 选择，不能只按报告期末连接历史行情。原基线、新来源、问答和合并来源的实验需使用同一公司日期集合，保留缺项及版本排除计数。现有窗口已消耗为开发数据，不作为盲验证。宏观事件、公司暴露和交易约束的方向/幅度各自验证，本合同不自动放行 Agent 参数。

## 固定出口

三个排他写入的 JSON 位于 Git 忽略的 `research_outputs`：`issuer_q3_balance_sheet_candidates_2019_v1.json`、`issuer_q3_balance_sheet_audit_2019_v1.json`、`issuer_financial_states_2019q3_v1.json`。上游原文、元数据、配置、解析与独立核对程序逐项绑定 SHA-256，计算前后重验；`--audit-existing` 要求逐字节一致。对应程序及复现命令见报告。
