# 武汉独立公司样本：行情和无文本仿真基线

更新日期：2026-09-28。此基线沿用[独立语义验证](WUHAN_INDEPENDENT_VALIDATION_V3.md)事前固定的 126 家公司；行情下载、事件研究、逐股规则回放和多资产撮合都不读取该批模型预测或参考标签。目的是先把同池无文本路径和数据来源固定，供将来语义信号若通过门槛时配对比较；当前没有独立批次的真实 v3 文本路径。

## 行情来源与覆盖

BaoStock SDK 0.9.4 匿名接口取得 2019-06-01 至 2020-03-31 的 126 股、沪深 300 指数、证券目录和交易日历。清单 `research_outputs/wuhan_validation_market_download_2020_v1/download_manifest.json` 的 SHA-256 为 `9115ccdf1967e1907d203a94ebb458376c33039363529540ff18963b19ad9e8b`。查询记录 129 次，133 个来源产物逐项哈希与清单相符；股票代码及顺序与冻结公司池一致。提供方同日 `pctChg` 与 `close/preclose` 对照通过，调整后相邻报价差异检查无告警。这里保存的是 **2026-09-28 取得的当前历史版本**，不能证明 2020 年当时即可取得同样数据。

按[冻结的导入配置](configs/wuhan_validation_market_import_2020.json)标准化后，203 个交易日、126 家公司、24,706 条股票和基准对齐收益记录全部保留，行情数据集 ID 为 `bde7b2dfc0c513f8df26255e196ed51b217e178b4cb0e3575706b3bea69081c0`。两条绝对值超过 50% 的收益分别来自 `688168` 2019-09-06 上市首日 216.526% 和 `688123` 2019-12-23 上市首日 139.188%，未做事后缩尾或填补。股票日收益使用提供方复权查询的 `pctChg`，沪深 300 用价格指数收盘价推导；两者并非独立验证的同定义总回报口径。原始 CSV、标准化面板和清单均保留于 Git 忽略的本地 `research_outputs/`。

## 历史窗口与无文本路径

与旧 126 家样本相同的 2020-01-23 通告日期、120 日估计窗、5 日隔离和 `[-3,+5]` 事件窗口，固定在[独立事件研究配置](configs/wuhan_validation_pit_market_study_2020.json)。126 家中 119 家具备完整估计窗；`688021`、`688181`、`300789`、`688101`、`688168`、`300796`、`688123` 因较晚上市缺窗，7 条失败记录明确保存。全 126 家覆盖不完整，运行器将总体 CAR 均值设为不可用，不以 119 家结果冒充全样本效应，也不作因果解释。

[独立逐股规则配置](configs/wuhan_validation_pit_replay_2020.json)在 2020-02-12 至 2020-03-31 的 35 个共同交易日为 126 家均生成无文本路径。该价格接受型回放仅使用历史收益的滞后市场信号与零信号对照，三类 Agent 各自账户按手设规则交易；没有共享现金或跨股撮合。

[独立多资产无文本配置](configs/wuhan_validation_pit_portfolio_no_text_2020.json)把按代码排序的 126 家分成 42 个三股篮子，比较分账户与共享现金两种模式，共 84 条路径、2,940 个组合日和 8,820 次资产竞价。该路径由有限双边撮合内生形成价格，历史行情只给交易日、停牌及信息时点，**不把历史价格当模拟价格**。正式实验 ID `a0acdf5119c9a30957211bd0427795a3105d33c3ad18c624378a5ca29a129e45`。两种资金模式均请求并接受 3,167,800 股、成交 424,500 股，现金裁剪均为零；共同的平均终价/初价为 0.984866。两种结果相同，说明本组资金假设没有识别共享现金的独立效应，不是现实收益或疫情冲击估计。

独立账本审计复算了 140 份来源文件、全部 2,940 个组合日、8,820 次竞价、时点、反馈协方差、决策、现金与股份守恒，检查全部通过。正式产物在 `research_outputs/wuhan_validation_pit_portfolio_no_text_2020_v1/`，第二次独立执行在 `_v1_rerun/`；四份摘要、路径、报告和压缩日账本逐字节一致，日账本 SHA-256 为 `2a1b6e641a2098d724d40cd3085906428773243534f3e21e6d79d5e343689156`。两份运行各自的生成时间清单不同，不把清单也声称为逐字节一致。[固定完整性目录](configs/integrity_catalog_wuhan_validation_market_2020.json)对行情导入、事件研究、逐股回放与组合基线的四份清单核验为 4/4；这只证明当前本机字节与固定清单一致，不证明数据经济含义或因果结论。参数、报价响应和参与者持仓仍是未按真实订单校准的示意假设。

