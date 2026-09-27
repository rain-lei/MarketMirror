# Research data pipeline

这部分代码构建 MarketMirror 的研究数据层：质量报告、可追溯的问答明细、待核对财务快照，以及按指定截止时刻生成的文本特征。原始 Excel 保持只读，生成的数据保存在 Git 忽略的 `research_outputs/` 中。

## 生成质量报告

在仓库根目录执行：

```powershell
$py = "python"
$sourceDir = "<请填写三份 Excel 所在的本地目录>"
& $py -m research.data_pipeline.profile_sources `
  --output-dir research_outputs `
  (Join-Path $sourceDir "金融数据示例.xlsx") `
  (Join-Path $sourceDir "irm_szse_all_recent3m.xlsx") `
  (Join-Path $sourceDir "用户与公司问答(高频,按季度拆分)-2020.xlsx")
```

输出：

- `research_outputs/data_quality_report.json`
- `research_outputs/data_quality_report.md`

## 生成标准问答 CSV

先用小批量验证字段映射：

```powershell
& $py -m research.data_pipeline.normalize_qa `
  (Join-Path $sourceDir "金融数据示例.xlsx") `
  research_outputs/qa_sample.csv `
  --limit 100
```

这个 CSV 用于检查字段映射。它的 `qa_id` 基于文件名和提问内容；正式实验使用下文统一 SQLite 数据集中的来源行标识和运行清单，不依赖这个样本导出标识。

## 生成公司季度特征

公司季度特征只使用提问内容、提问时间、用户类型和公司信息。回复状态和回复数量以 `post_event_` 开头，默认只做事后描述，不进入预测特征。

```powershell
& $py -m research.data_pipeline.aggregate_qa_features `
  (Join-Path $sourceDir "用户与公司问答(高频,按季度拆分)-2020.xlsx") `
  research_outputs/qa_features_2020.csv
```

## 提取待核对的财务快照

使用 Python 自带的 SQLite 保存快照和来源行引用，无需额外安装数据库。

```powershell
& $py -m research.data_pipeline.extract_financial `
  (Join-Path $sourceDir "用户与公司问答(高频,按季度拆分)-2020.xlsx") `
  --output-dir research_outputs/financial_2020
```

输出包括 `financial_unverified.sqlite`、`financial_quality_report.json` 和 `financial_quality_report.md`。

快照按来源文件、股票代码、来源季度提示及财务值去重。同一公司和来源季度的不同值保留为不同快照，并报告冲突。
`p` 和 `p/e` 放在来源行上下文中，不参与财务快照去重。
来源文件 SHA-256、字段清单 SHA-256、Excel 行号和记录计数用于追溯。重复执行不会追加重复记录。

财务单位、变换和实际报告期尚未确认，所以所有快照保持 `unverified`，不用于财务比率或预测特征。

可以把提取报告渲染为便于人工核对的字段字典：

```powershell
& $py -m research.data_pipeline.render_financial_dictionary `
  research_outputs/financial_2020/financial_quality_report.json `
  --output-dir research_outputs/financial_2020
```

这会生成 `field_dictionary.json` 和 `field_dictionary.md`。字典列出 68 个字段的层级、缺失率和数值形态，同时单独列出报告期、公告时间、单位、变换、报表类型以及 `p/p/e` 的未确认项。它是结构审计材料，不会把字段提升为 `verified`，也不包含本地源文件路径或问答正文。
公司季度问答特征在整个季度结束后才可用；不能用于预测同一季度内的早期事件。公司标签和来源财务季度提示也不是实时可用输入。

正式财务 schema 要求提供单位、变换说明、公告时间和 `verification_status=verified`；提取结果保存在单独的待核对 SQLite 表里，不会直接填入该 schema。

## 合并为可追溯的标准数据集

先完成上面的财务提取，再把三份 Excel 合并。每个文件只读取第一个工作表，工作表名和完整表头写入来源表。

```powershell
$sources = @(
  "$sourceDir\用户与公司问答(高频,按季度拆分)-2020.xlsx",
  "$sourceDir\irm_szse_all_recent3m.xlsx",
  "$sourceDir\金融数据示例.xlsx"
)
& $py -m research.data_pipeline dataset @sources `
  --financial-db research_outputs/financial_2020/financial_unverified.sqlite `
  --output-dir research_outputs/unified
