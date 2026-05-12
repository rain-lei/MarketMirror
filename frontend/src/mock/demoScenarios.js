export const DEMO_SCENARIOS = [
  {
    key: "deleveraging-2018",
    title: "2018 去杠杆冲击",
    severity: "高",
    horizon: "中期(3-6个月)",
    industry: "房地产行业",
    event: "监管层连续收紧融资和资管通道，叠加去杠杆政策推进，地产与高负债板块流动性迅速收缩。",
    impact: -0.64,
    scenarioTag: "deleveraging:real-estate",
    riskSignals: ["信用利差走阔", "融资渠道收缩", "资产抛售压力"],
    talkingPoints: ["强调资金链脆弱行业的顺周期风险", "展示机构对冲行为与散户恐慌差异"]
  },
  {
    key: "pandemic-2020",
    title: "2020 疫情突发",
    severity: "极高",
    horizon: "短期(1-3个月)",
    industry: "A股主板",
    event: "疫情突发导致供应链停滞、消费收缩与风险偏好下降，市场在短期内出现剧烈波动与恐慌抛压。",
    impact: -0.72,
    scenarioTag: "pandemic-shock:a-share",
    riskSignals: ["波动率飙升", "流动性折价", "避险资产走强"],
    talkingPoints: ["突出突发冲击对市场流动性的瞬时破坏", "展示保守型与机构型资金防御路径"]
  },
  {
    key: "tech-regulation-2021",
    title: "平台经济监管升级",
    severity: "中",
    horizon: "中长期(6-12个月)",
    industry: "科技行业",
    event: "平台经济监管规则集中发布，企业合规成本上升，估值体系重估并引发成长板块分化。",
    impact: -0.38,
    scenarioTag: "regulation-shock:technology",
    riskSignals: ["估值中枢下移", "行业分化加剧", "高成长因子回撤"],
    talkingPoints: ["强调结构性冲击而非系统性崩盘", "展示机构板块轮动与风格切换逻辑"]
  }
];

function buildActions(impact) {
  return [
    {
      agent_type: "Aggressive (激进型)",
      action_type: impact < -0.55 ? "Short Volatility / 做空波动" : impact < 0 ? "Buy on Dip / 抄底" : "Leverage Long / 加杠杆做多",
      confidence: impact < -0.55 ? 0.91 : 0.82,
      reasoning: "高风险偏好交易者倾向在非理性波动中放大仓位，以换取超额收益。"
    },
    {
      agent_type: "Conservative (保守型)",
      action_type: impact < -0.5 ? "Panic Sell / 恐慌性抛售" : "Wait and See / 观望",
      confidence: impact < -0.5 ? 0.93 : 0.84,
      reasoning: "保守资金优先考虑本金安全，在不确定性上升阶段降低风险暴露。"
    },
    {
      agent_type: "Institutional (机构型)",
      action_type: impact < 0 ? "Hedge / 对冲风险" : "Sector Rotation / 板块轮动",
      confidence: 0.88,
      reasoning: "机构以风控模型和流动性约束为核心，执行对冲或仓位再平衡策略。"
    }
  ];
}

export function createOfflineDemoResult(eventDescription, targetIndustry, investmentAmount = 10000) {
  const matched = DEMO_SCENARIOS.find((item) => eventDescription.includes(item.title.slice(0, 4)) || eventDescription.includes(item.industry));

  const baseImpact = matched ? matched.impact : -0.28;
  const scenarioTag = matched ? matched.scenarioTag : `general-stress:${targetIndustry || "market"}`;

  const expected_return_rate = Number((baseImpact * 0.4).toFixed(4));
  const expected_return = Number((investmentAmount * expected_return_rate).toFixed(2));

  return {
    policy_parsed: `Parsed Policy: ${eventDescription}. Interpreted as a structural stress event for ${targetIndustry}.`,
    market_impact_score: Number(baseImpact.toFixed(2)),
    scenario_tag: scenarioTag,
    agent_actions: buildActions(baseImpact),
    expected_return_rate,
    expected_return,
    timestamp: Date.now() / 1000
  };
}

export const DEMO_PRESENTATION_STEPS = [
  "先点击样例按钮，加载历史冲击背景",
  "执行离线演示，保证现场无网络也可稳定出结果",
  "对比三类 Agent 的置信度、策略强度和行为标签",
  "切换风险预警页展示监管预警指标与后续处置建议"
];
