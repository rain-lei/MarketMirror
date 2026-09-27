# 历史 Agent 回放的成交容量诊断

2026-09-27，在已固定的 2018 上半年、2020 一季度及 2020 后续时期三段价格接受型回放上，追加一项**事后**容量检查。旧回放用归一化收益指数作为成交参考价，且默认全部订单成交。新诊断只把每位 Agent 在每只股票上的实际回放成交额除以其示意初始资金，再乘以事先列出的假设资金规模（每类 Agent 分别为 100 万、1 亿或 10 亿元人民币）；三类 Agent 的绝对成交额相加，与同一股票同日 BaoStock 实际**总成交额**比较。市场信号与零信号路径分别计算。它没有更改原回放的决策、价格或财富路径。

此比例是历史回放的**容量敏感性**，不是可成交深度：当日总成交额在决策时未知，包含市场所有买卖双方；Agent 之间的潜在内部对冲未扣除。小于 1% 不能证明订单能以参考价成交，大于 5% 也不是机械的拒单门槛。没有订单簿、买卖净方向、投资者类别与滑点数据，不能据此估计价格冲击、复现参与者决策或声称监管预警。

## 实际结果

三段时期共生成 54 个股票 × 信号路径 × 假设资金情景；每个情景逐日保存交易日、实际成交额、假设总成交额和比例。2018 为每股 119 日，2020 一季度为 58 日，后续时期为 185 日；这几段已运行窗口内未出现停牌仍交易的回放记录。下面列出市场信号路径中平安银行 `000001` 的例子：

| 时期 | 每类 Agent 每股假设资金 | 参与比例 95 分位 | 最大值 | 超过实际成交额 5% 的天数 |
|---|---:|---:|---:|---:|
| 2018 上半年 | 1 亿元 | 2.96% | 4.95% | 0/119 |
| 2018 上半年 | 10 亿元 | 29.55% | 49.46% | 69/119 |
| 2020 一季度 | 1 亿元 | 3.00% | 3.50% | 0/58 |
| 2020 一季度 | 10 亿元 | 29.99% | 35.05% | 36/58 |
| 2020 后续时期 | 1 亿元 | 2.87% | 8.14% | 2/185 |
| 2020 后续时期 | 10 亿元 | 28.70% | 81.35% | 120/185 |

这表明原先“不限容量、按参考价全部成交”的设定在大规模情景下缺乏可信支撑。下一步若要模拟较大资金，必须先引入可核验的订单/盘口数据或明确的容量约束与滑点情景，再重新计算 Agent 路径；现有比例不能直接填作流动性或价格冲击参数。

## 复现与范围

固定配置和代码分别位于 `configs/capacity_diagnostic_2018.json`、`configs/capacity_diagnostic_2020_q1.json`、`configs/capacity_diagnostic_2020_later.json` 与 `simulation/capacity_diagnostic.py`。运行清单、原回放、实际成交额和输出的 SHA-256 都写入产物；三项新增运行由 `configs/integrity_catalog_capacity_diagnostic.json` 核验为 3/3。

```powershell
python -m research.simulation.capacity_diagnostic research/configs/capacity_diagnostic_2018.json --output-dir research_outputs/observed_2018/capacity_diagnostic_v2
python -m research.simulation.capacity_diagnostic research/configs/capacity_diagnostic_2020_q1.json --output-dir research_outputs/observed_2020/capacity_diagnostic_q1_v2
python -m research.simulation.capacity_diagnostic research/configs/capacity_diagnostic_2020_later.json --output-dir research_outputs/observed_2020/capacity_diagnostic_later_v2
python -m research.registry.verify_catalog research/configs/integrity_catalog_capacity_diagnostic.json --output-dir research_outputs/integrity_catalog_capacity_diagnostic
```

输出目录必须为空。三个 `capacity_report.md` 是可读摘要，`capacity_results.json` 给出逐日记录。Git 只保存配置、代码和固定清单哈希，不保存本机原始资料。