```

输出 `dataset.sqlite` 和 `run_manifest.json`。运行清单记录输入文件、财务输入、代码、字段清单、特征规则和输出数据库的 SHA-256，以及逐文件记录数、异常数和处理假设。

数据库中的主要关系：

| 表 | 用途 |
|---|---|
| `source_file` | 文件内容哈希、原路径、工作表、表头和行数 |
| `company` / `company_alias` | 按来源股票代码建立主表，保留不同简称和引用数 |
| `qa_record` | 每条来源问答、规范化时间、可用性标记和财务快照关联 |
| `qa_restricted_context` | 原始时间、代码、状态、标签、季度和其他待核对列 |
| `financial_snapshot` / `source_row_reference` | 待核对财务快照及复合键来源引用 |
| `financial_field_dictionary` / `dataset_metadata` | 字段统计和数据集处理清单 |

`qa_id` 来自文件内容哈希、工作表和行号。同一输入重复执行不会追加记录。相同提问内容的指纹重复单独计数，保留全部来源行；缺少平台问答 ID 时不自动合并。公司主表只确认来源股票代码，交易所和法定主体还没有核实。

有财务列的文件必须提供哈希匹配的财务提取数据库；逐行核对股票、公司名、原始时间、标签和来源行，关联不足或不一致会终止构建。新数据库通过计数、外键和完整性检查后才替换已有产物。

## 按指定时点导出实验特征

```powershell
& $py -m research.data_pipeline features research_outputs/unified/dataset.sqlite `
  --window-start "2020-01-01T00:00:00+08:00" `
  --as-of "2020-01-23T23:59:59+08:00" `
  --output research_outputs/unified/qa_asof_2020-01-23.csv
```

可加 `--stock-code 000001` 只导出一家公司。输出 CSV 和同名 `.csv.manifest.json`，记录时窗、输入数据库哈希、特征列、规则和代码哈希。

时窗两端包含指定时刻。只统计这个时窗内已经提问的记录；回复必须有确认状态、有效文本及不早于提问的时间，并且已在截止时刻之前出现。未来回复不会进入回复长度、关键词计数或可见回复比例。`no_visible_reply_count` 表示截至当时没有可用回复，不代表公司后来没有回复。

特征白名单只包含提问和已知回复的数量、长度、字面关键词计数及比例。标签、财务快照、`p`、`p/e`、来源季度、用户名称及类型、最终回复状态统计均不进入导出结果。关键词匹配还不具备语义或情绪判断能力。

无时区的时间按北京时间解释。仅含日期的提问、错误代码、空文本、公式文本等记录保留在数据库中，但不进入相应的文本特征。2020 年的回复时间只有日期，因此仅从次日零点起视为可用，并添加 `reply_date_only_next_day_bound` 标记；这只是保守可用时点，不能当作真实日内发布时间。来源时间暂作公开可见时间的代理，还没有公告日志或文本修订历史，真实预测验证仍需确认这一假设。

导出前校验数据集哈希和特征规则哈希。修改过的数据库或已变更规则不能直接沿用旧运行清单。

## 事件研究窗口

市场 CSV 的必要列是 `trade_date,stock_code,stock_return,market_return`。收益率使用小数单位，例如 1% 写为 `0.01`。
多股票文件必须使用 `--stock-code` 指定研究对象。
估计窗口和事件窗口严格分开；间隔、窗口长度按交易观测条数计数。窗口不足、重复日期和非有限收益率会报错。

```powershell
& $py -m research.baselines.event_study market_daily.csv 2020-02-03 `
  --stock-code 000001 --estimation-window 120 --pre-event-gap 5 `
  --window-before 3 --window-after 5 --output research_outputs/event_result.json
```

这是底层计算命令。当前已完成小规模真实数据运行，详见下文的真实行情试验；结果仍是描述性市场基线。

## 行情导入与可追溯事件实验

正式实验使用配置驱动的行情导入和执行器。上面的 `event_study` 命令是底层计算接口；下文流程额外保存来源、口径、样本、失败原因和结果哈希。

### 输入结构与口径

需要三份 UTF-8 CSV：

| 输入 | 必要列 | 说明 |
|---|---|---|
| 股票 | `trade_date,stock_code` 和一个数值列 | 一行一个股票代码和交易日 |
| 基准 | `trade_date,benchmark_id` 和一个数值列 | 明确选择基准 ID，其他基准的行另行计数后排除 |
| 交易日历 | `trade_date` | 实际开市日列表，不使用普通工作日替代真实交易日 |

日期必须为 `YYYY-MM-DD`。基准要覆盖日历中的全部日期；每只股票要覆盖自身首末输入日之间的所有日历日期。缺失日、重复日期、非有限值和无效代码会终止导入，不能通过取交集悄悄减少样本。停牌等特殊状态需要明确提供方的每日价格口径，当前程序不会填零或补价格。

股票和基准可以分别使用 `prices` 或 `returns`：

