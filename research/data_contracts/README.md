# 数据合同

这里保存进入研究管道的标准记录结构。原始 Excel 的列名和单位不能直接代表标准字段含义，财务字段必须在确认口径后写入 `financial_quarter`。

- `qa_record.schema.json`：早期 CSV 字段映射参考；正式实验使用统一数据库的来源行结构。
- `qa_occurrence.schema.json`：统一数据库中一条问答来源记录的结构。
- `qa_features.schema.json`：旧版公司季度描述统计，不能用于预测季度内事件。
- `forecast_feature_policy.json`：按截止时刻导出的文本特征白名单、时间假设和排除字段。
- `financial_quarter.schema.json`：一家公司一个报告期的一项财务指标。
- `financial_fields.json`：66 个财务快照字段和 2 个行级行情上下文字段的核对清单。
- `market_daily.schema.json`：带基准 ID 的标准行情收益记录，收益率统一为小数形式。
- `market_activity.schema.json`：提供方日成交股数、人民币成交额和交易状态；它描述已成交的双边总量，不是盘口深度或净买卖压力。
- `event_definition.schema.json`：事件名义日期、来源证据与公开可见日期/时间，后二者必须二选一。
- `semantic_signal.schema.json`：单条可见问答文本的结构化事件、方向、时间范围、强弱/不确定性和原文证据跨度；执行器还会检查跨度确实与可见原文逐字相符。

财务提取命令生成 `financial_unverified.sqlite`，所有指标以 `unverified` 状态保存。
`financial_snapshot` 存储去重后的原始财务值；`source_row_reference` 保留来源工作表、Excel 行号、原始时间、标签及 `p`、`p/e`，通过 `snapshot_id` 回溯。
`field_dictionary` 存储每列的缺失率、数值范围和待确认口径，`run_metadata` 存储文件哈希及处理版本。

只有拿到报告年度、来源季度含义、指标单位、计算公式及公告时间后，才能写入正式 `financial_quarter` 表。当前命令不会通过提问日期或文件名自动生成财报日期。

`label_company_violation` 是公司级属性。它不能在没有重新定义标签的情况下作为问答级标签使用。

`post_event_replied_count` 和 `post_event_unreplied_count` 描述回复结果。它们默认不进入预测任务，只用于事后分析。

统一数据集按 `(source_file_hash, source_sheet, source_row)` 追溯问答与财务快照；不同文件的相同行号不会冲突。公司别名分别保存，不从最后一行覆盖公司名称。

`question_available_at` 和 `reply_available_at` 使用带 `+08:00` 的固定精度时间格式。原始值保存在 `qa_restricted_context` 和财务引用表。仅到日期的回复使用次日零点作为保守可用时点，并标记 `reply_date_only_next_day_bound`；不表示观测到真实发布时间。当前可见时间以来源时间为代理，尚未验证公开发布及修订历史。

`question_eligible` 是有效代码、明确时间及可用文本的质量标记，不表示该记录适用于任意预测时点。特征导出还必须按 `window_start` 和 `as_of` 过滤；回复还要求 `reply_eligible=1` 且回复时间不晚于 `as_of`。

同一输入重复构建保留相同来源行 ID；记录内容指纹只用于重复审计，不自动删除记录。跨来源对齐或去重需要额外的平台记录 ID 或人工确认。

行情记录的价格列只有在输入为价格时才填值；收益率输入时为空，不反推价格。配置声明的复权/收益口径、基准、来源和交易日历保存在 `market_manifest.json`，而不是从列名猜测。未经复权的股票价格、缺失交易日、多日跨度和未声明收益率单位不进入标准行情。

事件配置需要证据来源。`visible_at` 必须带时区；只有 `visible_date` 时保守使用次日作为可反应日期。执行器保存名义日期、可见信息与实际对齐日，不用较早的日期回填事件。真实数据的来源声明尚需独立核实；`synthetic` 清单只能作为实现验证。
