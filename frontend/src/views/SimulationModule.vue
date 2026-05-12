<script setup>
import {
  DashboardOutlined,
  PlayCircleOutlined,
  StepForwardOutlined,
  ThunderboltOutlined
} from '@ant-design/icons-vue'
import { message } from 'ant-design-vue'
import { computed, onBeforeUnmount, reactive, ref } from 'vue'
import AgentDecisionMatrix from '../components/AgentDecisionMatrix.vue'
import MarketImpactChart from '../components/MarketImpactChart.vue'
import { usePlatformStore } from '../composables/usePlatformStore'
import { useTheme } from '../composables/useTheme'

const {
  addLog,
  formatTime,
  getAgentTone,
  getAgentToneLabel,
  industries,
  demoScenarios,
  runSimulation,
  createOfflineDemoResult
} = usePlatformStore()

const { theme } = useTheme()

const loading = ref(false)
const error = ref('')
const lastUpdated = ref('')
const demoMode = ref('online')
const selectedScenarioKey = ref('')
const autoPlaying = ref(false)
const autoPlayIndex = ref(0)
let autoPlayTimer = null

const simulationForm = reactive({
  event_description: '2020年初突发新冠疫情全球蔓延，全球供应链受阻，多地面临封锁。',
  target_industry: '房地产行业',
  investment_amount: ''
})

const result = ref(null)
const investmentError = ref('')
const investmentPrefix = ref('')

const investmentValid = computed(() => {
  const raw = String(simulationForm.investment_amount || '').replace(/[^\d.]/g, '').trim()
  if (raw === '') return false
  if (!/^\d+(\.\d+)?$/.test(raw)) return false
  return Number(raw) >= 0
})

function formatWithCommas(numStr) {
  const [intPart, decPart] = String(numStr).split('.')
  const formattedInt = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return decPart !== undefined ? `${formattedInt}.${decPart}` : formattedInt
}

function checkInvestment() {
  const rawFull = String(simulationForm.investment_amount || '')
  const prefixMatch = rawFull.match(/^\s*([$¥￥€]|[A-Za-z]{3})\s*/i)
  let prefix = ''
  let body = rawFull
  if (prefixMatch) {
    prefix = prefixMatch[1]
    body = rawFull.slice(prefixMatch[0].length)
  }
  const raw = String(body).replace(/[^\d.]/g, '').trim()
  if (raw === '') {
    investmentError.value = '请输入投资金额'
    return false
  }
  if (!/^\d+(\.\d+)?$/.test(raw) || Number(raw) < 0) {
    investmentError.value = '请输入有效的正数金额（例如 10000）'
    return false
  }
  investmentError.value = ''
  // normalize input and format with commas for display
  const normalized = String(Number(raw))
  simulationForm.investment_amount = (prefix ? prefix + ' ' : '') + formatWithCommas(normalized)
  investmentPrefix.value = prefix
  return true
}

function onInvestmentInput(val) {
  let raw = ''
  if (typeof val === 'string') raw = val
  else if (val && val.target) raw = val.target.value
  else raw = ''

  // detect leading currency symbol or 3-letter code (e.g., $, ¥, ￥, €, USD, CNY)
  const prefixMatch = raw.match(/^\s*([$¥￥€]|[A-Za-z]{3})\s*/i)
  let prefix = ''
  if (prefixMatch) {
    prefix = prefixMatch[1]
    raw = raw.slice(prefixMatch[0].length)
  } else {
    prefix = investmentPrefix.value || ''
  }

  // remove minus and keep only digits, commas and dot
  let cleaned = String(raw).replace(/-/g, '').replace(/[^\d.,]/g, '')
  // preserve only first dot if multiple
  if (cleaned.indexOf('.') !== -1) {
    const parts = cleaned.split('.')
    cleaned = parts.shift() + '.' + parts.join('')
  }
  // remove leading commas
  cleaned = cleaned.replace(/^[,]+/, '')

  // format integer part with commas while preserving decimals
  if (cleaned.includes('.')) {
    const [intPart, decPart] = cleaned.split('.')
    const intClean = intPart.replace(/,/g, '')
    simulationForm.investment_amount = (prefix ? prefix + ' ' : '') + formatWithCommas(intClean + (decPart ? '.' + decPart : ''))
  } else {
    const intClean = cleaned.replace(/,/g, '')
    simulationForm.investment_amount = (prefix ? prefix + ' ' : '') + formatWithCommas(intClean)
  }
  investmentError.value = ''
  investmentPrefix.value = prefix
}

