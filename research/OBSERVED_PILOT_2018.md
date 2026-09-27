# 2018 资管新规节点：探索性事件研究与规则回放

本实验补入 2018 年历史样本，回答同一套市场模型和 Agent 规则在另一段真实收益路径上会产生什么结果。它没有验证政策因果效应、投资者行为拟合或市场预测能力。

## 事件定义与信息时间

选取 2018-04-27 正式发布的资管新规，作为金融去杠杆背景下的一个具体监管节点。中国人民银行[官方答记者问](https://www.pbc.gov.cn/jinrongwendingju/146766/146770/c2e228ad641d4a85acdc9142da4f955d/index.html)确认发布日期；页面标注 18:38:49，为收盘后。该页面时间不等于已独立确认的首次公开时刻。

配置使用发布日期、次日零点可见的规则，由下载的交易日历对齐至 **2018-05-02**。官方资料也说明，公开征求意见自 2017-11-17 开始，发布前已有信息与预期。本节点不能代表整个 2018 年去杠杆过程，也不是突然且完全外生的冲击。

## 行情和固定设置

- BaoStock 0.9.4；下载范围 2017-01-01 至 2018-12-31，487 个开市日。
- 沿用 2020 事件样本：平安银行 `000001`、万科 A `000002`、贵州茅台 `600519`；基准沪深 300 `sh.000300`。
- 每股 486 条对齐收益，共 1,458 条。股票采用提供方 `pctChg`，逐日与同日 close/preclose 核对；本次下载无相邻复权价断点。
- 万科原始数据有 5 个停牌日，均在 2017 年，零成交和同日价格持平检查通过；不在 2018 上半年回放区间。
- 估计窗口 120 日、间隔 5 日、事件窗口 [-3, +5]，与 2020 配置相同；实际窗口为 2018-04-25 至 2018-05-09。
- 原始响应、交易日历、提供方说明和政策页面均保存在本地；当前下载版本可能有历史修订，并非 2018 年当时留存的数据版本。

## 实际结果

CAR 为窗口内异常简单收益之和。下表把整个窗口拆成事件前和事件日及之后，防止把全部变化误归于政策发布。

| 股票 | 事前 [-3, -1] CAR | 当日及之后 [0, +5] CAR | 整个 [-3, +5] CAR |
|---|---:|---:|---:|
| 000001 | -5.3766% | -3.8980% | -9.2746% |
| 000002 | -5.6630% | -6.2021% | -11.8650% |
| 600519 | -2.0821% | +1.6173% | -0.4649% |

三股整个窗口的等权平均为 -7.2015%，包含发布前收益，不能解释为发布后的政策跌幅。三只股票非代表性抽样，尚未进行独立行情源交叉验证或有效统计显著性分析。

## 2018 成交活动与其他日期对照

同一下载版本的原始逐日响应包含成交股数和人民币成交额。逐行核对提供方代码、交易日历、单位与交易状态后，三股各 487 日，共 1,461 条；万科在 2017 年有 5 个停牌日，零成交检查通过。以事件估计期 120 个交易日的成交中位数为基期，2018-05-02 当日成交额倍数如下：

| 股票 | 当日成交量倍数 | 当日成交额倍数 | 事件窗口最高成交额倍数 |
|---|---:|---:|---:|
| 000001 | 0.877 | 0.781 | 1.784 |
| 000002 | 0.830 | 0.730 | 1.543 |
| 600519 | 1.463 | 1.388 | 1.771 |

成交额是双向总成交，不表示净卖出压力或可执行盘口深度。平安银行和万科的事件日成交额低于此前中位数，不能把收益窗口中的下跌直接解释成政策触发的单向抛售。

在相同市场模型与窗口参数下，把其他完整候选日期的估计期和事件窗口都与实际事件窗口（2018-04-25 至 2018-05-09）隔离。2017-09-01 至 2018-12-31 有事前 150、事后 27 个可比较日期，另有 142 日因隔离区间相交、5 日因窗口或拟合不完整被剔除。实际整个 [-3,+5] 窗口 CAR 的绝对值在事前候选日期中的排名比例为：平安银行 91.3%、万科 89.3%、贵州茅台 6.0%、三股等权均值 100.0%。事后分别为 100.0%、100.0%、3.7%、100.0%。这说明三股均值在所选日期集合里较极端，但候选窗口重叠、股票相关、前后市场环境不同，且事件前 CAR 已明显为负；这些比例**不是 p 值**，不构成政策因果识别或预测验证。

数值明细及输入/代码哈希位于 `research_outputs/observed_2018/activity/`、`activity_event/` 和 `placebo/`。原有 2020 诊断器的报告文案含特定武汉通告说明；2018 使用 `observed_diagnostics.py` 保留其经核验的数值算法、另生成事件中性的报告，并单独记录报告代码哈希。新增三项已并入主固定清单，18 项共通过 214 个文件引用核对；三项新增运行的 7 份产物从输入独立重跑均逐字节一致。这不证明统计或因果结论。

## Agent 回放

2018-01-02 至 2018-06-29 共 119 日，三只股票分别运行三类 Agent，并各自运行市场信号和零信号两个设置。角色参数、5 日动量、20 日波动率及 0.001 费率与 2020 一季度配置完全相同，没有按 2018 结果重新调参。

| 股票 | 激进型：市场 / 零信号末值比 | 保守型：市场 / 零信号末值比 | 机构型：市场 / 零信号末值比 |
|---|---:|---:|---:|
| 000001 | 0.891 / 0.898 | 0.938 / 0.928 | 0.911 / 0.882 |
| 000002 | 0.899 / 0.941 | 0.946 / 0.957 | 0.919 / 0.915 |
| 600519 | 1.016 / 1.023 | 1.011 / 1.013 | 0.996 / 1.013 |

末值比是期末财富 / 初始现金。参数未经真实交易或持仓校准，信号优势没有跨股票和角色一致出现。交易使用假设的参考收盘价，不改变历史价格；没有输入 2018 问答或政策语义。样本为事后补充的探索性回放，不是预注册留出实验。

## 复现与产物

已有原始下载和证据缓存时，在仓库根目录运行；回放须使用新的空输出目录：

```powershell
python -m research.data_pipeline.archive_evidence research/configs/observed_2018_evidence.json --output-dir research_outputs/observed_2018/evidence
python -m research.data_pipeline market research_outputs/observed_2018/download/market_import.json --output-dir research_outputs/observed_2018/prepared
python -m research.baselines.run_experiments research/configs/observed_pilot_2018.json --output-dir research_outputs/observed_2018/results
python -m research.simulation.historical_replay research/configs/historical_replay_2018.json --output-dir research_outputs/observed_2018/historical_replay
python -m research.data_pipeline.market_activity research/configs/market_activity_pilot_2018.json --output-dir research_outputs/observed_2018/activity
python -m research.baselines.observed_diagnostics activity research/configs/activity_event_pilot_2018.json --output-dir research_outputs/observed_2018/activity_event
python -m research.baselines.observed_diagnostics placebo research/configs/placebo_pilot_2018.json --output-dir research_outputs/observed_2018/placebo
python -m research.registry.verify_catalog research/configs/integrity_catalog_2020.json --output-dir research_outputs/integrity_catalog_2018_2020_v2
python -m research.registry.reexecute research/configs/reexecution_catalog_2020.json --output-dir research_outputs/reexecution_catalog_2018_2020_v2
```

以上是首次构建命令；对已固定的产物应使用下面的受控重跑，避免覆盖清单。没有行情时，可先安装 `requirements-market.txt` 并运行：

```powershell
python -m research.data_pipeline.fetch_baostock --stocks sz.000001 sz.000002 sh.600519 --benchmark sh.000300 --start-date 2017-01-01 --end-date 2018-12-31 --output-dir research_outputs/observed_2018/download
```

证据缓存还需取得上述人民银行页面和 BaoStock 文档；现有缓存的精确路径见 `configs/observed_2018_evidence.json`。重新下载的网页或行情不保证与固定版本字节一致，不能直接替换既有实验输入。

市场、事件、回放、成交导入、事件成交和日期对照均已加入主固定清单。完整性和独立重跑报告位于 `research_outputs/integrity_catalog_2018_2020_v2/` 与 `research_outputs/reexecution_catalog_2018_2020_v2/`。配置文件名保留 `_2020` 以兼容现有命令，清单已扩展至 18 项。

```powershell
python -m research.workbench.run observed_event_2018
python -m research.workbench.run historical_replay_2018
```

新版工作台可切换 2018 事件、2020 两种对齐和三个 Agent 回放时期，并展示各自的成交额倍数及日期对照。跨期波动、政策预期、其他事件和行业暴露还需要进一步研究；当前结果不支持图片中“高度贴合真实投资者决策”的结论。
