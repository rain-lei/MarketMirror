import { reactive, ref } from 'vue'
import { API_BASE_URL, APP_NAME } from '../config'
import { createOfflineDemoResult, DEMO_PRESENTATION_STEPS, DEMO_SCENARIOS } from '../mock/demoScenarios'
import { runSimulation } from '../services/api'

const logs = reactive([])
const industries = ['房地产行业', '科技行业', 'A股主板', '银行与保险', '新能源产业']
const demoScenarios = DEMO_SCENARIOS
const presentationSteps = DEMO_PRESENTATION_STEPS

const TONE_LABEL_MAP = {
  risk: '高风险',
  aggressive: '进攻型',
  balanced: '平衡型',
  cautious: '防御型'
}

export function usePlatformStore() {
  function addLog(type, text) {
    logs.unshift({ type, text, time: Date.now() })
    if (logs.length > 50) logs.splice(50)
  }

  function formatTime(ts) {
    return new Date(ts).toLocaleString('zh-CN', { hour12: false })
  }

  function getAgentTone(actionType) {
    const text = String(actionType || '')
    if (text.includes('Panic') || text.includes('抛售')) return 'risk'
    if (text.includes('Leverage') || text.includes('杠杆')) return 'aggressive'
    if (text.includes('Short') || text.includes('做空')) return 'aggressive'
    if (text.includes('Buy') || text.includes('抄底')) return 'aggressive'
    if (text.includes('Hedge') || text.includes('对冲')) return 'balanced'
    if (text.includes('Sector') || text.includes('轮动')) return 'balanced'
    if (text.includes('Wait') || text.includes('观望')) return 'cautious'
    return 'balanced'
  }

  function getAgentToneLabel(actionType) {
    return TONE_LABEL_MAP[getAgentTone(actionType)] || '平衡型'
  }

  function buildPresenterScript(form, result, selectedScenario) {
    if (!result) return ''
    const impactText = result.market_impact_score < 0 ? '下行冲击'
      : result.market_impact_score > 0 ? '上行扰动' : '中性波动'
    const actions = result.agent_actions || []
    const leading = actions.reduce((best, cur) =>
      (cur.confidence || 0) > (best.confidence || 0) ? cur : best, actions[0])
    return [
      `【场景】${selectedScenario?.title || '自定义冲击'}`,
      `【行业】${form.target_industry}`,
      `【结论】市场影响指数 ${result.market_impact_score}，判断为${impactText}`,
      `【主导行为】${leading?.agent_type || 'N/A'}，动作 ${leading?.action_type || 'N/A'}`,
      `【解释】${result.policy_parsed}`
    ].join('\n')
  }

  return {
    logs,
    industries,
    demoScenarios,
    presentationSteps,
    APP_NAME,
    API_BASE_URL,
    addLog,
    formatTime,
    getAgentTone,
    getAgentToneLabel,
    buildPresenterScript,
    runSimulation,
    createOfflineDemoResult
  }
}
