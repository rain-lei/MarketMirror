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

三股整个窗口的等权平均为 -7.2015%，包含发布前收益，不能解释为发布后的政策跌幅。三只股票非代表性抽样，尚未进行 2018 日期对照、独立行情源交叉验证或统计显著性分析。

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
```

以上是首次构建命令；对已固定的产物应使用下面的受控重跑，避免覆盖清单。没有行情时，可先安装 `requirements-market.txt` 并运行：

```powershell
python -m research.data_pipeline.fetch_baostock --stocks sz.000001 sz.000002 sh.600519 --benchmark sh.000300 --start-date 2017-01-01 --end-date 2018-12-31 --output-dir research_outputs/observed_2018/download
```

证据缓存还需取得上述人民银行页面和 BaoStock 文档；现有缓存的精确路径见 `configs/observed_2018_evidence.json`。重新下载的网页或行情不保证与固定版本字节一致，不能直接替换既有实验输入。

市场、事件和回放分别登记为 `observed_market_2018`、`observed_event_2018`、`historical_replay_2018`，并已加入固定清单。完整性和独立重跑报告位于 `research_outputs/integrity_catalog_2018_2020/` 与 `research_outputs/reexecution_catalog_2018_2020/`。配置文件名保留 `_2020` 以兼容现有命令，清单已扩展至 15 项。

```powershell
python -m research.workbench.run observed_event_2018
python -m research.workbench.run historical_replay_2018
```

工作台可切换 2018 事件、2020 两种对齐和三个 Agent 回放时期。2018 成交额倍数及日期对照尚无产物，会明确显示缺失状态。跨期波动、预期、其他事件和行业暴露还需要进一步研究；当前结果不支持图片中“高度贴合真实投资者决策”的结论。
