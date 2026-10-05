# 半年报融资因素增量实验合同

版本为 `issuer-financing-prespecified-paired-risk-blocks-v1`，冻结定义见[配置](../configs/issuer_financing_increment_2019.json)，数值、覆盖与限制见[实验结果](../ISSUER_FINANCING_INCREMENT_2019.md)。此出口为已使用开发窗口的配对风险诊断，`agent_signal_enabled=false`。

## 源、时钟和配对

- 原 123 家、43 个日期与所有 5,289 个公司日期位置保留；不与另一批 126 家合并。原财务实验的行业、市场、金额、目标与自身可见时钟逐字节绑定并独立重验。
- 固定 29 个训练日期，截至 2019-12-11；12-12 目标隔离；13 个评估日期 12-13 至 12-31。拟合、均值、尺度仅用首个评估 `t-2` 截点前可见目标。
- H1 状态须同一股票、明确六月末合并范围、源读取与独立余额 PASS、非金融通用适用。每份自身公开日期代理不晚于行的信号截点；原基线排除不能恢复。
- 三季度控制和 H1 新增因素各有来源与时钟。任何 H1 比例都不把 Q3、母公司或比较列作为分母。
- 每组七个参考与新增模型在完全相同公司日期重建。受限资金主组与交互组资格严格相同；期限组较小交集，跨组绝对误差不可直接比较。

## 固定字段与缺项

| 组 | 新增字段 |
|---|---|
| `primary_restricted_cash_buffer` | `h1_max_verified_restricted_money_fund_row_to_money_funds`；`h1_money_funds_to_assets` |
| `secondary_maturity_cash_coverage` | `h1_short_term_borrowings_to_assets`；`h1_due_within_one_year_noncurrent_liabilities_to_assets`；`h1_money_funds_to_current_liabilities` |
| `secondary_restriction_market_interactions` | 主组字段；`h1_restricted_row_x_benchmark_volatility_20`；`h1_restricted_row_x_log_amount_surprise_20` |

受限比例仅是最大已核验货币资金披露单行/同源总货币资金，原行范围、币种和尺度明确。不得求和重复行、混入现金子类、推断完整受限比例或自由现金。期限分项不得升格为完整有息债务或精确借款期限。交互只用原滞后市场特征，不读取当日目标或同日冲击。

任一固定字段缺失，整组该公司日期标记排除，`features`、`target_absolute_return`、`financial_decimal_inputs` 和 `financing_decimal_inputs` 全部 null，基线也不拟合该位置。原图仍保留身份、来源、时钟、排除原因；真实数字 0 留为数值 0，缺行、横线和 QC 不补 0。未明币种的同一张表无量纲比例可使用已核验同单位分子/分母；跨表限制关系另外要求人民币/尺度证明。

## 模型、核验和解释

固定无惩罚线性模型；常数列或秩不足报错，不自动删列、换惩罚或挑组。负绝对收益预测保留并计数，不截断。公司固定效应市场模型只作参考，没有把静态 H1 字段重复加入。

输出为 `panel.json`、`results.json`、`manifest.json`、`independent_audit.json`，新目录/文件排他写入；重跑只能比较冻结字节，不能覆盖。 manifest 绑定源文件、代码和配置；旧试验及旧源状态不改写。

独立审计不导入生产融资代码，重建原完整 raw CSV/Decimal/SVD 审计和融资状态审计，从独立当前金额及原限制行重算因素，再用 SVD 核对全部模型、预测、指标与删除日期的敏感性。合同 PASS 说明实现和计算一致，不说明经济效果通过。

主组为事先指定，次组为探索性；所有结果保存。删除单个评估日只删除误差样本，不重训，范围不当作置信区间。来源完整性交集及已使用日期有限；不能外推政策因果、股价方向、投资者响应、市场校准或监管预警。实际本轮没有稳定 MAE/RMSE 共同改善，经济信号继续关闭。
