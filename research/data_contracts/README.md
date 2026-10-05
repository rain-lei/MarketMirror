# 数据合同

<!-- public-factor-current -->
最新[滞后沪深300暴露的公共估值通道实验](../PRE_WUHAN_PUBLIC_FACTOR_CHANNELS_2019.md)已完成80条件、3,280条完整路径、141,040个组合日及105,780条同状态提交诊断，**20/80通过原五项联合开发检查**。公共因素分别进入策略目标和所有主体报价，保持原私有消息和账户资源。全量原始来源/账本独立复核、生产五份产物及独立统计逐字节复现通过，868项测试通过；820条原样路径和35,260个完整日对象与旧归档一致。公共相加提高总估值扰动方差，结果仍来自已使用开发队列，尚未进行真实投资者校准或未使用时期验证，未采纳新默认参数；语义门槛保留。后续检验共同/行业/公司残差风险分配，继续补真实资金、库存、订单和政策预期证据；本阶段继续做研究与可复现实验。
<!-- /public-factor-current -->

此前[固定总资源的现金分配合同](cash_resource_controls_2019.md)约束初始化一次转移、逐股总现金/总股份、原资金完整对象桥接和未执行的固定订单资金诊断。60 条件、2,460 路与全部统计独立核验，五项产物逐字节复现，848 项测试通过；市场联合检查仍 0/60，见[报告](../PRE_WUHAN_CASH_RESOURCE_CONTROLS_2019.md)。旧来源 QC 和渲染器环境差异均保留。
此前[订单到成交价格只读合同](order_price_diagnostics_2019.md)固定全量主体/资产/路径范围、所有最优价区间、实际请求/接受/成交与真实钱包；环境例外只用于明确绑定的非执行渲染器，不解除旧来源 QC。该阶段结果和 836 项测试见[报告](../PRE_WUHAN_ORDER_PRICE_DIAGNOSTICS_2019.md)。

此前[目标与报价反馈分离合同](price_feedback_channels_2019.md)要求同状态分配/数量与物理报价分别隔离，完整二乘二网格、五种子和两个库存锚点保留；旧对角路径全部桥接，完整多日账户与假设提交分别验收。全量实际/同状态核验与五项逐字节重建已通过，40 条件仍无联合改善，见[报告](../PRE_WUHAN_PRICE_FEEDBACK_CHANNELS_2019.md)。

这里保存进入研究管道的标准记录结构。原始 Excel 的列名和单位不能直接代表标准字段含义，财务字段必须在确认口径后写入 `financial_quarter`。

此前[自身价格反馈完整路径合同](own_price_feedback_2019.md)固定五束种子、六条件及原核心兼容，目标/报价同状态与连续账户路径分别验收；预期数由维度导出，保留原计数失败，全部结果保存。全量独立核验和逐字节重建通过，但没有共同市场结构改善，见[结果](../PRE_WUHAN_OWN_PRICE_FEEDBACK_2019.md)。

此前[完整账本分配与观点来源合同](allocation_attribution_2019.md)要求全部路径、逐位置身份、十阶段分量和实际提交/接受/成交分别一致，固定四来源顺序并保留裁剪；这是事实记账，不能解释为成交因果或新价格路径。实际覆盖和自身价格反馈发现见[报告](../PRE_WUHAN_ALLOCATION_ATTRIBUTION_2019.md)。

此前[融资因素增量合同](issuer_financing_increment_2019.md)固定三组因素、同组八模型配对、原训练截点、缺项留空和独立当前金额/SVD 核验；本轮没有稳定公司日共同改善，见[结果](../ISSUER_FINANCING_INCREMENT_2019.md)。

已完成的[同一半年报融资状态合同](issuer_financing_states_2019_half.md)限定六月末现金、资产和借款分项及受限资金单行比例：原 PDF、范围、单位和独立读取须一致，失败/未知留空，不推断自由现金或完整有息债务。实际覆盖与核验见[融资状态报告](../ISSUER_FINANCING_STATES_2019_HALF.md)。

三份用户提供工作簿的文件身份和表头核对见[附件结构核验](../USER_WORKBOOK_SOURCE_AUDIT_2026.md)：`金融数据示例.xlsx` 实际是 32 条互动问答样例；66 个财务快照字段只见于 2020 年问答大表，仍缺少单位、变换、报告期间及公告时间的外部定义。

[来源季度时序审计](../FINANCIAL_QUARTER_ASSIGNMENT_AUDIT_2026.md)对 2020 大表的 390,608 条记录发现：`季度` 可由回复日期的特定分桶规则逐条重建，且季末边界提前一天、2021–2023 年回复均落入 4。该字段只能作为来源分组提示，不能解释为财报季度或事前可见财务数据。

[真实特征出口审计](../ASOF_FEATURE_ISOLATION_AUDIT_2026.md)核对统一库的两个已归档截止时点，确认 37 列文本特征与独立 SQL 的可见问答/回复计数一致，全新导出的 CSV 逐字节一致。此结论只覆盖这两个文本特征出口，不代替其他模型入口的时点审核。

