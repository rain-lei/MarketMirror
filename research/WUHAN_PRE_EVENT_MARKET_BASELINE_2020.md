# 武汉事件前问答样本的行情基线

更新日期：2026-09-28。

本记录把[事前问答活跃公司样本](WUHAN_PRE_EVENT_QA_SNAPSHOT_2020.md)的 126 家公司接到同一份来源留存的日行情面板，运行固定窗口市场模型事件研究，以及不使用文本信号的三类 Agent 价格路径回放。回放对每家公司分别运行单股票路径；公司间不共享现金或持仓，也没有撮合成一个共同市场。它补齐了问答快照之后的行情基线，尚未把 DeepSeek 输出接入决策。

## 样本与行情

126 家公司完全沿用问答样本配置，不按事件后回复、上市后的收益表现或事件结果再次选股。行情通过 BaoStock SDK 0.9.4 取得，窗口为 2019-06-01 至 2020-03-31，基准为沪深 300 指数 `sh.000300`。下载清单另存提供方证券目录、交易日历和每个代码的原始逐日响应；历史行情属于当前提供方版本，不是 2020 年当时归档的行情版本。

下载包含 126 家股票、基准和 203 个开市日。来源目录中的 133 个产物哈希全部通过；标准化时核验了 3 个来源文件哈希，并生成 25,159 条对齐收益记录。交易日历和证券代码逐项检查，不填补缺失值、不跨停牌日拼接收益，也不缩尾。提供方的 126 条股票回复均通过同日 `pctChg` 与 `close/preclose` 对照和相邻报价检查。三条绝对值超过 50% 的收益分别是三家科创板公司的首个上市交易日收益：`688018` 为 2019-07-22 的 106.246%，`688138` 为 2019-11-20 的 122.665%，`688081` 为 2020-01-06 的 86.919%；均保留在原始面板，不做事后平滑。

