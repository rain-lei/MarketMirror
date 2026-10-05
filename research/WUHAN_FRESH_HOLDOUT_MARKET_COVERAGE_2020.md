# 新留出组行情覆盖与无文本基线输入

更新日期：2026-09-29。本说明记录新留出组的市场数据采集与无文本基线。采集阶段只处理公开行情，没有读取问答文本、参考标签或模型输出，也没有向 AI 服务发送内容。之后该组另行完成了一次模型评估；样本与评分边界见[留出集评估记录](WUHAN_FRESH_HOLDOUT_RESERVE.md)。

## 来源覆盖

按新留出名单和已有武汉基线窗口，尝试从 BaoStock 匿名接口获取 2019-06-01 至 2020-03-31 的股票日收益、沪深 300 指数和交易日历。`sz.200468`（B 股）不在当前 `query_stock_basic()` 股票目录中；对它直接发起同窗口 `query_history_k_data_plus` 请求时，接口返回 `error_code=0`、`error_msg=success`，但没有任何行情行。因此没有把空序列伪装成有效行情，也没有从冻结名单中删除该公司。

可用行情下载覆盖其余 **125/126** 个冻结代码。下载清单记录 128 次查询，132 个源文件的 SHA-256 均与清单相符；沪深 300 和日历各有 203 个交易日。标准化结果有 125 个代码、24,828 条对齐收益记录。股票使用 BaoStock `adjustflag=1` 查询的 `pctChg`，指数收益由指数收盘价计算；两者不是同一定义的总回报序列。数据是 2026-09-29 取得的当前历史版本，可能含历史修订。

本机下载清单 SHA-256：`f7a292ce3cbeb4c03c32c1706f6ebd53149ffd9b4d761acb012bae3db796ff00`。由固定导入配置生成的数据集 ID：`42e5a9974705190b048be8dbb27c36e8dadf1ca8005e7231115ce01c0ec276b2`；标准化清单 SHA-256：`7dd9ac1afa828adc143c58ef546ad292ce4fd9ff93edd8da9b960ed416cd5f85`；行情面板 SHA-256：`67582fd9378ab6401208b8a1917b6033f2789dcc6b432f30f42160e1d3826c87`。原始行情和标准化面板均位于 Git 忽略的 `research_outputs/`；完整成员仍以[冻结名单](configs/wuhan_qna_fresh_v3_holdout_universe_2020.json)为准。

## 使用边界

126 家的选择没有改变。上文同源行情基线必须标为 **125 家有源行情覆盖**，并单列缺失代码 `200468`；不得描述为完整 126 家结果。补入新浪行情后形成的 126 家面板见下文，属于混合来源敏感性分析。该留出组已另有一次模型评估，但未通过类别门槛，因此没有语义信号；市场覆盖本身不构成预测、因果或收益证据。

严格滞后 21 个收益观测的逐股规则回放从 `2020-02-21` 起跑：只有 `603551` 在原 `2020-02-12` 起点前不足预热数据，因此统一延后起点，而没有缩短波动率窗或补零。事件研究仍使用原冻结事件日和估计窗；完整估计窗不足的股票单列失败，总体均值保持不可用。

已按固定配置执行[事件研究](configs/wuhan_fresh_v3_holdout_market_study_2020.json)：125 家中 120 家有完整估计窗，`300792`、`688139`、`603551`、`002966`、`688002` 因 IPO 较晚缺少完整 120 日估计窗。运行状态为 `partial`；程序没有把 120 家的结果冒充完整 125 家总体效应。实验 ID 为 `b14eb29d093f84916aef0f177861f92c7e7a53a6e8e06867886f5ecb80e986a1`。两次独立执行的事件收益 CSV 和报告逐字节一致；结果 JSON 仅 `generated_at` 不同，去除该生成时间后的内容 SHA-256 为 `4af98b55c5c86fb0abd85d37634f2fb4743a42a70760910b6ded36276d8715eb`。

[无文本逐股规则回放](configs/wuhan_fresh_v3_holdout_replay_2020.json)固定从 2020-02-21 跑到 2020-03-31，125 家各 28 个交易日。市场信号只使用 t-2 及更早信息，另保留零信号对照。回放 ID 为 `c4914d954567ab29d19162ebe793bced1c4ec72b8918f3b06fa609dd47f63496`；正式结果及报告分别与独立重跑逐字节一致，哈希为 `4f1fd561380cc4010b0caae19d4102248838c447bcd01edd733afad13397b640` 和 `04132a0e78d73b5aaaadb0b457da520e0ac77ac11e6b918faf495be33b25b88f`。产物保存在 Git 忽略目录 `research_outputs/wuhan_fresh_v3_holdout_market_study_2020_v1/` 和 `research_outputs/wuhan_fresh_v3_holdout_market_replay_2020_v1/`。

在 125 家同源行情阶段，当时没有运行多资产组合，因为可用代码不能按原方案完整划分为三股篮子。之后补齐 `200468` 的第二来源行情，并完成全 126 家无文本组合基线，结果见下文。逐股规则回放只建立机制基线，不代表真实收益或疫情因果效应。

