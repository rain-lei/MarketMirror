<script setup>
import {
  DashboardOutlined,
  PlayCircleOutlined,
  StepForwardOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons-vue'
import { message } from 'ant-design-vue'
import { computed, onBeforeUnmount, reactive, ref } from 'vue'
import AgentDecisionMatrix from '../components/AgentDecisionMatrix.vue'
import MarketImpactChart from '../components/MarketImpactChart.vue'
import { usePlatformStore } from '../composables/usePlatformStore'
import { useTheme } from '../composables/useTheme'

const {
  addLog, formatTime, getAgentTone, getAgentToneLabel,
  industries, demoScenarios, runSimulation, createOfflineDemoResult,
  parseScenarioTag, getImpactSeverity,
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
  investment_amount: '',
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

function onInvestmentInput(val) {
  let raw = typeof val === 'string' ? val : val && val.target ? val.target.value : ''
  const prefixMatch = raw.match(/^\s*([$¥￥€]|[A-Za-z]{3})\s*/i)
  let prefix = ''
  if (prefixMatch) {
    prefix = prefixMatch[1]
    raw = raw.slice(prefixMatch[0].length)
  } else {
    prefix = investmentPrefix.value || ''
  }
  let cleaned = String(raw).replace(/-/g, '').replace(/[^\d.,]/g, '')
  if (cleaned.indexOf('.') !== -1) {
    const parts = cleaned.split('.')
    cleaned = parts.shift() + '.' + parts.join('')
  }
  cleaned = cleaned.replace(/^[,]+/, '')
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
    investmentError.value = '请输入有效的正数金额'
    return false
  }
  investmentError.value = ''
  const normalized = String(Number(raw))
  simulationForm.investment_amount = (prefix ? prefix + ' ' : '') + formatWithCommas(normalized)
  investmentPrefix.value = prefix
  return true
}

const canSubmit = computed(() =>
  simulationForm.event_description.trim().length > 7 && !loading.value
)

// ── Result Computed ──

const impactSeverity = computed(() =>
  result.value ? getImpactSeverity(result.value.market_impact_score) : null
)

const impactDirection = computed(() => {
  if (!result.value) return null
  const s = result.value.market_impact_score
  if (s < 0) return { text: '下行冲击', color: 'var(--color-danger)', icon: '↓' }
  if (s > 0) return { text: '上行扰动', color: 'var(--color-success)', icon: '↑' }
  return { text: '中性波动', color: 'var(--color-info)', icon: '→' }
})

const scenarioInfo = computed(() =>
  result.value ? parseScenarioTag(result.value.scenario_tag) : null
)

const selectedScenario = computed(() =>
  demoScenarios.find((item) => item.key === selectedScenarioKey.value) || null
)

const formattedTimestamp = computed(() =>
  result.value ? formatTime(result.value.timestamp * 1000) : ''
)

// Agent-specific color mapping
function getAgentColor(agentType) {
  const t = String(agentType || '')
  if (t.includes('Aggressive') || t.includes('激进')) return { bg: '#fef2f2', border: '#fecaca', text: '#dc2626', dot: '#ef4444' }
  if (t.includes('Conservative') || t.includes('保守')) return { bg: '#eff6ff', border: '#bfdbfe', text: '#2563eb', dot: '#3b82f6' }
  if (t.includes('Institutional') || t.includes('机构')) return { bg: '#ecfdf5', border: '#a7f3d0', text: '#059669', dot: '#10b981' }
  return { bg: 'var(--bg-base)', border: 'var(--border-color)', text: 'var(--text-primary)', dot: 'var(--text-tertiary)' }
}

function getAgentLabel(agentType) {
  const t = String(agentType || '')
  if (t.includes('Aggressive') || t.includes('激进')) return '激进型'
  if (t.includes('Conservative') || t.includes('保守')) return '保守型'
  if (t.includes('Institutional') || t.includes('机构')) return '机构型'
  return t
}

function getAgentRoleDesc(agentType) {
  const t = String(agentType || '')
  if (t.includes('Aggressive') || t.includes('激进')) return '风险偏好型交易者'
  if (t.includes('Conservative') || t.includes('保守')) return '本金优先型投资者'
  if (t.includes('Institutional') || t.includes('机构')) return '风控驱动型机构'
  return ''
}

// ── Actions ──

function applyScenario(scenario) {
  selectedScenarioKey.value = scenario.key
  simulationForm.event_description = scenario.event
  simulationForm.target_industry = scenario.industry
  error.value = ''
  addLog('演示', `已加载样例：${scenario.title}`)
  message.info(`已加载场景：${scenario.title}`)
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
    const payload = {
      ...simulationForm,
      investment_amount: Number(String(simulationForm.investment_amount || '').replace(/[^\d.]/g, '')) || 0,
    }
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
    <!-- ======== Left: Input Panel ======== -->
    <div>
      <a-card :bordered="false" class="glass-card">
        <template #title>
          <div class="card-title"><ThunderboltOutlined /> 场景推演输入</div>
        </template>

        <a-form layout="vertical">
          <a-form-item label="冲击事件描述">
            <a-textarea
              v-model:value="simulationForm.event_description"
              :rows="4"
              placeholder="描述市场冲击事件，例如：宏观流动性收紧叠加地产融资约束..."
              :auto-size="{ minRows: 3, maxRows: 6 }"
            />
          </a-form-item>

          <a-form-item label="目标行业">
            <a-select
              v-model:value="simulationForm.target_industry"
              :options="industries.map((v) => ({ label: v, value: v }))"
              placeholder="选择目标行业"
            />
          </a-form-item>

          <a-form-item label="投资金额 (元)">
            <a-input
              v-model:value="simulationForm.investment_amount"
              placeholder="例如：10000"
              @input="onInvestmentInput"
              @blur="checkInvestment"
            />
            <a-alert
              v-if="investmentError"
              type="error"
              :message="investmentError"
              show-icon
              class="mt-8"
            />
          </a-form-item>
        </a-form>

        <!-- Action Buttons -->
        <div style="display: flex; gap: 10px; margin-bottom: 14px">
          <a-button
            type="primary"
            size="large"
            :loading="loading"
            :disabled="!canSubmit"
            @click="submitSimulation"
            style="flex: 1; height: 44px; font-weight: 600"
          >
            <template #icon><PlayCircleOutlined /></template>
            启动推演
          </a-button>
          <a-button size="large" :disabled="!canSubmit" @click="runOfflineDemo" style="height: 44px">
            离线演示
          </a-button>
        </div>

        <a-alert v-if="error" type="warning" :message="error" show-icon style="margin-bottom: 12px" />

        <!-- Scenario Library -->
        <div class="sample-box">
          <div class="sample-head">
            <span class="sample-title">演示样例库</span>
            <a-space size="small">
              <a-button size="small" @click="useNextScenario">
                <template #icon><StepForwardOutlined /></template>
              </a-button>
              <a-button
                size="small"
                :type="autoPlaying ? 'primary' : 'default'"
                @click="toggleAutoPlayDemo"
              >
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
              <div class="sample-signals" style="margin-top: 6px">
                <span v-for="sig in scenario.riskSignals" :key="sig">{{ sig }}</span>
              </div>
            </button>
          </div>
        </div>
      </a-card>
    </div>

    <!-- ======== Right: Results Panel ======== -->
    <div>
      <a-card :bordered="false" class="glass-card">
        <template #title>
          <div class="card-title"><DashboardOutlined /> 推演结果</div>
        </template>
        <template #extra>
          <a-tag v-if="result" :color="demoMode === 'online' ? 'blue' : 'default'" style="font-size: 11px">
            {{ demoMode === 'online' ? '在线' : '离线' }}
          </a-tag>
        </template>

        <template v-if="result">
          <!-- Impact Score Hero -->
          <div class="sim-hero" :style="{ borderColor: impactDirection?.color || 'var(--border-color)' }">
            <div class="sim-hero-main">
              <div class="sim-hero-score" :style="{ color: impactDirection?.color }">
                {{ result.market_impact_score > 0 ? '+' : '' }}{{ result.market_impact_score.toFixed(2) }}
              </div>
              <div class="sim-hero-direction" :style="{ color: impactDirection?.color }">
                {{ impactDirection?.icon }} {{ impactDirection?.text }}
              </div>
            </div>
            <div class="sim-hero-meta">
              <span
                v-if="impactSeverity"
                class="sim-severity-badge"
                :style="{ background: impactSeverity.color + '1a', color: impactSeverity.color, borderColor: impactSeverity.color + '40' }"
              >
                {{ impactSeverity.label }}
              </span>
              <span v-if="scenarioInfo" class="sim-tag-badge">
                {{ scenarioInfo.type }} / {{ scenarioInfo.industry }}
              </span>
            </div>
          </div>

          <!-- Policy Parsed -->
          <div class="sim-policy-box">
            <div class="sim-policy-label">分析结论</div>
            <p class="sim-policy-text">{{ result.policy_parsed }}</p>
          </div>

          <!-- Scenario Tag + Timestamp -->
          <div class="sim-meta-row">
            <span class="sim-scenario-tag">
              <code>{{ result.scenario_tag }}</code>
            </span>
            <span class="sim-timestamp">{{ formattedTimestamp }}</span>
          </div>

          <!-- Impact Chart -->
          <MarketImpactChart
            :impact="result.market_impact_score"
            :scenario-tag="result.scenario_tag"
            :theme="theme"
          />

          <!-- Agent Decision Cards -->
          <a-divider orientation="left" style="margin: 16px 0 10px">
            <span style="font-size: 13px; font-weight: 600">三类投资者决策对比</span>
          </a-divider>

          <div class="sim-agent-cards">
            <div
              v-for="item in result.agent_actions"
              :key="item.agent_type"
              class="sim-agent-card"
              :style="{
                borderLeftColor: getAgentColor(item.agent_type).dot,
                background: getAgentColor(item.agent_type).bg,
                borderColor: getAgentColor(item.agent_type).border,
              }"
            >
              <div class="sim-agent-card-header">
                <div>
                  <span class="sim-agent-dot" :style="{ background: getAgentColor(item.agent_type).dot }"></span>
                  <strong :style="{ color: getAgentColor(item.agent_type).text }">
                    {{ getAgentLabel(item.agent_type) }}
                  </strong>
                  <span class="sim-agent-role">{{ getAgentRoleDesc(item.agent_type) }}</span>
                </div>
                <span class="action-chip" :class="`tone-${getAgentTone(item.action_type)}`">
                  {{ item.action_type }}
                </span>
              </div>

              <div class="sim-agent-confidence">
                <div class="sim-confidence-bar-bg">
                  <div
                    class="sim-confidence-bar-fill"
                    :style="{
                      width: Math.round(item.confidence * 100) + '%',
                      background: getAgentColor(item.agent_type).dot,
                    }"
                  ></div>
                </div>
                <span class="sim-confidence-num" :style="{ color: getAgentColor(item.agent_type).text }">
                  {{ Math.round(item.confidence * 100) }}%
                </span>
              </div>

              <p class="sim-agent-reason">{{ item.reasoning }}</p>
            </div>
          </div>

          <!-- Agent Behavior Chart -->
          <a-divider orientation="left" style="margin: 14px 0 10px">
            <span style="font-size: 13px; font-weight: 600">行为强度量化映射</span>
          </a-divider>
          <AgentDecisionMatrix :agent-actions="result.agent_actions" :theme="theme" />
        </template>

        <!-- Empty State -->
        <a-empty v-else description="请先执行一次推演" style="padding: 40px 0">
          <template #image>
            <DashboardOutlined style="font-size: 48px; color: var(--text-tertiary)" />
          </template>
        </a-empty>
      </a-card>
    </div>
  </section>