股票收益取提供方后复权查询的日 `pctChg` 百分数，基准收益由提供方指数收盘价计算。两类收益口径并不等于经独立确认的总回报序列。证券 IPO/退市日期用于限定请求区间，证券目录的当前状态没有用于决定样本资格。来源含义和口径以[提供方 API 文档](https://www.baostock.com/mainContent?file=pythonAPI.md)及本地下载清单为准，哈希校验只证明文件未变，不证明提供方历史数据无修订。

## 武汉事件研究

主事件定义仍是 2020-01-23 通告日期的保守版本。首次公开的精确时刻未存档，所以只按发布日期并从次日视为可见；春节休市后，首个可用市场日为 2020-02-03。市场模型采用 120 个估计交易日、5 日隔离、事件前 3 日和事件后 5 日，共 9 个事件窗口交易观测。

固定的 126 家中有 123 家具备完整估计窗口。`688018`、`688138` 和 `688081` 因 2019 年或 2020 年较晚 IPO 而缺少 120 个事前估计交易日。运行器保留三家失败记录，并将全样本 CAR 均值设为不可用；因此不把 123 家可用公司的均值冒充 126 家样本的结果，也不据此做总体推断。单家公司 CAR 只是市场模型的描述性残差，未给出显著性或因果解释。

复跑使用[冻结的事件研究配置](configs/wuhan_pre_event_pit_market_study_2020.json)。成功的 123 条结果、3 条窗口失败及完整运行清单位于 Git 忽略目录 `research_outputs/wuhan_pit_event_study_2020_v2/`。保守日期口径不是首次公开时刻，日收益还混合了当日公告前后价格变动。

## 无文本 Agent 价格路径回放

回放使用同一批 126 家股票收益与沪深 300 基准，比较三类规则 Agent 的滞后市场动量信号和零信号对照。每家公司各自独立运行一条单股票路径，公司间没有共享现金、持仓或竞价。参数延续已有的示意配置：动量 5 日、波动率 20 日、单边固定成本 0.1%。每个交易日信号最多使用执行参考收盘日之前的数据；账户现金、份额和费用逐日守恒。

`688081` 于 2020-01-06 上市，在 2020-02-03 的首个节后交易日还不满足 20 日波动率预热。为保持固定 126 家共同样本，回放统一从 2020-02-12 开始，到 2020-03-31 共 35 个交易日；其余 125 家单独可以更早启动。此路径不含文本、问答计数或事件信号。按手设 Agent 规则计算的市场信号相对零信号结果方向并不一致；此差值仅用于检查规则响应，不是文本增益、策略表现或预测证据。

这不是市场冲击仿真或真实账户回测。执行价格是归一化收益指数的前收盘参考价，假设订单全部成交；Agent 不改变历史收益路径，且角色参数、交易成本和成交行为未通过真实持仓、净订单流或盘口数据校准。

同一批固定 126 家公司随后完成[无文本多资产组合基线](WUHAN_PRE_EVENT_PORTFOLIO_NO_TEXT_2020.md)：42 个三股篮子、共享/分账户 84 条内生模拟路径和 2,940 个组合日。该模型中的价格由有限双边成交产生，历史行情只供交易日、信息时点与停牌代理；它和本节价格接受型逐股回放属于不同机制实验，不能合并解释为历史市场拟合。

## 重现

在新环境重新取得当前历史行情时，先安装固定版本 SDK，并从事前样本配置派生代码。下载输出必须是新空目录；该次取得的数据可能与本报告哈希不同：

```powershell
python -m pip install -r requirements-market.txt --target research_outputs/provider_deps
$universe = Get-Content research/configs/wuhan_qna_active_universe_2020.json -Raw | ConvertFrom-Json
$stocks = $universe.stock_codes | ForEach-Object {
  if ($_.StartsWith("6")) { "sh.$_" }
  elseif ($_.StartsWith("0") -or $_.StartsWith("2") -or $_.StartsWith("3")) { "sz.$_" }
  else { throw "Unsupported stock-code prefix: $_" }
}
python -m research.data_pipeline.fetch_baostock `
  --stocks $stocks --benchmark sh.000300 `
  --start-date 2019-06-01 --end-date 2020-03-31 `
  --dependency-dir research_outputs/provider_deps `
  --output-dir research_outputs/wuhan_pit_market_download_2020
```

在已归档的下载文件可用时，按冻结配置重建行情、事件研究和回放：

```powershell
python -m research.data_pipeline market research/configs/wuhan_pre_event_pit_market_import_2020.json `
  --output-dir research_outputs/wuhan_pit_market_prepared_2020_v2
python -m research.baselines.run_experiments research/configs/wuhan_pre_event_pit_market_study_2020.json `
  --output-dir research_outputs/wuhan_pit_event_study_2020_v2
python -m research.simulation.historical_replay research/configs/wuhan_pre_event_pit_replay_2020.json `
  --output-dir research_outputs/wuhan_pit_historical_replay_2020_v2
```

上面的下载步骤需要网络；完整股票代码来自冻结样本配置。已归档原始行和其他运行产物在 Git 忽略的本机 `research_outputs/`，不会提交。重复取得的提供方历史数据可能已修订，若原始哈希不同，应作为新行情版本另行记录，不能覆盖或称为同一份精确复现。

## 后续

下一步是在配置好的 DeepSeek 网关上为这批事前问答生成预测，再由助手逐条对照原文、时间和唯一证据引用复核；未通过复核门槛的模型运行不生成交易信号。固定的[武汉语义回放配置](configs/wuhan_pre_event_pit_semantic_replay_2020.json)已准备好将通过门槛的信号接到同批 126 家逐股路径，并与无文本路径配对；21 家没有事前可用回复的公司保留为“无可见文本”，不能记作负面事件。之后再研究共享资金的多资产 Agent 路径及关键词规则对照，同时保留 120 日估计窗失败、公开时刻代理、样本选择限制和无校准市场假设。
