# 初始资源配置完整对照：启动记录

最新状态：已从27个完整检查点恢复并完成剩余53个条件，全部80个条件生产及独立账本审计完成，10个子进程实际退出0。最终重算160个原/新条件、80项配对、40项交互及314,880个账户摘要；联合通过0/80，五项差距同时不恶化0/80，没有采用新默认。285项冻结绑定、原中断日志及未完成目录保持。完整效果见[结果报告](TEMPORAL_INITIAL_ALLOCATION_RESULTS_2021.md)，[恢复起点证据](../research_outputs/initial_allocation_recovery_20261003_v2_start.json)与[实际退出记录](../research_outputs/initial_allocation_jobs_20261003_v1.json)分开保留。

下文保留启动范围及预检证据。启动时登记80组新增实验，范围为原123家公司、58日、5束种子和16组条件。原80组已完成条件作为基线；初始配置在原固定库存目标与当前持仓目标两种设置下分别比较，并计算40项交互；完整执行和效果以以上结果报告为准。

新增配置按每个主体原财富和常态仓位计算整手持仓，由有限背景账户承接净股份并支付相应现金。每个账户的初始财富、市场总现金及每只股票总股份保持。背景目标仍为原设置；没有把重分配后的持仓偷偷替换为新的固定目标。

启动前证据：

- [32条完整路径预检](../research_outputs/temporal_initial_allocation_path_preflight_20261003_v1.json)：含全部16条件和两个篮子，32次禁用适配器的完整结果桥接、1,856份日账本和5,568次资产调用独立核验。
- [全公司初始化核验](../research_outputs/initial_allocation_independent_initializer_preflight_20261003_v1.json)：5束种子、41篮子、205个初始组合及9,840个账户由独立算法重建，与生产初始化一致。
- [全公司单条件端到端预检](../research_outputs/initial_allocation_full_condition_preflight_20261003_v1.json)：正式生产器及独立审计器覆盖123家公司、41个篮子、完整58日；两个子进程实际退出0。
- [冻结前完整回归](../research_outputs/initial_allocation_full_suite_20261003_v1.json)：975项通过，0失败、0错误、0跳过。加入配对统计模块后另执行了[当前完整回归](../research_outputs/initial_allocation_full_suite_20261003_v2.json)：单次运行978项通过，0失败、0错误、0跳过；不是累加历史运行次数。

[冻结数值协议](configs/temporal_initial_allocation_2021_v1.json)绑定285项代码与来源证据。生产保存每个条件完整账本，独立审计重建初始账户并逐日检查信息、报价、成交、资金、T+1、协方差及最终财富/回撤/风险。每组首篮子完整重跑，最终保留全部80项配对和40项交互，包括负面或未定义结果。

本项是已有偏差诊断后的机制开发实验，不是真实投资者校准或预测验证。前序[预热完整结果](TEMPORAL_CARRIED_WARMUP_RESULTS_2021.md)五项联合通过0/80，[流动性与价格机制](TEMPORAL_LIQUIDITY_AUCTION_RESULTS_2021.md)通过0/320；这些证据不支持直接宣称高保真。当前未采用新默认，整体研究目标仍未完成。