</template>

<style scoped>
/* ── Impact Score Hero ── */
.sim-hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 16px 20px;
  border-radius: var(--border-radius-lg);
  border: 2px solid var(--border-color);
  background: var(--bg-base);
  margin-bottom: 14px;
}

.sim-hero-main {
  display: flex;
  align-items: baseline;
  gap: 12px;
}

.sim-hero-score {
  font-size: 40px;
  font-weight: 800;
  font-family: 'Inter', sans-serif;
  line-height: 1;
  letter-spacing: -1px;
}

.sim-hero-direction {
  font-size: 16px;
  font-weight: 600;
}

.sim-hero-meta {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 6px;
}

.sim-severity-badge {
  display: inline-flex;
  align-items: center;
  padding: 3px 12px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  border: 1px solid;
}

.sim-tag-badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 11px;
  color: var(--text-secondary);
  background: var(--bg-container);
  border: 1px solid var(--border-color);
}

/* ── Policy Box ── */
.sim-policy-box {
  padding: 12px 16px;
  border-radius: var(--border-radius);
  background: var(--primary-50);
  border: 1px solid var(--primary-200);
  margin-bottom: 12px;
}

[data-theme='dark'] .sim-policy-box {
  background: rgba(59, 130, 246, 0.08);
  border-color: rgba(59, 130, 246, 0.2);
}

