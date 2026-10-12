# 实现与证据索引

本文件按职责定位实现，运行条件与使用命令见[研究操作说明](README.md)，平台操作见[平台说明](../design/README.md)。实验结果以各报告登记的来源、时期、版本和核验范围为准。

## 平台与引擎

| 能力 | 实现入口 | 证据或合同 |
| --- | --- | --- |
| 合成情景、参数快照、两组撮合与逐日审计 | `../design/engine.py`、`simulation/portfolio_market.py`、`simulation/portfolio_audit.py` | [平台说明](../design/README.md)、[真实原文实验](../design/REAL_SOURCE_EXPERIMENT_VALIDATION_20261006.md) |
| 同状态规则解释与敏感性 | `../design/strategy_preview.py`、`simulation/agent_decision_trace.py` | [固定状态结果](MECHANISM_SENSITIVITY_RESULTS_2026.md)、[预览核验](../design/STRATEGY_PREVIEW_VALIDATION_20261007.md) |
| 原文事实、证据和来源时钟 | `semantic/source_case_facts.py`、`simulation/source_case_decision_link.py` | [原文到决策链路](SOURCE_TEXT_TO_AGENT_DECISIONS_2026.md) |
| 批量、失败恢复与报告 | `../design/batches.py`、`../design/batch_metrics.py` | [批量核验](../design/BATCH_PLATFORM_VALIDATION_20261006.md) |
| 存档、草稿、版本比较与导出 | `../design/server.py`、`../design/draft-cache.js`、`../design/decision-view.js` | [提交](../design/SUBMISSION_VALIDATION_20261006.md)、[比较](../design/EXPERIMENT_COMPARISON_VALIDATION_20261007.md) |
| 组合净值、回撤、执行与超限 | `../design/portfolio-metrics.js`、`simulation/prototype_risk_metrics.py` | [平台风险](../design/PORTFOLIO_RISK_VALIDATION_20261008.md)、[归档风险](PROTOTYPE_RISK_DIAGNOSTICS_2026.md) |

## 数据与语义

| 能力 | 实现入口 | 证据或合同 |
| --- | --- | --- |
| 工作簿质量检查与统一来源记录 | `data_pipeline/profile_sources.py`、`data_pipeline/build_dataset.py` | [工作簿来源](USER_WORKBOOK_SOURCE_AUDIT_2026.md)、[数据合同](data_contracts/README.md) |
| 截止时点特征、回复隔离 | `data_pipeline/asof_features.py` | [特征出口审计](ASOF_FEATURE_ISOLATION_AUDIT_2026.md) |
| 财务字段与官方披露 | `data_pipeline/extract_financial.py`、`data_pipeline/issuer_disclosures.py` | [季度提示审计](FINANCIAL_QUARTER_ASSIGNMENT_AUDIT_2026.md)、[披露目录](ISSUER_FORMAL_DISCLOSURE_INVENTORY_2019_2020.md) |
| 行情、日历、收益与成交口径 | `data_pipeline/market_data.py`、`data_pipeline/market_activity.py`、`data_pipeline/crosscheck_quotes.py` | [第二行情源核对](INDEPENDENT_QUOTES_2018_2020.md) |
| 模型原答、规范化与引用核验 | `semantic/run_model.py`、`semantic/parse_model_outputs.py`、`semantic/quote_grounding.py` | [语义复核规则](semantic/ANNOTATION_GUIDE.md) |
| AI 参考标签、评分与接入 | `semantic/assistant_review.py`、`semantic/compare_holdout.py`、`semantic/agent_signal_adapter.py` | [AI 复核结果](AI_REVIEW_H2_2020.md)、[v6 独立评估](WUHAN_INDEPENDENT_EVALUATION_V6.md) |

## 实验与复现

| 能力 | 实现入口 | 证据 |
| --- | --- | --- |
| 事件研究、文本预测基线 | `baselines/event_study.py`、`baselines/run_prediction.py` | [研究命令](README.md) |
| 真实收益上的三类规则回放 | `simulation/historical_replay.py`、`simulation/single_observed_case.py` | [真实历史收益实验](REAL_OBSERVED_EXPERIMENT_20261006.md) |
| 有限撮合、反馈与资金诊断 | `simulation/portfolio_auction.py`、`simulation/portfolio_wallet_diagnostic.py` | [多资产机制](SEMANTIC_PORTFOLIO_H2_2020.md)、[资金诊断](SEMANTIC_WALLET_ALLOCATION_DIAGNOSTIC.md) |
| 资源条件全量执行与恢复 | `simulation/temporal_resource_unit_study_v2.py`、`simulation/resource_unit_checkpoint_runner.py` | [资源结果](TEMPORAL_RESOURCE_UNIT_RESULTS_2022.md)、[恢复凭据](RESOURCE_BATCH_RECOVERY_2026.md) |
| 清单完整性与独立重执行 | `registry/verify_catalog.py`、`registry/reexecute.py` | [归档复现](PROTOTYPE_REPRODUCTION_2026.md) |
| 机制归档读取与重放 | `prototype.py`、`replay_prototype_archives.py` | [研究总结](RESEARCH_SUMMARY_2026.md)、[完整重放](PROTOTYPE_FULL_REPLAY_2026.md) |
| 核验摘要、受控命令与离线报告 | `workbench/build.py`、`workbench/run.py`、`workbench/serve.py` | [研究摘要操作](README.md#归档研究摘要) |

## 能力边界

三类 Agent 是参数化规则；模型事实、情景映射和市场成交是不同层。官方披露数值读取通过不自动允许经济系数或收益预测，未核实字段不进入特征。AI 参考评分不称独立人工准确率；原武汉 v6 类别门槛未通过。

平台提供个人本地实验、可视化解释和固定版本导出。多用户部署、真实投资者参数识别、高保真历史重建与监管预测尚未完成。领域知识图谱及 Nexent 编排另有研究设想，未列为现有能力。
