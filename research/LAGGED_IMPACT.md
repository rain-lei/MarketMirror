# 滞后成交额容量与假设冲击情景

固定流动性实验假设每天可成交 10 亿元；本实验单独检验这个假设。它读取 2018 年资管新规和 2020 年武汉通告场景中平安银行 `000001` 的已存档日收益，在三类 Agent 的合成订单上施加逐日变化的容量与价格冲击。

每天仅使用信号截止日 `t-2` 的**历史双向总成交额**：乘以 1% 或 5% 形成 `t-1` 参考收盘的共享成交预算；另乘以独立的 1% 或 5% 形成假设冲击分母。对每种组合扫描冲击系数 `0`、`0.01`、`0.03`，并配对有/无手设事件信号，共 `2 × 2 × 3 × 2 = 24` 条路径/事件。每类 Agent 假设资金 10 亿元，信号 `-0.8`、不确定性 `0.6`、持续三次决策。比例、系数、信号和资金均非估计值；冲击分母比例不表示真实盘口深度。参考日停牌时无成交，零成交额时容量和冲击为零。

2018 年首次可用信号截止日为 5 月 2 日，首次受信号影响的收益日为 5 月 4 日；2020 年分别为 2 月 3 日、2 月 5 日。两个实验所有零冲击路径都复现了已观察收益连乘价格。以冲击系数 `0.03` 为例：

| 场景 | 容量比例 / 冲击分母比例 | 情景末日价格指数差 | 全期末价格指数差 |
|---|---:|---:|---:|
| 2018 | 1% / 1% | -13.0841 | -2.3933 |
| 2018 | 5% / 5% | -2.9562 | +0.8990 |
| 2020 | 1% / 1% | -3.7415 | -2.6021 |
| 2020 | 5% / 5% | -4.4335 | +1.9206 |

同一事件在不同假设下，期末配对差异甚至可能改变方向。因此这些指数差不能解释为真实市场价格预测。逐日的订单、容量、冲击、财富和账本在本地 `research_outputs/observed_2018/lagged_impact_v1/` 与 `research_outputs/observed_2020/lagged_impact_v1/`；各自 24 条路径的报告为 `lagged_impact_report.md`。两份运行清单由 `integrity_catalog_lagged_impact.json` 固定，结果和报告在两个独立重跑目录中逐字节一致。

从仓库根目录重跑（需要本机已存档行情、成交额和事件证据；输出目录须为空）：

```powershell
python -m research.simulation.lagged_impact research/configs/lagged_impact_2018.json --output-dir research_outputs/observed_2018/<新目录>
python -m research.simulation.lagged_impact research/configs/lagged_impact_2020.json --output-dir research_outputs/observed_2020/<新目录>
python -m research.registry.verify_catalog research/configs/integrity_catalog_lagged_impact.json --output-dir research_outputs/<另一新目录>
```

实际收益本身已经包含市场交易，再叠加模型冲击可能重复计数。更早的总成交额也不是实际可执行买卖深度；模型仍以归一化参考价格、线性冲击和外部对手方成交，三类 Agent 尚未按真实投资者订单校准。该实验只能揭示假设敏感性，不能证明历史市场拟合、事件因果、预测或监管预警。