const canSubmit = computed(() =>
  simulationForm.event_description.trim().length > 7 && !loading.value && investmentValid.value
)

const impactTone = computed(() => {
  if (!result.value) return 'neutral'
  return result.value.market_impact_score < 0 ? 'down' : 'up'
})

const impactText = computed(() => {
  if (!result.value) return '待分析'
  if (result.value.market_impact_score < 0) return '下行冲击'
  if (result.value.market_impact_score > 0) return '上行扰动'
  return '中性波动'
})

const selectedScenario = computed(() =>
  demoScenarios.find(item => item.key === selectedScenarioKey.value) || null
)

function applyScenario(scenario) {
  selectedScenarioKey.value = scenario.key
  simulationForm.event_description = scenario.event
  simulationForm.target_industry = scenario.industry
  error.value = ''
  addLog('演示', `已加载样例：${scenario.title}`)
  message.info(`已加载 ${scenario.title}`)
}

function runOfflineDemo() {
  if (!canSubmit.value) {
    error.value = '冲击事件描述至少 8 个字'
    return
  }
  const amount = Number(String(simulationForm.investment_amount || '').replace(/[^\d.]/g, '')) || 0
  const data = createOfflineDemoResult(simulationForm.event_description, simulationForm.target_industry, amount)
  result.value = data
  lastUpdated.value = formatTime(data.timestamp * 1000)
  demoMode.value = 'offline'
  error.value = ''
  addLog('演示', `离线演示完成：${data.scenario_tag}`)
  message.success('离线演示结果已生成')
}

async function submitSimulation() {
  if (!canSubmit.value) {
    error.value = '冲击事件描述至少 8 个字'
    return
  }
  error.value = ''
  loading.value = true
  try {
    const payload = { ...simulationForm, investment_amount: Number(String(simulationForm.investment_amount || '').replace(/[^\d.]/g, '')) || 0 }
    const data = await runSimulation(payload)
    result.value = data
    lastUpdated.value = formatTime(data.timestamp * 1000)
    demoMode.value = 'online'
    addLog('推演', `完成场景 ${data.scenario_tag || 'general-stress'}`)
    message.success('沙盒推演完成')
  } catch (err) {
    error.value = `${err.message}，已自动切换离线演示结果`
    addLog('异常', `${err.message}，切换离线演示`)
    runOfflineDemo()
  } finally {
    loading.value = false
  }
}

function useNextScenario() {
  if (!demoScenarios.length) return
  const next = demoScenarios[autoPlayIndex.value % demoScenarios.length]
  autoPlayIndex.value += 1
  applyScenario(next)
}

function toggleAutoPlayDemo() {
  if (autoPlaying.value) {
    clearInterval(autoPlayTimer)
    autoPlayTimer = null
    autoPlaying.value = false
    message.info('自动演示已停止')
    return
  }
  autoPlaying.value = true
  useNextScenario()
  runOfflineDemo()
  autoPlayTimer = setInterval(() => {
    useNextScenario()
    runOfflineDemo()
  }, 7000)
  message.success('自动演示已启动，每7秒轮播一个样例')
}

onBeforeUnmount(() => {
  if (autoPlayTimer) {
    clearInterval(autoPlayTimer)
    autoPlayTimer = null
  }
})
</script>