.sim-policy-label {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--primary-500);
  margin-bottom: 4px;
}

.sim-policy-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.65;
  color: var(--text-primary);
}

/* ── Meta Row ── */
.sim-meta-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.sim-scenario-tag code {
  font-size: 11px;
  color: var(--text-tertiary);
  background: var(--bg-base);
  padding: 2px 8px;
  border-radius: 4px;
  font-family: 'SF Mono', 'Cascadia Code', monospace;
}

.sim-timestamp {
  font-size: 11px;
  color: var(--text-tertiary);
}

/* ── Agent Cards ── */
.sim-agent-cards {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.sim-agent-card {
  padding: 12px 14px;
  border-radius: var(--border-radius);
  border: 1px solid;
  border-left: 4px solid;
  transition: box-shadow var(--transition-fast);
}

.sim-agent-card:hover {
  box-shadow: var(--shadow-sm);
}

.sim-agent-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}

.sim-agent-card-header > div:first-child {
  display: flex;
  align-items: center;
  gap: 8px;
}

.sim-agent-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.sim-agent-role {
  font-size: 11px;
  color: var(--text-tertiary);
  margin-left: 4px;
}

/* ── Confidence Bar ── */
.sim-agent-confidence {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}

.sim-confidence-bar-bg {
  flex: 1;
  height: 6px;
  border-radius: 3px;
  background: var(--border-color-light);
  overflow: hidden;
}

.sim-confidence-bar-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.6s cubic-bezier(0.4, 0, 0.2, 1);
}

.sim-confidence-num {
  font-size: 13px;
  font-weight: 700;
  font-family: 'Inter', sans-serif;
  min-width: 38px;
  text-align: right;
}

/* ── Agent Reason ── */
.sim-agent-reason {
  margin: 0;
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.55;
}

/* ── Responsive ── */
@media (max-width: 1120px) {
  .sim-hero {
    flex-direction: column;
    align-items: flex-start;
  }

  .sim-hero-meta {
    align-items: flex-start;
  }
}
</style>
