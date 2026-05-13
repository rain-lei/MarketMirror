import { reactive } from 'vue'
import { API_BASE_URL, APP_NAME } from '../config'
import { createOfflineDemoResult, DEMO_SCENARIOS } from '../mock/demoScenarios'
import { runSimulation } from '../services/api'

const logs = reactive([])
const industries = ['房地产行业', '科技行业', 'A股主板', '银行与保险', '新能源产业']
const demoScenarios = DEMO_SCENARIOS

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
    if (text.includes('Reduce') || text.includes('减仓')) return 'cautious'
    if (text.includes('Rebalance') || text.includes('再平衡')) return 'balanced'
    return 'balanced'
  }

  function getAgentToneLabel(actionType) {
    const map = {
      risk: '高风险',
      aggressive: '进攻型',
      balanced: '平衡型',
      cautious: '防御型',
    }
    return map[getAgentTone(actionType)] || '平衡型'
  }

  function parseScenarioTag(tag) {
    if (!tag) return { type: '未知', industry: '未知' }
    const parts = tag.split(':')
    const typeMap = {
      'deleveraging': '去杠杆冲击',
      'pandemic-shock': '疫情冲击',
      'regulation-shock': '监管冲击',
      'general-stress': '一般压力',
      'policy-boost': '政策利好',
      'liquidity-stress': '流动性压力',
    }
    return {
      type: typeMap[parts[0]] || parts[0],
      industry: parts[1] || '',
    }
  }

  function getImpactSeverity(score) {
    const abs = Math.abs(score)
    if (abs >= 0.6) return { label: '剧烈冲击', color: '#ef4444', level: 'critical' }
    if (abs >= 0.4) return { label: '显著冲击', color: '#f59e0b', level: 'high' }
    if (abs >= 0.2) return { label: '中度波动', color: '#3b82f6', level: 'medium' }
    return { label: '轻微波动', color: '#10b981', level: 'low' }
  }

  return {
    logs,
    industries,
    demoScenarios,
    APP_NAME,
    API_BASE_URL,
    addLog,
    formatTime,
    getAgentTone,
    getAgentToneLabel,
    parseScenarioTag,
    getImpactSeverity,
    runSimulation,
    createOfflineDemoResult,
  }
}
