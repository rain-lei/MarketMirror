# 123家公司后复权来源补齐

2026-10-03，后复权来源已取得全部123家公司；140日原始网格17,220个位置独立核验完成，其中17,194个实际来源行、26个来源缺行。缺行均对应先前单独核对的停牌期间，原字段继续为空，没有填0或改写原来源状态。该结论只涉及后复权来源，原247项不复权/前复权来源批次仍为246项取得、`002331`前复权一项失败。

| 有限采集阶段 | 本阶段实际状态 | 保留记录 |
|---|---|---|
| 原后复权全队列 | 取得113/123，10项失败，批处理实际退出1 | [v1协议](configs/temporal_hfq_acquisition_2020_2021_v1.json) |
| 公共参数补采 | 仅重试已核验的10项失败，取得8项、2项失败，实际退出1 | [v2协议](configs/temporal_hfq_retry_2020_2021_v2.json) |
| 完整适配器字段补采 | 仅重试剩余2项，均取得，实际退出0 | [v3协议](configs/temporal_hfq_sdk_retry_2020_2021_v3.json) |
| 全队列独立来源重建 | 113项来自原请求、8项来自公共参数请求、2项来自完整字段请求；123项均重新解析 | [全部来源QC](../research_outputs/temporal_hfq_source_qc_20261003_v3/results.json) |

每个协议最多两次尝试、两路并发、请求间隔至少8秒，每个子进程55秒超时后实际等待终止；中断未观察到退出时保留为未知。已完成的旧协议、旧失败、CLI日志和原载体不覆盖，新的来源通过显式选择表追溯。

请求参数来自已归档的[AKShare官方适配器](https://raw.githubusercontent.com/akfamily/akshare/master/akshare/stock_feature/stock_hist_em.py)，未执行或安装远端代码。v2保留原11字段，使用公开`ut`参数及文档顺序；v3使用完整`fields2`，额外请求`f116`。该适配器预期11个K线数值，本轮实际返回也为11个；没有删列、截断响应或改变字段值以通过检查。完整URL、协议哈希和来源版本保存于[123项来源选择表](../research_outputs/temporal_hfq_source_qc_20261003_v3/selected_sources.json)。Firecrawl产物为呈现后的JSON载体，不能称为原HTTP线路字节。

## 数值核对

17,194个后复权OHLC位置均严格为正，与同期不复权的成交量、成交额、换手率原文均相同。17,065个相邻交易日正收盘比例、原涨跌幅/涨跌额的序列化舍入区间均相容。停牌跨度另外记录6个累计比例，未分配到缺行日，也未转成普通每日收益。原不复权和后复权的286个未知字段各自保留。

[独立比例复核](../research_outputs/temporal_hfq_ratios_verification_20261003_v3.json)从原数据以Fraction核对17,071个比例及34,130项区间比较，789份来源绑定通过。后复权价格不能成为物理成交价，也不认证供应商公司行为算法、经济总收益或点时版本。本轮没有计算2021年模型效果，语义入口仍关闭。

## 可复算材料

- [全17,220个后复权来源位置](../research_outputs/temporal_hfq_source_qc_20261003_v3/source_positions.jsonl.gz)。
- [全部报价原文与比例诊断](../research_outputs/temporal_hfq_ratios_20261003_v3/positions.jsonl.gz)。
- [独立比例复核](../research_outputs/temporal_hfq_ratios_verification_20261003_v3.json)。
- [2021年信息时点与窗口预检](TEMPORAL_INPUT_PREFLIGHT_2021.md)。

旧[113家公司阶段报告](TEMPORAL_HFQ_RATIOS_2020_2021.md)和其实际失败退出继续保留为历史阶段证据；当前后复权来源已补齐，不能把来源补齐表述为市场仿真已验证。
