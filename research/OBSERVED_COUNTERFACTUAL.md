# 2018/2020 已观察收益路径上的假设冲击实验

此实验把已存档的逐日股票和基准收益送入三类 Agent 的合成市场引擎，分别运行无事件信号和手设事件信号，并扫描价格冲击系数 `0`、`0.01`、`0.03`。它只回答：**在给定 Agent 规则和假设市场容量/冲击函数下，事件信号会如何改变订单与模拟价格？** 它不估计真实冲击系数，不识别政策因果效应，也没有把 DeepSeek 输出接入交易决策。

输入分别固定为 2018 资管新规、2020 武汉通告的日期保守事件口径及平安银行 `000001` 的已存档行情。两个场景均假设每类 Agent 每股资金 10 亿元、固定可成交规模 10 亿元、事件信号 `-0.8`、不确定性 `0.6`、持续三个信号截止日。这些数值都是**敏感性参数**，并非用订单簿、投资者持仓或人工语义标签拟合所得。

事件公开时间沿用 `baselines/run_experiments.py` 的规则：只有日期时，次日零点以后视作可见。每日决策读取截至收益日 `t-2` 的基准动量和波动率；在 `t-1` 的归一化参考价格成交，然后应用 `t` 的已观察收益及当次假设价格冲击。2018 年事件首次可用的截止日是 5 月 2 日，首次受情景信号影响的收益日是 5 月 4 日；2020 年相应日期为 2 月 3 日和 2 月 5 日。事件日和可见时间来自已缓存、哈希核验的公开材料；这仍不能确认首次公开时刻。

每个事件有 `3 个冲击系数 × 2 条信号路径 = 6` 条模拟路径。冲击系数为零时，两条价格路径均等于已观察收益连乘的归一化指数。假设系数为 `0.03` 时，事件信号相对无信号路径的配对差异如下：

| 场景 | 情景窗口净订单差 | 情景末日价格指数差 | 全期末价格指数差 |
|---|---:|---:|---:|
| 2018 资管新规 | -3.325 亿元 | -0.8510 | -0.0094 |
| 2020 武汉通告 | -2.580 亿元 | -0.6952 | -0.0065 |

不同时间点的差异方向和幅度不相同，说明不能只看全期末值判断事件响应。完整六路径表、逐日订单/成交/价格/账本以及输入与代码哈希分别在 `research_outputs/observed_2018/counterfactual_assumed_impact_v4/` 和 `research_outputs/observed_2020/counterfactual_assumed_impact_v4/`。两份固定清单经 `integrity_catalog_observed_counterfactual.json` 核验为 `2/2`；再从相同输入在 `counterfactual_verified_rerun_v1/` 新目录各执行一次，四份结果与报告产物逐字节一致。研究工作台 v7 只在两份原清单和重跑产物均核对通过后展示六条配对摘要，不导出逐日路径。现有通用重跑目录尚未包含这两项，逐字节比对是单独执行的。

使用前须有本机 `research_outputs/` 中的行情、成交活动和事件证据存档。在仓库根目录运行，输出目录须为空：

```powershell
python -m research.simulation.observed_counterfactual research/configs/observed_counterfactual_2018.json --output-dir research_outputs/observed_2018/<新目录>
python -m research.simulation.observed_counterfactual research/configs/observed_counterfactual_2020.json --output-dir research_outputs/observed_2020/<新目录>
python -m research.registry.verify_catalog research/configs/integrity_catalog_observed_counterfactual.json --output-dir research_outputs/<另一新目录>
```

这不是已验证的历史市场仿真。实际收益本身已经反映真实市场全部交易，再叠加合成冲击可能重复计入部分运动；固定流动性、线性冲击、归一化成交价及外部对手方也都未经校准。因此模拟价格只能作为**机制与参数敏感性**，不能用于市场预测或监管预警。若要验证图片中“三类真实交易主体”和“提前发现市场变化”的主张，仍需行为类别或持仓/订单证据、订单流和盘口、独立语义标签，以及跨期留出检验。
