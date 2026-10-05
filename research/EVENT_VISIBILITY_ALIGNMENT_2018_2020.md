# 2018/2020 事件可见时点与 Agent 信号对齐审计

## 目的

核对官方材料记录的发布时间、事件研究锚点和 Agent 可用信号日是否被混为一谈。2018 与 2020 输入继续保留各自的保守/代理口径；下列三项 2020 口径属于同一武汉通告的时点敏感性比较，不是独立事件。

## 证据与对齐

人民银行官方答记者问记载，资管新规于 2018 年 4 月 27 日正式发布，页面发布时间为当日 18:38:49。它是该官方网页的可核实发布时间，不证明全网首次公开时刻。新增页面时间配置把它作为可复查的信息时间；由于晚于 A 股收盘，模型可见锚点为 4 月 28 日，与原“发布日期次日可见”配置相同。三只股票的事件研究都锚定到 5 月 2 日，Agent 信号截止日从 5 月 2 日开始，首次受信号影响的交易日为 5 月 4 日。两种时间配置的三只股票事件研究指标和六条 Agent 路径逐项相同。

武汉通告的新华社全文页显示 2020-01-23 03:15:55；页面不注明时区，配置按中国本地时间 UTC+08 解释。武汉市生态环境局后续回顾称市级指挥部约于凌晨 2 时发令。前者是存档网页的刊发时刻，后者是约略发令时刻，两者都不证明全渠道首次公开的精确时刻。通告规定的 10:00 是措施生效时间，不是首次发布时间。日期保守配置从 1 月 24 日起视为可用；另保留 10:00 生效时点上界代理。按 UTC+08，新华社页面时刻早于 A 股开盘。上交所公告将春节休市延长到 2 月 2 日，并规定 2 月 3 日开市。三种时点如下：

| 武汉口径 | 可见锚点 | 事件研究首个对齐交易日 | Agent 首个信号截止日 | 首个受影响交易日 |
|---|---|---|---|---|
| 日期保守，1 月 23 日通告日期 | 2020-01-24 | 2020-02-03 | 2020-02-03 | 2020-02-05 |
| 10:00 生效时点上界代理 | 2020-01-23 | 2020-01-23 | 2020-01-23 | 2020-02-04 |
| 新华社全文页刊发时间，03:15:55 | 2020-01-23（开盘前） | 2020-01-23 | 2020-01-23 | 2020-02-04 |

新增页面时间配置重跑了三股票事件研究和六条冲击/对照路径：它的事件锚点、三只股票 CAR 及配对模拟结果与原 10:00 代理逐项相同，因为二者在日频口径均落在 1 月 23 日；与日期保守口径相比，首个受影响交易日早一个交易日。这三种时点是同一通告敏感性，不是独立事件或性能验证。10:00 代理用于事件研究时，日频收盘收益包含措施生效前的价格运动；描述性事件研究仍应优先报告日期保守口径。03:15:55 页面时间早于开盘，但不代表已确定全渠道首次公开时刻。

## 冲击回放敏感性

两套武汉 Agent 回放均使用固定信号 `-0.8`、不确定性 `0.6`、三次信号、每 Agent 10 亿元资金、固定 10 亿元流动性和假设冲击系数。它们是手设机制条件，不是从文本、持仓、订单流或盘口估计的参数。冲击系数 `0.03` 时，信号路径减无信号路径的模型内结果为：

| 可见口径 | 窗口净订单差 | 窗口末模拟价格指数差 | 全期末模拟价格指数差 |
|---|---:|---:|---:|
| 日期保守 | -2.580 亿元 | -0.6952 | -0.0065 |
| 10:00 生效时点代理 | -2.076 亿元 | -0.5511 | +0.0206 |

末值差异变号说明该合成路径对单日信号起点敏感；不能解释为真实价格效应、预测效果或政策因果。2018 两个时间口径结果相同，也只说明该交易日历下的锚点等价，不证明市场首次获知时间已确定。

## 复现与检查

独立运行使用当前版本引擎，事件研究覆盖三只预选股票；五个日期/时间变体的冲击回放各有六条信号/对照与冲击路径，所有新旧产物清单哈希均已核验。原始输入、来源档案和结果清单在 Git 忽略的本机 `research_outputs/`。单元测试验证 2018 页面时间仍落在原保守锚点，并验证武汉日期保守口径首个受影响日为 2 月 5 日、两种 1 月 23 日时间口径为 2 月 4 日；另覆盖页面时间与后续约略发令时间分开存档。当前全套 330 项测试通过。

```powershell
python -m research.baselines.run_experiments research/configs/observed_pilot_2018.json --output-dir <新的日期保守研究目录>
python -m research.baselines.run_experiments research/configs/observed_pilot_2018_page_timestamp.json --output-dir <新的页面时间研究目录>
python -m research.simulation.observed_counterfactual research/configs/observed_counterfactual_2018.json --output-dir <新的2018日期保守回放目录>
python -m research.simulation.observed_counterfactual research/configs/observed_counterfactual_2018_page_timestamp.json --output-dir <新的2018页面时间回放目录>
python -m research.simulation.observed_counterfactual research/configs/observed_counterfactual_2020.json --output-dir <新的2020日期保守回放目录>
python -m research.simulation.observed_counterfactual research/configs/observed_counterfactual_2020_effective_time_proxy.json --output-dir <新的2020生效时点代理回放目录>
python -m unittest discover -s tests -q
```

来源：[人民银行资管新规答记者问](https://www.pbc.gov.cn/jinrongwendingju/146766/146770/c2e228ad641d4a85acdc9142da4f955d/index.html)、[新华社保存的武汉第 1 号通告全文](https://www.xinhuanet.com/politics/2020-01/23/c_1125495557.htm)、[武汉市生态环境局关于凌晨发令时间的后续回顾](https://hbj.wuhan.gov.cn/hjsj/ztzl/kjyq/wmzxd/202004/t20200422_1074017.html)、[上交所 2020 年春节休市调整公告](http://www.sse.com.cn/disclosure/announcement/general/c/c_20200127_4991582.shtml)。新增武汉页面归档及 SHA-256 位于 Git 忽略目录 `research_outputs/observed_2020/evidence/`。

新增复现命令：

```powershell
python -m research.baselines.run_experiments research/configs/observed_pilot_2020_xinhua_timestamp.json --output-dir <新的新华社页面时间事件研究目录>
python -m research.simulation.observed_counterfactual research/configs/observed_counterfactual_2020_xinhua_timestamp.json --output-dir <新的新华社页面时间冲击敏感性目录>
```

三只股票的历史收益压力回放仍是机制敏感性实验。之后已构建[武汉通告前公司问答样本](WUHAN_PRE_EVENT_QA_SNAPSHOT_2020.md)：从截至 2020-01-22 有事前提问的 2,599 家公司中确定性抽取 126 家，105 家可选到一条最新确认回复；未来回复未回填。此队列不代表全部上市公司，哈希抽样也不是事前预注册。下一步是在助手逐条复核模型输出原文和证据后，使用时间隔离、关键词及无文本对照开展受控 Agent 实验；这一步仍不能证明预测能力或因果效应。