重跑标准化导入：

```powershell
$py = 'C:\ProgramData\miniconda3\python.exe'
& $py -m research.data_pipeline market `
  research/configs/wuhan_pre_event_fresh_v3_holdout_market_import_2020.json `
  --output-dir 'research_outputs/replace-with-new-fresh-market-import'
& $py -m research.baselines.run_experiments `
  research/configs/wuhan_fresh_v3_holdout_market_study_2020.json `
  --output-dir 'research_outputs/replace-with-new-fresh-event-study'
& $py -m research.simulation.historical_replay `
  research/configs/wuhan_fresh_v3_holdout_replay_2020.json `
  --output-dir 'research_outputs/replace-with-new-fresh-market-replay'
```

如需复现上文 125 家同源基线，先从冻结名单派生交易所代码并显式排除 `200468`；下载目标必须为空，不能覆盖归档。完整 126 家敏感性面板使用下方单独记录的混合来源流程。

## 2026-09-29：126 家混合来源敏感性扩展

上文 125 家 BaoStock 结果保留为同源基线。为保留全部 126 家成员及 42 个三股篮子，另从新浪公开历史 K 线端点补取 `sz200468`：`https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol=sz200468&scale=240&ma=no&datalen=3000`。请求未提供复权参数；系统据连续收盘价计算简单 close-to-close 收益，并要求日期与 BaoStock 交易日历逐日完全匹配。窗口内 203 个交易日全部匹配，无补值或跨日桥接。原始响应 SHA-256 为 `b75ab29964608be69619af6a9984c863aefc1c624e60d7373b5baba1063cef81`。

组合下载清单 SHA-256 为 `9f4d2e2f042af346c261677fc627268f5fcf845693293056785c649370588817`，合并收益面板 SHA-256 为 `c5895939b6a3cdc9f8e5a386385bcb97e26bee3fd6f4a54623305d5fd4a7f19c`。标准化数据集 ID 为 `5db82b68b148af90c2fe4ee89f12718d6a931a59439fe7e955e181d81dcbe40d`，面板清单 SHA-256 为 `c4478d24c27bea589373ffe1cfa7dfb8905f04f3edd03dcbeb59c2012330e3da`，对齐后 126 家共有 25,030 条股票收益行和 203 个日历交易日。股票收益口径明确标记为 `mixed_adjusted_and_unadjusted_price_return`：125 家为 BaoStock `pctChg`，`200468` 为新浪未复权收盘价收益。这是完整成员敏感性扩展，不是同质行情面板；B 股复权与公司行为差异仍可能影响可比性。

按 [126 家事件研究配置](configs/wuhan_pre_event_fresh_v3_holdout_market_study_2020_126.json)重跑，121 家估计窗完整，5 家因窗口不足保留失败：`300792`、`688139`、`603551`、`002966`、`688002`。整体保持 `partial`，没有把部分公司均值称为完整样本效应。实验 ID 为 `c4876dbad3a244819df386e936dafc535d036443c9e55be2ea459d30f1efbe4e`；两次运行的异常收益 CSV 与报告逐字节一致，去掉生成时间后的结果内容哈希为 `fba1897c894c3ea3897d22945bf0b001c7586da6d573f39f15554c7754cf8351`。

[126 家无文本规则回放](configs/wuhan_pre_event_fresh_v3_holdout_replay_2020_126.json)覆盖 `2020-02-21` 至 `2020-03-31`，每家公司 28 个交易日；信号严格只用 t-2 及更早的市场信息。回放 ID 为 `5cd214b05a378ae117afab2392a0871aad27527eb750b6b4c4e30836e364102b`，结果和报告 SHA-256 分别为 `350667332345fcf1a16bfdb468111a42452c6635e418db5bf5ae2a6feb8003e6` 与 `592a7da79ca3aa80cfd5ff4f3a989a210aa48b68461e599260ba76f351c305fd`；独立重跑逐字节一致。

[无文本多资产组合基线](configs/wuhan_pre_event_fresh_v3_holdout_portfolio_no_text_2020_126.json)现覆盖冻结的完整 126 家：42 个三股篮子、共享/分账户两类条件共 84 条路径、2,352 个组合日和 7,056 次资产竞价。独立账本审计重建了全部路径及来源，七项检查全通过。两类资金条件在当前假设下完全相同，现金裁剪为 0；平均终值价格倍数为 0.9878，策略成交/接受比例为 13.55%。这表示该组参数没有触发共享现金约束，不能解释为共享现金效应或现实市场拟合。基线实验 ID 为 `2e0ae6c85f8160f99e0070badaa45a65534d14ad1932a924e3dc89a3889d6593`。

下载、标准化面板、事件研究、回放和组合产物都保存在 Git 忽略的 `research_outputs/`。125 家同源基线与 126 家混合来源扩展必须分开引用；任何图表、指标或模型训练都需显示该口径差异。