原组合基线从 2020-02-12 开始，因为严格的 20 个事前交易日波动率窗口会排除 2020-01-06 才上市的 `688181`。为覆盖 2020-01-23 通告日和春节后首周，另运行了[早期窗口暖启动诊断](simulation/wuhan_early_window_portfolio.py)，仍固定 126 家、42 个篮子和两种资金条件，窗口为 2020-01-23 至 2020-03-31，共 84 条路径、3,612 个组合日和 10,836 次资产竞价。暖启动只在截至 t-2 的历史不足 20 条时为波动率估计补零收益，至少需要两条历史收益；没有补交易日、价格或文本。只有 `688181` 需要补齐。达到严格 20 条历史后，126/126 家公司的步骤逐字段一致。

正式本机产物在 Git 忽略的 `research_outputs/wuhan_validation_pit_portfolio_early_2020_v5/`，实验 ID 为 `0a2bb7281ed848bcdb1baf3356e1d1a67a1f405f4fcfa5251b3dd05a5042ba08`，完整压缩逐日账本 SHA-256 为 `0e05c514a033565164979dfbe393be9d69a05e3b0dbc91d0d174485483f92b3d`。独立入口[重新核对账本](simulation/audit_wuhan_early_window_portfolio.py)：复算全部 3,612 个组合日和 10,836 次竞价，重新执行 Agent 决策，并核对来源、代码、无文本时点、内生反馈与协方差、成交、钱包/股份守恒、完整路径和终值摘要，全部通过。审计产物在 `research_outputs/wuhan_validation_pit_portfolio_early_2020_v5_audit_v1/`，审计 ID 为 `397e6c7851164a8599f7277fcf552b050a485bda5b8cb8dff775b797fee88a75`。这仍是机制基线，不是疫情因果估计或现实收益回放。与仅从 2 月 12 日开始的旧窗口相比，延长模拟区间后两种资金条件的平均模拟终价倍数均由 0.984866 变为 0.983098，激进、保守、机构三类 Agent 平均财富倍数差分别为 -0.000443、-0.000453、-0.000556。该对照同时增加早期模拟交易日并改变后续钱包状态，按窗口敏感性解释。

## 重现入口

所有输出目录须为新的空目录。先从[冻结公司池](configs/wuhan_qna_independent_validation_universe_2020.json)派生 126 个交易所代码，以 `research.data_pipeline.fetch_baostock` 同窗口重取行情；提供方可能修订历史数据，重新取得时应另存版本。已有**本机正式下载及标准化行情**可直接重跑下面的固定实验，配置始终读取正式清单 `research_outputs/wuhan_validation_market_prepared_2020_v1/market_manifest.json`：

```powershell
$py = 'C:\ProgramData\miniconda3\python.exe'
& $py -m research.baselines.run_experiments research/configs/wuhan_validation_pit_market_study_2020.json --output-dir 'research_outputs/replace-with-new-validation-event-study'
& $py -m research.simulation.historical_replay research/configs/wuhan_validation_pit_replay_2020.json --output-dir 'research_outputs/replace-with-new-validation-replay'
& $py -m research.simulation.wuhan_portfolio_baseline research/configs/wuhan_validation_pit_portfolio_no_text_2020.json --output-dir 'research_outputs/replace-with-new-validation-portfolio'
& $py -m research.simulation.audit_wuhan_portfolio_baseline 'research_outputs/replace-with-new-validation-portfolio' --config research/configs/wuhan_validation_pit_portfolio_no_text_2020.json --output-dir 'research_outputs/replace-with-new-validation-portfolio-audit'
& $py -m research.simulation.wuhan_early_window_portfolio research/configs/wuhan_validation_pit_portfolio_no_text_2020.json --output-dir 'research_outputs/replace-with-new-early-window'
& $py -m research.simulation.audit_wuhan_early_window_portfolio 'research_outputs/replace-with-new-early-window' --config research/configs/wuhan_validation_pit_portfolio_no_text_2020.json --output-dir 'research_outputs/replace-with-new-early-window-audit'
```

若要检查正式下载的标准化再生能力，可另运行 `& $py -m research.data_pipeline market research/configs/wuhan_validation_market_import_2020.json --output-dir 'research_outputs/replace-with-new-validation-market-prepared'` 并比较产物。事件研究因 7 家估计窗不足会给出 `partial` 并以非零进程码结束；这是保留失败与阻止不完整全样本均值的预期结果。下游配置已绑定本机正式导入的行情清单；若要使用新取得或新标准化目录，须生成新的配置并重新固定其来源哈希，不能拿新版本与上述实验 ID 混称为同一运行。