- `qa_record.schema.json`：早期 CSV 字段映射参考；正式实验使用统一数据库的来源行结构。
- `qa_occurrence.schema.json`：统一数据库中一条问答来源记录的结构。
- `qa_features.schema.json`：旧版公司季度描述统计，不能用于预测季度内事件。
- `forecast_feature_policy.json`：按截止时刻导出的文本特征白名单、时间假设和排除字段。
- `financial_quarter.schema.json`：一家公司一个报告期的一项财务指标。
- `financial_fields.json`：66 个财务快照字段和 2 个行级行情上下文字段的核对清单。
- `market_daily.schema.json`：带基准 ID 的标准行情收益记录，收益率统一为小数形式。
- `market_activity.schema.json`：提供方日成交股数、人民币成交额和交易状态；它描述已成交的双边总量，不是盘口深度或净买卖压力。
- `industry_membership.schema.json`：官方历史行业表及样本映射，保留发布时间代理、原文、PDF 页码与单元格坐标；缺损标签为 null，影响方向未知、幅度为空，经济信号关闭。当前 123 公司映射与结构检验见[2019 官方行业报告](../PRE_WUHAN_OFFICIAL_INDUSTRY_2019.md)。
- [`monetary_operations_2019.md`](monetary_operations_2019.md)：官方操作公告和 `t-2` 上下文的字段合同；操作总量、净量及工具期限分开，缺前值留空，最新观察利率不冒称现行政策利率，经济信号关闭。57 份原文及全部截点核对见[货币操作报告](../PRE_WUHAN_MONETARY_CONTEXT_2019.md)。
- [`policy_calendar_2019.md`](policy_calendar_2019.md)：83 份公告及 1 份正式文件的扩充合同；分开首次可见观测调息、继承状态和此前已知的降准实施，保留分钟精度及 PDF 来源复核。14 天逆回购前值已补齐，五项响应参数仍不可同时确定，见[扩充政策报告](../PRE_WUHAN_EXPANDED_POLICY_2019.md)。
- [`extended_policy_rates_2019_2020.md`](extended_policy_rates_2019_2020.md)：178 份跨年利率公告及 95/43 日基准对照合同；MLF/TMLF、未知前值、时钟与配对分开，训练满秩仍不自动放行；见[扩展时期诊断](../EXTENDED_POLICY_RATE_DIAGNOSTIC_2019_2020.md)。
- [`policy_information_clocks_2019_2020.md`](policy_information_clocks_2019_2020.md)：四组市场/政策截止字段、212 份补源及 96/43 日共同配对；保留初始秩失败及修订，不择优、不填未知为零；另约束只用设计的事件支持诊断，见[完整报告](../POLICY_INFORMATION_CLOCK_CONTRAST_2019_2020.md)。
- [`issuer_formal_disclosures_2019_2020.md`](issuer_formal_disclosures_2019_2020.md)：123 家官方报告目录、日期代理、更新版、原始 PDF 与页候选的来源合同；原始银行锚点的有限范围保留，后续数字层另见下一项，见[目录报告](../ISSUER_FORMAL_DISCLOSURE_INVENTORY_2019_2020.md)。
- [`issuer_financial_states_2019q3.md`](issuer_financial_states_2019q3.md)：123 家当前报表的字段、列日期/范围、3,422 个单元格交叉核对、598 个明确缺项、金融机构适用性及 as-of 出口合同；数字读取通过不放行经济系数，见[财务状态报告](../ISSUER_FINANCIAL_STATES_2019Q3.md)。
- [`issuer_operating_states_2019q3.md`](issuer_operating_states_2019q3.md)：123 家累计盈利、现金流和四项额外资产的源符号/期间/范围、独立复核、带时区可见性及非金融比率合同；缺失不填零，数据一致不等于经济增量。
- [`issuer_financial_increment_2019.md`](issuer_financial_increment_2019.md)：共同公司日期、财务/行业可见性、模型训练目标时点隔离、三个固定财务变量与市场/行业对照及独立 SVD 核验；增量未改善公司日误差，经济输入仍关闭，见[完整结果](../ISSUER_FINANCIAL_INCREMENT_2019.md)。
- [`issuer_operating_increment_2019.md`](issuer_operating_increment_2019.md)：经营/收入/资产三组固定配对，原控制与全部参考在各组交集重新拟合，源缺项不填零，训练时钟和独立源金额/SVD 核验；未发现稳定公司日增量，见[最新结果](../ISSUER_OPERATING_INCREMENT_2019.md)。
- [`issuer_financing_increment_2019.md`](issuer_financing_increment_2019.md)：同源半年融资三组固定增量、原网格/时钟、同组参考重建与缺项留空；滞后交互不看当日目标，单行限制不作总额，独立原单元格/Decimal/SVD 检验。
- [`issuer_business_evidence_2019_half.md`](issuer_business_evidence_2019_half.md)：122 份半年报全页证据、原文及可见性、地区/收入/子公司/担保关系的范围边界、独立读取诊断及缺源；字面一致不等于数值暴露通过，3 份读取 QC 保留，见[业务证据报告](../ISSUER_BUSINESS_EVIDENCE_2019_HALF.md)。
- [`issuer_regional_revenue_2019_half.md`](issuer_regional_revenue_2019_half.md)：独立物理单元格、当前列/单位、完整/重大分部/主营业务、跨页及 QC，有限来源数值与真实地域定义分开；55 家 267 个数值，16 个收入构成表闭合，不自动成为冲击系数，见[收入状态报告](../ISSUER_REGIONAL_REVENUE_STATES_2019_HALF.md)。
- [`issuer_restricted_assets_2019_half.md`](issuer_restricted_assets_2019_half.md)：期末列、资金/非现金抵押、原因原文、单位/范围、跨页与独立读取；92 家有限源值，不推断自由现金或跨季度比例，见[资金约束来源报告](../ISSUER_RESTRICTED_ASSETS_2019_HALF.md)。
- `event_definition.schema.json`：事件名义日期、来源证据与公开可见日期/时间，后二者必须二选一。
- `semantic_signal.schema.json`：单条可见问答文本的结构化事件、方向、时间范围、强弱/不确定性和原文证据跨度；执行器还会检查跨度确实与可见原文逐字相符。

