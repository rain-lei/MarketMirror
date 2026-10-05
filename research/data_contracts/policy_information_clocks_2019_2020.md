# 四组政策信息时点与补源合同

本合同约束[时点交叉协议](../configs/policy_information_clock_contrast_2019_2020.json)、[补源修订协议](../configs/policy_information_clock_completion_2019_2020.json)与[事件支持协议](../configs/policy_event_support_2019_2020.json)。结果和初始失败均见[完整报告](../POLICY_INFORMATION_CLOCK_CONTRAST_2019_2020.md)。全部产物 `agent_signal_enabled=false`；结果不生成股票方向或 Agent 参数。

## 来源事实和期限

完成版共有 212 份 HTML，包含 204 份普通 OMO 标题公告和 8 份原始 LPR 公告，共 111 行利率观察。新增 34 份页面的实际范围为 2019-06-17 至 07-31，不认证其他政策渠道或未归档日期覆盖完整。

原 178 份记录的标题、正文、签署日期、页面时钟、原始字节和操作字段不变；仅可修改由新增历史证据产生的 `previous_observation` 与 `change_from_previous_observed_bps`。每条修订保存旧/新值及前值来源，首次观察仍缺前值时为 `null`。同期限、同工具的已观察差才可用基点表达；补出相同前值可支持 0，缺来源不能填 0。

混合 MLF/TMLF 原文按紧邻表格标题建立身份。普通 MLF、TMLF、逆回购、LPR、香港票据分别维护历史。TMLF 的 `tenor_value`、`tenor_unit` 表示原文名义期限，另保存 `renewal_count_stated`、`actual_term_years_stated`、`tenor_qualifier_text` 和 `table_heading`；名义一年、可展期两次、实际三年不得丢失或改为普通三年 MLF。操作总量不能当成净流动性，标题提及到期不创造当日操作。

## 时点面板

`clock_panels.json` 包含按四个版本分组的 `calendars`、`policy_vectors` 和 `model_rows`，每组 139 个相同目标交易日。

| 字段 | 定义 |
|---|---|
| `trade_date` | 目标简单收益的交易日，四组目标及日期一致 |
| `signal_cutoff_date` / `policy_cutoff_timestamp` | 指定前一/前二交易日的日期末，上海时区；政策只能在此刻之前可见 |
| `market_feature_cutoff_date` / `market_feature_cutoff_timestamp` | 市场特征另设的前一/前二交易日末，不与政策字段混用 |
| `execution_reference_date` | 前一交易日，仅为原收益参考，不是取得日末信息后可成交的证明 |
| `baseline_features` | 截距、截至市场截点的 5 日简单动量、20 日收益样本标准差、20 日平均绝对收益 |
| `policy_features` | 按冻结顺序的 MLF 一年、逆回购 7/14 天、LPR 一年/五年以上首次可见变化，单位基点 |
| `target_return` / `target_absolute_return` | 目标日简单收益及其绝对值，只作为响应，不作为输入；绝对收益不是波动率 |
| `intrinsic_exclusion_reason` | 本版本新政策前值未知或市场历史不足的原因；无排除为 `null` |
| `paired_exclusion_reason` | 任一版本排除的日期在全部版本同时排除的原因；不依据目标收益大小挑选日期 |

时钟只支持整数 1/2 个交易日，禁止同日或未来截点。政策新可见变化只触发一次，后续继承最新观察不是重复冲击；首个截点之前的历史只初始化状态。零脉冲仅说明当前有限目录没有新的已知变化，不说明全部政策无变化。

3 月 30 日的 7 天下降 20 基点在前一日时钟下于 3 月 31 日可见；前两日时钟最后信息日为 3 月 27 日，故该事件在末端删失。相同目标日期不能描述成相同事件数。

## 拟合、缺失和分阶段产物

训练截止 2020-01-22，检查从 01-23 开始。初始 178 份来源交叉版共同排除 2019-09-20、23，剩 94/43 日；两个政策前一日设计秩 8/9，必须保留空 `targets`、`coefficients_fitted=false`，禁止发布整体时点比较。满秩的另外两组不构成全部四组完成的替代。

补源后 96/43 日共同配对，无排除。所有基线 4 列、增量 9 列均用精确 Fraction 与 NumPy 检查满列秩后才拟合。OLS 只使用训练段；不搜索参数，负绝对收益预测原值保留并计数。`r2_against_training_target_mean` 参考训练均值，不能误写成以检查均值为基准的通常 R²。

完成版在独立新目录保存四份文件和来源/代码哈希，初始失败版及此前 95/43 日对照不覆盖。各阶段样本数不同，误差不能跨阶段冒充同日期增量。

## 事件支持诊断

仅从面板读取日期、设计列和缺失字段，`target_response_fields_used=[]`、`coefficients_refitted=false`。每个非零政策日期单独删除，以 Fraction 与 NumPy 比较政策五列和完整九列秩；另以事件单位向量的增广秩证明是否位于政策列空间，并用 SVD 投影对角复核。投影有效性及数值容差为 `1e-10`。

当前四组每组五个支持日期，20 次删除均让完整设计秩从 9 降至 8；20 个单位向量全部在政策列空间，事件杠杆值全部接近 1。这说明设计可以完全拟合这些训练日期的任意响应，并非真实事件预测、政策意外或因果作用证据。不能在删除失败后改为合并系数或选最佳版本而不另立协议。

独立来源/预测审计从原始 HTML 与行情 CSV 重建，不调用生产解析器、时钟、特征或拟合函数；事件支持检查为另一个只使用设计的诊断。原始目录被 Git 忽略，代码可读不等于跨机器原文已可获取。
