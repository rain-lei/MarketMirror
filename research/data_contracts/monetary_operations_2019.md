# 官方货币操作上下文合同 v1

本合同描述 [`../PRE_WUHAN_MONETARY_CONTEXT_2019.md`](../PRE_WUHAN_MONETARY_CONTEXT_2019.md) 对应的有限来源包。它用于来源核验及历史信息上下文，不是已放行的 Agent 输入，也不是完整货币政策表。

后续[扩充政策合同](policy_calendar_2019.md)补入前值、LPR 与降准；本合同的覆盖与缺失叙述仍专指初始 57 份来源包，原产物不原地修订。

## 公告与操作行

`operations.json` 的 `records` 每项是一份官方公告，保留标题、带 `+08:00` 的历史页面时间、落款日期、正文文字及来源身份。`source` 包含官方 URL、原始响应路径、获取时间、SHA-256、发布主体和时间解释。栏目日期、页面时间日期及落款日期必须一致；页面时间或表格缺失、身份冲突、未知操作结构均拒绝解析。当前页面版本不等于当年未修订快照。

| 字段 | 口径 |
|---|---|
| `operations` | 公告明确列出的工具操作；可以为空，不能据此断言所有工具均未操作 |
| `instrument` | `mlf`、`reverse_repo`、`hk_central_bank_bill` 分开；解析器支持的 `cbs` 也须独立，当前来源包没有 CBS 行 |
| `market` | 境内 `mainland` 与香港 `offshore_hk` 分开 |
| `tenor_value` / `tenor_unit` | 原始期限数值及“天”“个月”“年”；不擅自把不同期限视为同一工具条件 |
| `gross_amount_100m_yuan` | 原文亿元人民币操作量或发行量；不是净投放 |
| `rate_kind` / `rate_pct` | 境内操作利率、香港票据收益率或 CBS 费率按类别区分；数值 3.25 表示 3.25% |
| `table_evidence` | 对应原表格的行文本，保留数字与期限证据 |
| `previous_observation` | 来源包中同工具同期限的上一条操作及其时间、利率和来源 SHA-256；首条为 `null` |
| `change_from_previous_observed_bps` | `(本次 rate_pct - 前次 rate_pct) × 100`；首条无前值为 `null`；不能跨工具或期限计算 |
| `explicit_no_reverse_repo` | 只由明确不开展逆回购的原文确定 |
| `gross_reverse_repo_amount_100m_yuan` | 有逆回购表时按行求和，明确不开展时为 0；无声明时 `null`，不得默认为 0 |
| `net_liquidity_100m_yuan` | 当前所有公告均为 `null`；缺少完整到期及回笼来源，不能由总量替代 |
| `use_policy` | `source_verified_context`，`agent_signal_enabled=false` |

当前前值匹配键由工具、期限数值和期限单位组成；工具在本版本固定映射到市场与利率类别。未来如果一个工具允许多个市场或利率类别，须升级匹配合同并重新核验。

## 按截点生成的上下文

`lagged_context.json` 的 `calendar` 为固定实验的 43 个合成步骤，每步保留原 `trade_date` 标签、`signal_cutoff_date` 及该截点的 `23:59:59+08:00`。只取页面发布时间不晚于截点的公告，不读取目标日股票收益。旧实验时钟为 `t-2` 信息、`t-1` 执行参考、`t` 合成步骤标签，不能当作交易所同日行情重现。

`latest_observed_instrument_rates` 是截点前各工具期限的最近观察利率，每个值同时携带公告时间、标题、SHA-256、利率类别和该公告的前值差。没有已观察记录的工具不添加键，不能填 0。携带的利率不保证是该日全部渠道的现行政策利率；携带的差值也不表示每天再次发生该冲击。未来若生成事件脉冲，必须按来源发布时间和首次可见步骤去重。

`cutoff_date_bulletins` 仅列信息截点当日可见公告；`cutoff_date_gross_reverse_repo_100m_yuan` 仅在当日有明确境内逆回购声明时取值。香港公告不计入境内逆回购总量。`cutoff_date_net_liquidity_100m_yuan` 当前为 `null`，每步 `agent_signal_enabled=false`。

## 当前缺失与验收

首次观察的 6 种工具期限均缺来源包内前值，其中 12 月 18 日 14 天逆回购影响开发段的利率变化判定。未收集 LPR、降准、完整到期回笼等其他渠道，不能把该目录视为政策全貌。工具操作量、利率差和股票经济效应各自验收；不能自动把降息标记为股票正向消息。

来源与程序由获取及派生配置冻结，所有原始响应和派生产物哈希保存在清单。独立审计直接重新解析原始 HTML，核对事实、同期限前值、全部截点及缺失状态。新来源或新时间规则使用新版本和新目录；冻结产物不原地修订。