<template>
  <section class="module-grid">
    <!-- Input Panel -->
    <a-card :bordered="false" class="glass-card">
      <template #title>
        <div class="card-title"><ThunderboltOutlined /> 场景推演输入</div>
      </template>

      <a-form layout="vertical">
        <a-form-item label="冲击事件描述">
          <a-textarea
            v-model:value="simulationForm.event_description"
            :rows="5"
            placeholder="例如：宏观流动性收紧叠加地产融资约束，导致信用扩张显著放缓"
          />
        </a-form-item>
        <a-form-item label="目标行业">
          <a-select
            v-model:value="simulationForm.target_industry"
            :options="industries.map(v => ({ label: v, value: v }))"
          />
        </a-form-item>
        <a-form-item label="投资金额 (元)">
          <a-input v-model:value="simulationForm.investment_amount" placeholder="例如：10000" style="width: 100%" @input="onInvestmentInput" @blur="checkInvestment" />
          <a-alert v-if="investmentError" type="error" :message="investmentError" show-icon class="mt-8" />
        </a-form-item>
      </a-form>

      <div class="sample-box">
        <div class="sample-head">
          <p class="sample-title">演示样例库（点击加载）</p>
          <a-space>
            <a-button size="small" @click="useNextScenario">
              <template #icon><StepForwardOutlined /></template>
            </a-button>
            <a-button size="small" type="primary" ghost @click="toggleAutoPlayDemo">
              <template #icon><PlayCircleOutlined /></template>
              {{ autoPlaying ? '停止' : '自动连播' }}
            </a-button>
          </a-space>
        </div>

        <div class="sample-card-grid">
          <button
            v-for="scenario in demoScenarios"
            :key="scenario.key"
            type="button"
            class="sample-card"
            :class="{ active: selectedScenarioKey === scenario.key }"
            @click="applyScenario(scenario)"
          >
            <div class="sample-card-top">
              <strong>{{ scenario.title }}</strong>
              <span class="sample-badge">{{ scenario.severity }}</span>
            </div>
            <p>{{ scenario.industry }} · {{ scenario.horizon }}</p>
          </button>
        </div>
      </div>

      <a-space direction="vertical" style="width: 100%">
        <a-button type="primary" size="large" :loading="loading" :disabled="!canSubmit" @click="submitSimulation" block>
          启动推演
        </a-button>
        <a-button size="middle" :disabled="!canSubmit" @click="runOfflineDemo" block>
          离线演示
        </a-button>
        <a-alert v-if="error" type="warning" :message="error" show-icon />
      </a-space>
    </a-card>

    <!-- Results Panel -->
    <a-card :bordered="false" class="glass-card">
      <template #title>
        <div class="card-title"><DashboardOutlined /> 推演结果</div>
      </template>

      <template v-if="result">
        <div class="kpi-row">
          <a-statistic
            title="市场影响指数"
            :value="result.market_impact_score"
            :value-style="impactTone === 'down' ? { color: '#d92d20' } : { color: '#079455' }"
          />
          <a-statistic title="冲击方向" :value="impactText" />
          <a-statistic title="场景标签" :value="result.scenario_tag" />
          <a-statistic
            title="预期收益 (元)"
            :value="result.expected_return"
            :value-style="result.expected_return < 0 ? { color: '#d92d20' } : { color: '#079455' }"
          />
          <a-statistic
            title="预期收益率"
            :value="(result.expected_return_rate * 100).toFixed(2) + '%'"
          />
        </div>

        <p class="result-note">{{ result.policy_parsed }}</p>
        <p class="result-time">{{ lastUpdated }} · {{ demoMode === 'online' ? '在线推演' : '离线演示' }}</p>

        <MarketImpactChart
          :impact="result.market_impact_score"
          :scenario-tag="result.scenario_tag"
          :theme="theme"
        />

        <a-divider orientation="left">Agent 行为强度</a-divider>
        <div class="agent-visual-grid">
          <a-card size="small" class="agent-visual-card" :bordered="false">
            <template #title>行为强度映射</template>
            <AgentDecisionMatrix
              :agent-actions="result.agent_actions"
              :theme="theme"
            />
          </a-card>

          <div class="agent-insight-list">
            <div class="agent-insight-card" v-for="item in result.agent_actions" :key="item.agent_type">
              <div class="agent-insight-top">
                <strong>{{ item.agent_type }}</strong>
                <span class="action-chip" :class="`tone-${getAgentTone(item.action_type)}`">
                  {{ item.action_type }}
                </span>
              </div>
              <a-progress :percent="Math.round(item.confidence * 100)" size="small" :show-info="false" />
              <p class="result-time">置信度 {{ Math.round(item.confidence * 100) }}%</p>
            </div>
          </div>
        </div>
      </template>
      <a-empty v-else description="请先执行一次推演" />
    </a-card>
  </section>
</template>