- `prices`：数值必须为正，使用相邻日历交易日的 `本期价格 / 上期价格 - 1`。股票口径必须声明为 `split_dividend_adjusted` 或 `split_adjusted`；未经复权的股票价格不接收，程序自身不计算复权因子。基准声明为 `price_index` 或 `total_return_index`。
- `returns`：必须声明 `decimal` 或 `percent` 单位，输出统一为小数形式的简单收益率。股票口径声明为 `adjusted_price_return` 或 `total_return`；基准为 `price_index_return` 或 `total_return_index_return`。对数收益率不接收。
- 价格输入的首日只提供收益率计算基期，不直接进入事件研究；某只股票可以有独立的上市或覆盖起点。股票和基准的收益率口径可能不同，清单会同时保存，不能据此声称两者口径相同。

行情配置示例（路径相对配置文件所在目录）：

```json
{
  "stock_input": "stock_prices.csv",
  "benchmark_input": "benchmark_prices.csv",
  "calendar_input": "calendar.csv",
  "benchmark_id": "YOUR_BENCHMARK_ID",
  "stock_format": "prices",
  "benchmark_format": "prices",
  "stock_value_column": "adjusted_close",
  "benchmark_value_column": "close",
  "stock_price_basis": "split_dividend_adjusted",
  "benchmark_price_basis": "price_index",
  "stock_data_source": "填入数据提供方、接口或原文件说明",
  "benchmark_data_source": "填入基准指数数据来源",
  "calendar_data_source": "填入交易日历来源",
  "data_kind": "observed",
  "return_type": "simple"
}
```

如果切换为收益率输入，需要删除对应 `*_price_basis`，改为 `*_return_unit` 和 `*_return_basis`，并指定收益率数值列。提供方说明与复权口径是输入声明，导入校验不会自动认证其真实性。

```powershell
& $py -m research.data_pipeline market market_import.json `
  --output-dir research_outputs/market_prepared
& $py -m research.baselines.run_experiments experiment.json `
  --output-dir research_outputs/event_experiment
```

行情输出为 `market_daily.csv`、`market_manifest.json` 和 `market_quality_report.md`，包含基准 ID、来源与配置哈希、处理代码、Python 版本、完整日历、每只股票的覆盖范围和价格/收益率口径。

### 事件配置与日频对齐

实验配置需要 `run_id`、`market_manifest`、`data_kind`、预先选定的 `stock_codes` 和 `events`。`windows` 可配置估计窗口、间隔和事件窗口，默认与底层事件研究相同。

每个事件需要 `event_id,event_date,event_type,evidence_source`，以及 `visible_at` 或 `visible_date` 二选一：

- `visible_at` 必须带时区，转换为北京时间；15:00 之前用当天作为可反应日期，15:00 及之后从次日开始。
- `visible_date` 只有日期，按次日零点起可见处理。
- 取名义事件日期与可反应日期中较晚的一天，再使用该日或之后的第一个交易观测。不会提前对齐到公开信息尚不可见的日期。

当前收盘时间假设为北京时间 15:00，适用范围需要与输入市场一致。日内公告使用当日收盘到收盘收益，也会包含公告之前的价格变化，不能解释为纯公告效应。

执行器输出 `event_results.json`、逐日异常收益 CSV、Markdown 报告和 `experiment_manifest.json`。结果保留实际对齐日、估计窗口、alpha、beta、逐日异常收益和 CAR；CAR 是异常简单收益的求和，不是投资组合的复利收益。

窗口不足或模型无法拟合会记录为失败案例。只要预选股票有失败，该事件的完整样本均值就为空；实验状态为 `partial` 或 `failed`，命令退出码为 1。它不会把失败股票直接删掉后宣称全样本成功。

当前流程没有显著性检验、置信区间或因果识别。其他冲击进入估计窗口、事件重叠以及股票间相关性，都需要另外验证。

### 已知冲击的合成验证

这一示例生成 100 个工作日、两只合成股票和一个合成基准。日历不包含真实休市安排，数据不是历史行情。生成器要求新目录，防止覆盖已有文件；已生成过时，可以直接重新运行导入和实验步骤。

```powershell
& $py -m research.examples.generate_market_fixture `
  --output-dir research_outputs/market_demo
& $py -m research.data_pipeline market research_outputs/market_demo/market_import.json `
  --output-dir research_outputs/market_demo/prepared
& $py -m research.baselines.run_experiments research_outputs/market_demo/experiment.json `
  --output-dir research_outputs/market_demo/results
