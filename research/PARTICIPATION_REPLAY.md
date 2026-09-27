# 用已知成交额约束的历史 Agent 回放

2026-09-27，在[事后容量诊断](CAPACITY_DIAGNOSTIC.md)发现大规模情景可能需要较高市场参与比例后，重新运行 2018 上半年、2020 一季度和 2020 后续时期的三类 Agent。三个时期沿用原回放的股票、行情收益、Agent 规则和参数；每只股票分别假设每类 Agent 有 1 亿或 10 亿元人民币初始资金。每个资金规模都计算无限制成交、1% 和 5% 三种容量情景，并分别保留市场信号及零信号路径，共 108 个情景。

容量预算在交易决策时只使用**信号截止日**的实际总成交额。按原回放的时序，信号截止日为收益日 `t` 前两交易日，执行参考日为 `t-1`：预算 = 假设参与比例 × `t-2` 已观察总成交额。三类 Agent 的买卖绝对订单额共用预算，超额时同比例缩小成交；未成交部分留在现金与持仓状态，由后续交易日重新决策。若 `t-1` 参考日停牌，则没有成交。当天 `t` 和 `t-1` 的成交额均不用于计算预算。价格继续沿用既有的实际收益路径，Agent 不能改变市场价格。

程序独立核对了无限制情景与原固定历史回放的各角色期末财富比例，并在每步检查现金、份额和费用账本。三个时期分别生成 36 个情景，含 4,284、2,088、6,660 条逐日情景记录；全部记录满足时间顺序、容量预算与账本约束。三份清单的输入、代码与输出哈希由 `configs/integrity_catalog_participation_replay.json` 核验为 3/3。与三份事后容量诊断合并后，还按固定输入独立重跑六项，12 份产物逐字节一致；见 `research_outputs/reexecution_catalog_capacity_series/reexecution_report.md`。

以平安银行 `000001`、市场信号路径、每类 Agent 每股假设 10 亿元为例：

| 时期 | 容量情景 | 触及上限的交易日 | 总成交额/总请求额 | 激进型期末财富/初始资金 |
|---|---|---:|---:|---:|
| 2018 上半年 | 不限 | 0/119 | 100.00% | 0.891 |
| 2018 上半年 | 1% | 117/119 | 5.90% | 0.906 |
| 2018 上半年 | 5% | 84/119 | 31.58% | 0.926 |
| 2020 一季度 | 不限 | 0/58 | 100.00% | 0.950 |
| 2020 一季度 | 1% | 58/58 | 5.38% | 0.945 |
| 2020 一季度 | 5% | 42/58 | 33.78% | 0.920 |
| 2020 后续时期 | 不限 | 0/185 | 100.00% | 1.091 |
| 2020 后续时期 | 1% | 181/185 | 5.79% | 1.128 |
| 2020 后续时期 | 5% | 147/185 | 31.38% | 1.135 |

这些结果说明容量假设会改变成交、后续持仓与财富路径，变化方向也不稳定。某一限制情景的期末财富更高，可能只是减少了风险暴露，不能称为策略改进或预测能力。1%/5% 是敏感性参数，**没有**从盘口或订单流估计；已观察的日总成交额不是可在参考收盘价成交的深度。归一化收益指数也不是实际成交价；回放未包含价差、队列、滑点和内生价格冲击，更未用真实投资者类别数据校准 Agent。因此这仍是历史收益路径上的机制实验，不能宣称复现了真实市场或形成监管预警。

在仓库根目录用新的空输出目录复现：

```powershell
python -m research.simulation.participation_replay research/configs/participation_replay_2018.json --output-dir research_outputs/observed_2018/participation_replay_v2
python -m research.simulation.participation_replay research/configs/participation_replay_2020_q1.json --output-dir research_outputs/observed_2020/participation_replay_q1_v2
python -m research.simulation.participation_replay research/configs/participation_replay_2020_later.json --output-dir research_outputs/observed_2020/participation_replay_later_v2
python -m research.registry.verify_catalog research/configs/integrity_catalog_participation_replay.json --output-dir research_outputs/integrity_catalog_participation_replay
python -m research.registry.reexecute research/configs/reexecution_catalog_capacity_series.json --output-dir research_outputs/reexecution_catalog_capacity_series
```

上述输出目录在本机已有产物，重跑时须选新目录并重新固定清单哈希。各目录的 `participation_report.md` 给出所有情景的可读汇总，`participation_results.json` 保存逐日决策、容量预算、成交与账本。