旧工作簿财务提取命令生成 `financial_unverified.sqlite`，所有指标以 `unverified` 状态保存。新正式披露的当前状态保存在单独的、来源及口径明确的 JSON 出口，不自动覆盖或放行旧快照。
`financial_snapshot` 存储去重后的原始财务值；`source_row_reference` 保留来源工作表、Excel 行号、原始时间、标签及 `p`、`p/e`，通过 `snapshot_id` 回溯。
`field_dictionary` 存储每列的缺失率、数值范围和待确认口径，`run_metadata` 存储文件哈希及处理版本。

`data_pipeline.render_financial_dictionary` 可从 `financial_quality_report.json` 生成脱敏的 `field_dictionary.json` 和 `field_dictionary.md`，将观察到的结构统计与尚未核实的经济含义分开呈现。

只有拿到报告年度、来源季度含义、指标单位、计算公式及公告时间后，才能写入正式 `financial_quarter` 表。当前命令不会通过提问日期或文件名自动生成财报日期。

`label_company_violation` 是公司级属性。它不能在没有重新定义标签的情况下作为问答级标签使用。

`post_event_replied_count` 和 `post_event_unreplied_count` 描述回复结果。它们默认不进入预测任务，只用于事后分析。

统一数据集按 `(source_file_hash, source_sheet, source_row)` 追溯问答与财务快照；不同文件的相同行号不会冲突。公司别名分别保存，不从最后一行覆盖公司名称。

`question_available_at` 和 `reply_available_at` 使用带 `+08:00` 的固定精度时间格式。原始值保存在 `qa_restricted_context` 和财务引用表。仅到日期的回复使用次日零点作为保守可用时点，并标记 `reply_date_only_next_day_bound`；不表示观测到真实发布时间。当前可见时间以来源时间为代理，尚未验证公开发布及修订历史。

`question_eligible` 是有效代码、明确时间及可用文本的质量标记，不表示该记录适用于任意预测时点。特征导出还必须按 `window_start` 和 `as_of` 过滤；回复还要求 `reply_eligible=1` 且回复时间不晚于 `as_of`。

同一输入重复构建保留相同来源行 ID；记录内容指纹只用于重复审计，不自动删除记录。跨来源对齐或去重需要额外的平台记录 ID 或人工确认。

行情记录的价格列只有在输入为价格时才填值；收益率输入时为空，不反推价格。配置声明的复权/收益口径、基准、来源和交易日历保存在 `market_manifest.json`，而不是从列名猜测。未经复权的股票价格、缺失交易日、多日跨度和未声明收益率单位不进入标准行情。

事件配置需要证据来源。`visible_at` 必须带时区；只有 `visible_date` 时保守使用次日作为可反应日期。执行器保存名义日期、可见信息与实际对齐日，不用较早的日期回填事件。真实数据的来源声明尚需独立核实；`synthetic` 清单只能作为实现验证。

为引入问答之外的政策/市场日历信息，新增 [`policy_event.schema.json`](policy_event.schema.json)：独立保存公告来源类型及原文哈希、发布时间精度、交易日历可见性规则、措施生效区间和主体/行业映射状态。2018 资管新规、武汉交通管制和上交所休市延长的本机来源核对见[政策事件目录](../POLICY_EVENT_CONTEXT_CATALOG_2018_2020.md)。另有 [`issuer_event_exposure.schema.json`](issuer_event_exposure.schema.json)，用于保存逐发行人的政策直接范围匹配和证据。当前 2018 三家试点只有部分主体映射，所有记录仍为上下文，不启用 Agent 信号；政策发布时间不可替代生效时间，未映射的范围也不能假定影响所有公司。
