# 半年报同源融资状态合同

正式出口为 `research_outputs/issuer_half_financing_states_2019_v1.json`，对应[来源和核验报告](../ISSUER_FINANCING_STATES_2019_HALF.md)。全队列固定 123 家；不删除缺源、读取失败或未知字段的公司。

## 来源与采纳

余额候选、独立原文审计和此前受限资产状态必须有唯一股票身份，并使用相同原 PDF 哈希、路径和报告元数据。报告期为 2019-06-30；选定当前列必须是六月末合并范围，全部原文 QC 保留。独立原表 PASS 不覆盖上游读取 QC。

采纳的 `balance_fields` 包括 16 个固定余额字段，分别保存 `source_status`、`reported_value`、`source_number_independently_verified`、`verified_value_reported_units`、`verified_value_at_reported_unit_scale`、`verified_value_yuan` 与原行。尺度值不是币种认证：人民币字段额外要求已打印且独立核对的 CNY 声明。横线、空白、缺行和歧义均为 null，实际打印的零保留。

`balance_ratios` 是同一张表内的 8 个固定无量纲比例，须分子非负、分母正且均为已核验数值；单位在该表内抵消。它不把币种未知的尺度值称为人民币。债务组成分别保存，不把缺项视为零，不生成完整有息债务。行业 J 保留可核验余额而排除这些普通公司比率。

## 受限行关系

`restricted_cash_rows` 对每个原表/行保存标签、现金类别、原因、坐标、原缺项状态及同源比例。仅当前受限货币资金或现金子类进入该列表，非现金资产抵押不作为现金。行金额经原独立读取、合并附注范围明确且币种/尺度证明后，才能连接余额中的人民币货币资金和资产。

仅“元”的限制表不能靠惯例推断币种；可采用此前打印、同一尺度、独立读取一致的附注报表人民币统一单位声明。声明晚于该行、尺度不一致、表单位未知或范围未知均不能连接。不同明确尺度先作 Decimal 换算，不能直接相除。

重复资产标签逐行保存，不覆盖、不自动相加。`max_verified_restricted_money_fund_row_to_money_funds` 和 `max_verified_cash_subcategory_row_to_money_funds` 是各类别已核验**单行**比例最大值，不是总限制比例、完整披露认证或可自由支配资金。比例超过 1 保留未采纳 QC，不截断。

以下字段在所有公司均保持 null：`unrestricted_money_funds`、`cash_available_for_trading`、`total_restricted_money_funds`、`complete_interest_bearing_debt`、`cash_to_complete_interest_bearing_debt`、`financing_impact_coefficient`。`agent_signal_enabled=false`，`all_company_restrictions_completely_disclosed_verified=false`。

## 状态、时间和独立核验

108 家为有限非金融事实、1 家为金融比率排除、10 家余额或独立表读取 QC、3 家源读取 QC、1 家缺源。失败状态的采纳字段为空；不得将源记录保留解释为 SUCCESS。独立融资状态合同核验的 123 PASS 只说明采纳和留空正确，不解除原源失败。

每份修订报告只从自身 `available_at_proxy` 起可见；`state_asof` 要求有时区时间。同比/重述列不成为旧公开快照，半年与三季不拼分母。后回取字节不认证历史未变；本队列和已用日期属于开发，经济响应、因果和预测增量未知。

代码及来源均有哈希绑定，修改规则须另建版本。完整回归 741 项通过，正式原表读取与状态/合同均已逐字节重建；源日志、PDF 与渲染页位于忽略目录，跨机器须提供相同输入。来源说明及真实运行环境见上方报告。
