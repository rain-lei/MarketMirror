export const DEMO_SCENARIOS = [
  {
    key: 'deleveraging-2018',
    title: '2018 去杠杆冲击',
    severity: '高',
    severityLevel: 'high',
    horizon: '中期 (3-6个月)',
    industry: '房地产行业',
    event: '监管层连续收紧融资和资管通道，叠加去杠杆政策推进，地产与高负债板块流动性迅速收缩。',
    impact: -0.64,
    scenarioTag: 'deleveraging:real-estate',
    riskSignals: ['信用利差走阔', '融资渠道收缩', '资产抛售压力'],
    description: '模拟金融去杠杆政策对高负债行业的冲击传导路径',
  },
  {
    key: 'pandemic-2020',
    title: '2020 疫情突发',
    severity: '极高',
    severityLevel: 'critical',
    horizon: '短期 (1-3个月)',
    industry: 'A股主板',
    event: '疫情突发导致供应链停滞、消费收缩与风险偏好下降，市场在短期内出现剧烈波动与恐慌抛压。',
    impact: -0.72,
    scenarioTag: 'pandemic-shock:a-share',
    riskSignals: ['波动率飙升', '流动性折价', '避险资产走强'],
    description: '模拟突发公共事件对市场流动性与投资者情绪的瞬时冲击',
  },
  {
    key: 'tech-regulation-2021',
    title: '平台经济监管升级',
    severity: '中',
    severityLevel: 'medium',
    horizon: '中长期 (6-12个月)',
    industry: '科技行业',
    event: '平台经济监管规则集中发布，企业合规成本上升，估值体系重估并引发成长板块分化。',
    impact: -0.38,
    scenarioTag: 'regulation-shock:technology',
    riskSignals: ['估值中枢下移', '行业分化加剧', '高成长因子回撤'],
    description: '模拟监管政策变化对成长型行业的结构性影响',
  },
  {
    key: 'green-energy-boom',
    title: '新能源刺激政策',
    severity: '低',
    severityLevel: 'low',
    horizon: '中长期 (12-24个月)',
    industry: '新能源产业',
    event: '大规模绿色能源补贴与碳达峰政策密集出台，新能源产业链景气度快速提升，传统能源面临转型压力。',
    impact: 0.45,
    scenarioTag: 'policy-boost:new-energy',
    riskSignals: ['行业景气上行', '资本加速流入', '传统能源承压'],
    description: '模拟正向产业政策对新兴行业的利好驱动效应',
  },
  {
    key: 'bank-stress',
    title: '银行流动性压力',
    severity: '高',
    severityLevel: 'high',
    horizon: '短期 (1-3个月)',
    industry: '银行与保险',
    event: '部分中小银行存款流失加速，同业拆借利率飙升，叠加房地产不良贷款率上升引发金融体系流动性担忧。',
    impact: -0.58,
    scenarioTag: 'liquidity-stress:banking',
    riskSignals: ['同业利差扩大', '存款搬家', '不良率攀升'],
    description: '模拟银行业流动性危机对金融板块的系统性冲击',
  },
]

function buildActions(impact) {
  const absImpact = Math.abs(impact)
  const confBoost = Math.min(absImpact * 0.2, 0.1)

  return [
    {
      agent_type: 'Aggressive (激进型)',
      action_type:
        impact < -0.55
          ? 'Short Volatility / 做空波动'
          : impact < 0
            ? 'Buy on Dip / 抄底'
            : 'Leverage Long / 加杠杆做多',
      confidence: Math.min(0.75 + confBoost + (impact > 0 ? 0.05 : 0), 0.95),
      reasoning:
        impact < 0
          ? '高风险偏好交易者倾向在非理性波动中放大仓位，以换取超额收益。'
          : '积极型资金顺势加大风险敞口，捕捉上行趋势中的杠杆收益。',
    },
    {
      agent_type: 'Conservative (保守型)',
      action_type: impact < -0.5 ? 'Panic Sell / 恐慌性抛售' : impact < 0 ? 'Reduce Position / 减仓观望' : 'Wait and See / 观望',
      confidence: Math.min(0.7 + confBoost, 0.95),
      reasoning:
        impact < -0.3
          ? '保守资金优先考虑本金安全，在不确定性上升阶段降低风险暴露。'
          : '保守型投资者维持防御姿态，等待更明确的趋势信号。',
    },
    {
      agent_type: 'Institutional (机构型)',
      action_type: impact < -0.3 ? 'Hedge / 对冲风险' : impact > 0.3 ? 'Sector Rotation / 板块轮动' : 'Rebalance / 再平衡',
      confidence: Math.min(0.78 + confBoost, 0.92),
      reasoning:
        impact < 0
          ? '机构以风控模型和流动性约束为核心，执行对冲或仓位再平衡策略。'
          : '机构基于量化模型进行组合再平衡，优化风险收益比。',
    },
  ]
}

export function createOfflineDemoResult(eventDescription, targetIndustry, _investmentAmount = 0) {
  const matched = DEMO_SCENARIOS.find(
    (item) =>
      eventDescription.includes(item.title.slice(0, 4)) ||
      eventDescription.includes(item.industry)
  )

  const baseImpact = matched ? matched.impact : -0.28
  const scenarioTag = matched ? matched.scenarioTag : `general-stress:${targetIndustry || 'market'}`

  // Response matches backend API exactly
  return {
    policy_parsed: `政策解析：${eventDescription}。综合评估认为该事件对「${targetIndustry}」构成${baseImpact < -0.5 ? '重大' : baseImpact < -0.2 ? '中等' : '轻微'}冲击。`,
    market_impact_score: Number(baseImpact.toFixed(2)),
    scenario_tag: scenarioTag,
    agent_actions: buildActions(baseImpact),
    timestamp: Date.now() / 1000,
  }
}