```

合成股票由已知的 alpha、beta 和冲击构造。价格导入后生成 198 条对齐收益观测；两个事件和两只股票共 4 次研究，已知冲击的 CAR 分别恢复为 `0.04` 和 `-0.02`，无冲击检查的 CAR 在浮点误差范围内为零。

合成/真实分类写入配置、清单和报告，执行器拒绝二者分类不一致的输入。这验证了导入、单位、对齐与计算链路，不提供真实市场拟合或 Agent 预测能力的证据。

## 真实数据下载和首个历史试验

可选依赖 `requirements-market.txt` 固定 BaoStock SDK 版本。可安装到本地忽略目录，不修改公共运行时的包：

```powershell
& $py -m pip install -r requirements-market.txt --target research_outputs/provider_deps
& $py -m research.data_pipeline.fetch_baostock `
  --stocks sz.000001 sz.000002 sh.600519 --benchmark sh.000300 `
  --start-date 2019-06-01 --end-date 2020-12-31 `
  --dependency-dir research_outputs/provider_deps `
  --output-dir research_outputs/observed_2020/download
```

下载需要新空目录，保留每次取得的历史版本；已有下载可以直接重新导入与计算。使用 SDK 的公开匿名读取接口，没有自建账户或个人 API 密钥。

当前适配器将股票 `pctChg` 作为百分比日收益，核对同日收盘价与提供方参考前收盘价；基准使用指数收盘价。所有原始行、查询参数、复权状态、日期覆盖和比对结果保存到下载清单。

发现的后复权价格断点另行记录，原始价格保留待核对。程序不修补或强行连接该价格序列，也不把“同日参考收益检查通过”写成“价格连续性通过”。这种输入是提供方日收益观测，还需要独立来源复核；价格版本也不是历史时点快照。

提供方文档见 [BaoStock API](https://www.baostock.com/mainContent?file=pythonAPI.md)。实际试验及其价格问题见 `OBSERVED_PILOT_2020.md`。

### 缓存事件证据

本地已保存证据，重复运行只需校验存档。新环境先读取原始网页并保存同名缓存，例如使用已配置的 Firecrawl CLI：

```powershell
firecrawl scrape "https://www.xinhuanet.com/politics/2020-01/23/c_1125495557.htm" -o .firecrawl/wuhan-notice-1.md
firecrawl scrape "http://www.sse.com.cn/disclosure/announcement/general/c/c_20200127_4991582.shtml" -o .firecrawl/sse-holiday-1.md
firecrawl scrape "https://www.baostock.com/mainContent?file=pythonAPI.md" -o .firecrawl/baostock-search-0.md
& $py -m research.data_pipeline.archive_evidence research/configs/observed_pilot_2020_evidence.json `
  --output-dir research_outputs/observed_2020/evidence
```

存档工具复制已读取的本地网页并计算哈希；它不访问网络、不执行网页里的说明。内容不同的既有存档不会被替换。其他网页工具也可生成这些缓存文件，证据目录只要求保留实际取得的内容和哈希。

事件执行器的可选 `evidence_manifest` 配置会校验每个来源文件哈希以及事件引用 URL。它确认文件与清单一致，不自动判定事件解释是否正确。武汉试验明确区分发布日期和生效时间代理，没有将生效时间当作首次发布时刻。

### 执行历史试验和生成图

```powershell
& $py -m research.data_pipeline market research_outputs/observed_2020/download/market_import.json `
  --output-dir research_outputs/observed_2020/prepared
& $py -m research.baselines.run_experiments research/configs/observed_pilot_2020.json `
  --output-dir research_outputs/observed_2020/results
```

绘图是可选步骤，使用独立的 Matplotlib 依赖目录生成 PNG 和 SVG：

```powershell
& $py -m pip install "matplotlib>=3.8,<4" --target research_outputs/plot_deps
& $py -m research.baselines.plot_event_results research_outputs/observed_2020/results/event_results.json `
  --dependency-dir research_outputs/plot_deps --output-dir research_outputs/observed_2020/figures
```

图会验证输入结果清单、检查累计曲线与 CAR 一致，并记录绘图代码、Matplotlib 版本及输出哈希。当前实际运行得到 3 只股票、388 个交易日、1,161 条标准收益观测；同一事件的两种对齐口径共 6 次计算成功。事件研究没有验证预测能力、统计显著性或 Agent 行为。

### 事件日期对照诊断

对同一三只股票、相同市场模型和 `120/5/[-3,+5]` 窗口，枚举 2019-12-01 至 2020-12-31 的其他交易日。候选日期的**估计期和事件窗口均不能与两个实际对齐口径合并后的真实事件窗口相交**。输出逐日、逐股票 CAR 及输入和代码哈希。

```powershell
& $py -m research.baselines.placebo_dates research/configs/placebo_pilot_2020.json `
  --output-dir research_outputs/observed_2020/placebo
```

