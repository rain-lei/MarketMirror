# 2018/2020 固定事件的第二行情源核对

为了核对原 BaoStock 行情，从东方财富历史日 K 线接口取得三只固定股票和沪深 300 的另一份当前历史版本。请求为日频、`fqt=0` 不复权，字段包括涨跌幅（%）；[AKShare 官方文档](https://akshare.akfamily.xyz/data/stock/stock.html)说明该字段和复权选项，[实现源码](https://github.com/akfamily/akshare/blob/master/akshare/stock_feature/stock_hist_em.py)将空复权选项映射到 `fqt=0`。八个响应于 2026-09-27 经 Firecrawl 抓取为 JSON 代码块，内容与请求参数、源代码、原 BaoStock 文件及结果均保存 SHA-256；缓存并非原始 HTTP 线传输字节，也不是 2018/2020 当时留存的版本。

逐日对齐后，2018 年四条序列各有 168 日；2020 年各有 171 日。描述性舍入容差为 **0.0052 个百分点**，依据第二来源涨跌幅显示到小数点后两位；这不是统计检验。2018 年全部 672 个序列日、2020 年除下表三日外的 681 个序列日都处在容差内。两个事件的所有 9 组股票/事件窗口也均处在容差内，窗口内最大差不超过 0.0049 个百分点。

| 序列 | 差异日 | 东方财富不复权涨跌幅 | BaoStock `adjustflag=1` 涨跌幅 | 差（百分点） |
|---|---|---:|---:|---:|
| 平安银行 `000001` | 2019-06-26 | −0.4500% | +0.601956% | 1.051956 |
| 万科 A `000002` | 2019-08-15 | −2.9100% | +0.996900% | 3.906900 |
| 贵州茅台 `600519` | 2019-06-28 | −1.2400% | +0.223058% | 1.463058 |

这些日期显示**不复权与复权收益口径不能直接混用**。本次未独立核对公司行为公告，不把差异指定为某次分红、拆分或数据错误。2019-08-15 位于万科两个 2020 事件口径的 120 日估计窗口内；用第二来源不复权日收益及同源沪深 300 按同样模型重算，其 CAR 分别比 BaoStock 结果高 **0.2196**、**0.2136** 个百分点。2018 三股 CAR 差分别为 −0.0005、−0.0187、+0.0205 个百分点。完整 9 组数值及逐日差见本地 `research_outputs/independent_eastmoney_2018_2020/check_v2/independent_quote_report.md`。

该对照确认事件窗口附近的**显示精度级逐日一致性**，但 CAR 比较混合了舍入、调整基准与估计期差异，不能称为同定义的独立历史复现。两来源也可能共享上游交易所报价；这里没有核实首次公开时刻、历史修订、事件因果、样本代表性或真实订单流。

当前机器已将原始运行和另一目录的独立重跑逐字节比较：结果 JSON、报告 Markdown、运行清单三份均一致；固定完整性目录 1/1 项通过。重跑需要本机已存档的八份独立响应、原 BaoStock 产物与固定事件运行；目标目录须为空：

```powershell
python -m research.data_pipeline.crosscheck_quotes research/configs/independent_quote_check_2018_2020.json --output-dir research_outputs/independent_eastmoney_2018_2020/<新的运行目录>
python -m research.registry.verify_catalog research/configs/integrity_catalog_independent_quotes.json --output-dir research_outputs/<新的核验目录>
```