本地结果保留此前 23 个候选日期（2019-12-10 至 2020-01-10）、此后 88 个候选日期（2020-08-17 至 2020-12-24）；另有 143 个日期因与事件隔离区间交叉排除，11 个因窗口或拟合不完整排除。平安银行在日期保守口径的实际绝对 CAR 大于等于事前候选日的 87.0%、事后候选日的 53.4%；等权三股均值分别仅为 8.7% 和 9.1%。其余股票和另一对齐口径见 `research_outputs/observed_2020/placebo/placebo_report.md`。这些比例只是不同日期的描述性位置；候选窗口彼此重叠、跨越不同市场阶段，两个实际口径又是同一事件，**不能解释为 p 值、因果效应或预测能力**。

### 成交量与成交额的独立核验

下载时留存的股票原始响应还含 `volume` 和 `amount`。已归档的 [BaoStock API 文档](https://www.baostock.com/mainContent?file=pythonAPI.md)分别将它们定义为成交股数和人民币成交额。`data_pipeline/market_activity.py` 核对文档存档哈希、下载清单、原始响应、独立交易日历和既有行情清单，再把这两个字段导出为独立活动数据。后复权价格断点不用于用成交额反推价格。

```powershell
& $py -m research.data_pipeline.market_activity research/configs/market_activity_pilot_2020.json `
  --output-dir research_outputs/observed_2020/activity
& $py -m research.baselines.activity_event_study research/configs/activity_event_pilot_2020.json `
  --output-dir research_outputs/observed_2020/activity_event
& $py -m research.baselines.plot_activity_event research_outputs/observed_2020/activity_event/event_activity.json `
  --dependency-dir research_outputs/plot_deps --output-dir research_outputs/observed_2020/activity_figures
```

实际校验得到 3 只股票各 388 个交易日，共 1,164 条成交记录。事件成交分析沿用原 6 个已经对齐的股票/口径窗口，以每个窗口之前的 120 个交易日的日成交中位数为基期。日期保守口径下 2020-02-03 的成交额倍数为平安银行 2.278、万科 3.060、贵州茅台 3.753；把同一通告按生效时间代理对齐到 2020-01-23 时分别为 1.251、2.159、1.719。详细数值和每交易日记录在 `research_outputs/observed_2020/activity_event/`，PNG/SVG 科学图在 `activity_figures/`；图中右侧相对日 +1 和左侧相对日 0 是同一个 2020-02-03，不是两次峰值。

这是**已成交双边总量**，不能区分买盘与卖盘，也不是某个 Agent 可即时成交的盘口深度。提供方数据不是历史时点归档；现有资料无法据此校准价格冲击系数或三类交易者行为。两个口径是同一事件的敏感性分析，不能作为两个独立事件做统计显著性推断。

## 文本增量预测对照

2020 年问答中，事件研究原选样的万科和贵州茅台没有来源记录，不能把缺失记录解释为零。因此预测对照固定选择平安银行 `000001`、中兴通讯 `000063` 和比亚迪 `002594`，并另存一套公开行情版本。三只股票在训练开始前已有问答记录，构建器会检查这一点。选股仍属查看来源后的探索性选择，没有外部预注册。配置文件 `configs/text_pilot_2020.json` 固定问答来源哈希、样本、30 日文本窗口、20 个行情交易日窗口、时间切分、Ridge 参数网格和区块重抽样设置。

```powershell
& $py -m research.data_pipeline.fetch_baostock `
  --stocks sz.000001 sz.000063 sz.002594 --benchmark sh.000300 `
  --start-date 2019-06-01 --end-date 2020-12-31 `
  --dependency-dir research_outputs/provider_deps `
  --output-dir research_outputs/text_pilot_2020/download
& $py -m research.data_pipeline market research_outputs/text_pilot_2020/download/market_import.json `
  --output-dir research_outputs/text_pilot_2020/prepared
& $py -m research.baselines.run_prediction research/configs/text_pilot_2020.json `
  --output-dir research_outputs/text_pilot_2020/results
```

输出包含面板、测试集预测、逐模型误差、报告和输入/代码哈希清单。输出目录须为空，以免覆盖已运行的版本。预测目标是下一交易日简单收益；可见时间采用北京时间 15:00 收盘代理，日期精度回复在次日零点才进入特征。训练、验证和测试按时间顺序分割，跨分界且尚未可见的目标收益被剔除，测试集只用于最后一次评估。没有使用违规标签、财务快照、用户字段或未来回复。

实际试验有 678 条候选面板行；测试集 59 日 × 3 只股票共 177 行。行情 Ridge 的 MAE 为 2.1591 个收益率百分点，加入文本为 2.1397；五日区块的配对误差差值近似 95% 区间包含零，不能宣称稳定增益。详情和局限见 `research_outputs/text_pilot_2020/results/prediction_report.md`。问答公开时点尚未独立核实，行情也非历史时点归档版本，因此这是小样本探索结果，不是可交易策略证据。

## 三类 Agent 的合成机制实验

`simulation/agents.py` 定义共用的长仓状态与可解释决策规则。激进型、保守型和机构型仅通过显式参数、确认次数与再平衡间隔区分；每一步记录目标仓位、风险预算、订单、成交、持仓和原因代码。`simulation/stress_market.py` 对总成交额施加流动性上限，按净订单施加有界价格冲击，并用外部流动性账户核对现金、份额和手续费。若波动率上升使原持仓超过预算，风险减仓优先于普通的确认、调仓日和换手限制；流动性不足时会记录未完成的部分。

固定的 16 步示意配置在 `configs/synthetic_stress_v1.json`，其中收益、信号、波动率、流动性和行为参数全是人为设定。运行时同时输出“使用文本”和“去掉文本”两组，其他外生路径和参数保持相同：

```powershell
& $py -m research.simulation.stress_market research/configs/synthetic_stress_v1.json `
  --output-dir research_outputs/synthetic_stress_v1
```

结果包含逐步账本、简明报告和输入/代码/输出哈希清单；复跑时使用新的空目录。此阶段的测试仅证明机制按配置执行，没有按真实投资者类型取得成交与持仓样本，**尚不能称为行为校准或历史仿真**。试验中的期末财富不代表可交易收益，合成文本的消融也不能代替真实问答的增益验证。

### 真实收益路径上的规则回放

`simulation/historical_replay.py` 把相同的三类示意 Agent 放到已归档的 2020 年股票逐日收益上，分别运行历史宽基动量信号和零信号对照。波动率与动量只使用执行参考收盘日前一交易日及更早的数据；订单按前一交易日收盘的归一化收益指数假定全部成交，再结算当日收益。源文件没有经过确认的连续可交易股价，所以这个指数仅用于计算持仓路径。

```powershell
& $py -m research.simulation.historical_replay research/configs/historical_replay_pilot_2020.json `
  --output-dir research_outputs/observed_2020/historical_replay
```

本地已跑通 `000001`、`000002`、`600519` 各 58 个交易日，共三类 Agent × 两个信号设置；每步核对现金、份额和手续费账本。逐日动作、基准和路径结果见 `research_outputs/observed_2020/historical_replay/`，输入与代码哈希写入清单。参数、固定费率和前收盘价全部成交都是**示意假设**；没有实际持仓、盘口或净订单流，也没有让 Agent 订单改变历史价格。文本、财务和 LLM 信号尚未验证，因此没有接入这一回放。它是时序与约束诊断，不能称为已校准的市场冲击仿真或可交易回测。

保持除运行标识和日期之外的全部参数不变，已追加 2020-04-01 至 2020-12-31 的 185 日路径：

```powershell
& $py -m research.simulation.historical_replay research/configs/historical_replay_later_2020.json `
  --output-dir research_outputs/observed_2020/historical_replay_later
```

后续时期的平安银行与万科三类 Agent 中，市场信号路径末值均低于零信号路径；贵州茅台三类略高。两段时期显示符号不稳定，且样本与时期是在探索中确定的，不能当作正式留出检验或挑选有利角色的依据。完整逐股数值在第二份本地报告中。

## 可审核的语义事件标注与抽取入口

`configs/annotation_pilot_2020.json` 固定 2020 问答来源、上半年可见时窗、公司级训练/验证/测试分组种子和各话题配额。问答只在提问阶段提供问题文本；回复阶段仅在确认已回复且保守可用时点已到时提供问题和回复。字面关键词只用于**抽样分层**，不会自动成为金标准。运行生成本地忽略目录中的 128 条待标注文本（提问、回复各 64 条）：

```powershell
& $py -m research.semantic.annotation_pack research/configs/annotation_pilot_2020.json `
  --output-dir research_outputs/semantic_annotation_pilot_2020
& $py -m research.semantic.keyword_baseline research_outputs/semantic_annotation_pilot_2020 `
  --output-dir research_outputs/semantic_keyword_baseline_2020
```

`semantic/ANNOTATION_GUIDE.md` 定义人工标注、分歧裁定和不能把问题猜测当成事实的规则；`data_contracts/semantic_signal.schema.json` 定义结构化事件。输出包的 `annotation_items.jsonl` 含本地原文和来源哈希，`annotation_template.jsonl` 的 128 个标签目前全部为空。无 LLM 关键词对照提供 113 个**候选话题**，方向一律为 unknown；目前无人工金标准，因此评分器明确输出 `no_reviewed_predictions`，不能报准确率。

双人独立审核的空白包与比较器已准备好：

```powershell
& $py -m research.semantic.review_workflow prepare research_outputs/semantic_annotation_pilot_2020 `
  --output-dir research_outputs/semantic_review_pilot_2020
& $py -m research.semantic.review_workflow compare research_outputs/semantic_annotation_pilot_2020 `
  --reviewer-a <审核者A完成的标签副本.jsonl> --reviewer-b <审核者B完成的标签副本.jsonl> `
  --output-dir <新的本地比较目录>
& $py -m research.semantic.review_workflow finalize research_outputs/semantic_annotation_pilot_2020 `
  --comparison-dir <比较目录> --final-labels <单独复制并填写的裁定标签.jsonl> `
  --output-dir <新的本地金标准目录>
```

审核任务包仅暴露当时可见文本、阶段、条目 ID 与哈希，不带结构化股票代码、训练/测试分组或抽样层；原文可能仍透露公司身份。空白标签副本不得当作已完成审核。当前将两个空白模板输入比较器，正确得到 `no_dual_review`、0/128 双人完成；程序拒绝从这个结果生成金标准。真实审核、分歧裁定和负责人的质量抽查仍未发生；具体操作与判据见 `semantic/ANNOTATION_GUIDE.md`。

未来接入模型时，将每条原始响应以 JSONL 保存：`item_id`、`source_text_sha256`、`model_id`、`prompt_version`、`raw_response`（原始 JSON 字符串）。模型仅返回 `{"events":[...]}`；提示格式见 `semantic/PROMPT_V1.md`。以下命令保留格式/证据失败并生成标准输出：

现在可以用 OpenAI 兼容网关运行原始模型调用。默认配置为 `http://aigw.dlut.edu.cn/v1` 和 `DeepSeek-V4-Flash-0731-W8A8`；密钥只从当前进程的环境变量读取，不会写入代码、清单或网页：

```powershell
$env:MARKETMIRROR_LLM_API_KEY = "<你的网关密钥>"
$env:MARKETMIRROR_LLM_BASE_URL = "http://aigw.dlut.edu.cn/v1"
$env:MARKETMIRROR_LLM_MODEL = "DeepSeek-V4-Flash-0731-W8A8"
& $py -m research.semantic.run_model --check
```

`--check` 只读取网关的 `/v1/models`，不会上传任何问答文本。确认 `available=true` 后再运行样本：

```powershell
& $py -m research.semantic.run_model research_outputs/semantic_annotation_pilot_2020 `
  --output-dir research_outputs/semantic_model_deepseek_v1 --limit 2
```

先用 `--limit 2` 检查网关连通性和返回格式，再去掉限制运行完整 128 条。原始响应目录仍保持在 Git 忽略路径；调用结果必须继续经过下面的标准化和证据跨度校验，不能直接当作标签或金标准。

如果调用中断，可在同一目录加 `--resume` 继续；程序会核对数据、提示、模型和网关版本哈希，只重试未完成或此前失败的条目，并保持每个 `item_id` 在原始输出中唯一。

```powershell
& $py -m research.semantic.parse_model_outputs research_outputs/semantic_annotation_pilot_2020 `
  --raw <本地模型原始输出.jsonl> --output-dir <新的本地标准化目录>
& $py -m research.semantic.signal_validation research_outputs/semantic_annotation_pilot_2020 `
  --gold <独立审核的标签.jsonl> --predictions <标准模型输出.jsonl> `
  --output <新的本地评估.json>
```

如果原始 JSONL 与 `model_run_manifest.json` 放在同一目录，标准化器还会核对原始文件哈希、annotation pack 实验 ID 和输入文件哈希；没有清单的手工测试 JSONL 仍可用于格式验证。

可以用只读审计器检查一次模型运行是否完整，以及标准化是否有解析失败：

```powershell
& $py -m research.semantic.audit_model_run `
  research_outputs/semantic_annotation_pilot_2020 `
  research_outputs/semantic_model_deepseek_v1 `
  --normalized-dir <标准化输出目录> `
  --output <新的模型运行审计.json>
```

审计报告会明确写出 `accuracy_claim_allowed=false`，除非后续另行完成并裁定人工金标准，否则不会把模型运行当作准确率证据。

证据引用的文本、起止字符索引与来源段必须逐字匹配，源文本哈希不一致会被拒绝。评估只使用 `status=labeled` 的人工审核条目，解析失败按漏检计入，报告标注覆盖和只适用于单事件类型匹配子集的方向/证据指标。当前仅完成网关调用适配，尚未形成真实模型效果结论；没有独立人工金标准前不得报告准确率。

## 验证

```powershell
& $py -m unittest discover -s tests -v
```

### 2018 资管新规历史节点

已追加三股 2017–2018 行情、2018-04-27 资管新规的日频事件窗口及 2018 上半年 Agent 规则回放；参数沿用 2020 设置。事件对齐到 2018-05-02，整个 [-3,+5] 窗口包含发布前交易日。证据、窗口拆分、回放表和首次构建命令见 [2018 实验说明](OBSERVED_PILOT_2018.md)。现有数据没有 2018 问答或已验证的政策语义，因此仍是市场模型与价格接受型规则诊断。

固定清单文件沿用 `_2020` 名称，已扩展至 2018/2020 共 15 项；新汇总报告和工作台存放于 `_2018_2020` 输出目录，旧输出保留作为历史版本。

### 跨实验产物完整性核验

`configs/integrity_catalog_2020.json` 在版本控制中固定 15 份本地运行清单的 SHA-256。校验器先比对清单本身，再检查清单声明的输入、代码和输出文件，包括原始 Excel、统一 SQLite、市场数据、事件、文本对照、合成压力和三段历史规则回放。当前共核验 170 个文件引用，输出逐项报告和审计清单：

```powershell
& $py -m research.registry.verify_catalog research/configs/integrity_catalog_2020.json `
  --output-dir research_outputs/integrity_catalog_2018_2020
```

清单中的原始来源路径指向本机文件；迁移环境时需重新取得原始资料并核对哈希。校验通过只表示当前文件字节与**版本控制中固定的清单**一致，不表示已重新运行实验，也不验证数据含义、信息可见时刻或统计结论。目录审计是 M6 工作台的数据基础。

### 本地研究工作台与受控重跑

工作台对 15 项固定运行再次校验输入、代码和输出，再从事件、预测、成交活动与 Agent 回放结果中按白名单抽取汇总。原始问答、个人路径和完整数据库不会写进页面；页面提供事件口径、回放时期与股票筛选、15 项运行的 34 份产物名称及比较状态、核验状态和公开证据链接。

```powershell
& $py -m research.workbench.build --output-dir research_outputs/workbench_2018_2020
```

在浏览器打开生成的 `index.html` 可离线查看摘要；输出目录必须是新空目录。要在页面上重跑固定实验，启动只监听 `127.0.0.1` 的本地服务：

```powershell
& $py -m research.workbench.serve --site-dir research_outputs/workbench_2018_2020
```

打开 `http://127.0.0.1:8766/`，选择清单中的运行并执行。页面会显示并提交该运行的固定数据版本和执行版本，服务端只接受与清单匹配的组合；财务字段口径卡片展示字段统计、来源哈希和未确认项。页面顶部可下载由同一份白名单摘要生成的 `report.md`，用于归档或复核。也可用 `& $py -m research.workbench.run observed_event` 单独重跑。执行入口只接受固定清单内的运行 ID、一次运行一项，不接受网页传入配置路径；每次在被 Git 忽略的 `research_outputs/workbench_runs/<job_id>/` 保存所选配置、版本、主配置与清单哈希、状态、对照报告和产物哈希。页面读取状态时会重新核对关键记录文件。上方事件/股票筛选不改动固定配置；自定义事件、数据或模型版本尚未实现。旧 Vue/FastAPI 静态演示已从当前分支移除，代码可在 Git 历史中找回。

### 从输入重新执行并比较结果

在完整性核验之外，`registry/reexecute.py` 会使用固定的原始输入和配置，在临时目录实际重跑目录中的 15 项研究运行。统一数据库、市场导入、事件研究、成交活动、文本预测、合成压力、语义抽样和历史规则回放均已纳入：

```powershell
& $py -m research.registry.reexecute research/configs/reexecution_catalog_2020.json `
  --output-dir research_outputs/reexecution_catalog_2018_2020
```

本机实测 15/15 项通过，共 34 份声明产物：31 份逐字节一致；事件结果 JSON 只忽略顶层 `generated_at`，其余字段精确一致；统一 SQLite 原始字节因内部构建时间不同而变化，比较器核对完整数据库、表结构和按主键排序的全部表行，仅忽略 `dataset_metadata.generated_at` 一行。重跑约需数分钟，临时数据库在核对后清理。该门槛验证**当前机器上的再生能力**，仍不确认来源文件的经济含义、历史公开时刻、可交易成交假设或 Agent 行为校准；其他未列入目录的产物也不在此结论内。
