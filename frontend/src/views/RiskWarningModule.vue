<script setup>
import { computed } from 'vue'
import {
  AlertOutlined,
  DashboardOutlined,
  ExclamationCircleOutlined,
  FundOutlined,
  RiseOutlined,
  WarningOutlined,
} from '@ant-design/icons-vue'

const riskIndicators = [
  {
    key: 'systemic',
    label: '系统性风险评分',
    value: 72,
    max: 100,
    color: 'var(--color-danger)',
    icon: ExclamationCircleOutlined,
    trend: 'up',
    desc: '持续上升，需关注',
  },
  {
    key: 'liquidity',
    label: '流动性压力指数',
    value: 64,
    max: 100,
    color: 'var(--color-warning)',
    icon: FundOutlined,
    trend: 'stable',
    desc: '维持高位震荡',
  },
  {
    key: 'volatility',
    label: '波动率指标',
    value: 58,
    max: 100,
    color: 'var(--color-warning)',
    icon: RiseOutlined,
    trend: 'up',
    desc: '近期显著抬升',
  },
]

const alertSignals = [
  { industry: '房地产行业', signal: '信用利差走阔', severity: 'high', value: '+32bps', change: '+12%' },
  { industry: '房地产行业', signal: '融资渠道收缩', severity: 'high', value: '关键节点', change: '-8%' },
  { industry: '科技行业', signal: '估值中枢下移', severity: 'medium', value: '-15% PE', change: '-5%' },
  { industry: 'A股主板', signal: '波动率飙升', severity: 'high', value: 'VIX 28.5', change: '+42%' },
  { industry: '银行与保险', signal: '流动性折价', severity: 'medium', value: '+18bps', change: '+6%' },
  { industry: '新能源产业', signal: '行业分化加剧', severity: 'low', value: '局部调整', change: '+2%' },
]

const severityConfig = {
  high: { label: '高危', class: 'severity-high' },
  medium: { label: '中危', class: 'severity-medium' },
  low: { label: '低危', class: 'severity-low' },
}

const recommendedActions = [
  { action: '触发行业级压力测试', priority: '紧急', color: 'red' },
  { action: '提升保证金阈值至15%', priority: '高', color: 'orange' },
  { action: '增强房地产行业风险敞口监控', priority: '高', color: 'orange' },
  { action: '通知合规部门启动专项审查', priority: '中', color: 'blue' },
  { action: '向监管机构报备异常波动', priority: '中', color: 'blue' },
]

const industryHeatData = [
  { name: '房地产', risk: 82, color: '#ef4444' },
  { name: '银行保险', risk: 68, color: '#f59e0b' },
  { name: '科技', risk: 55, color: '#f59e0b' },
  { name: 'A股主板', risk: 71, color: '#ef4444' },
  { name: '新能源', risk: 38, color: '#10b981' },
]
</script>

<template>
  <section>
    <!-- Top Risk Gauges -->
    <div class="risk-gauge-grid">
      <div
        v-for="indicator in riskIndicators"
        :key="indicator.key"
        class="risk-gauge-card"
      >
        <div class="gauge-label">
          <component :is="indicator.icon" style="margin-right: 6px" />
          {{ indicator.label }}
        </div>
        <div class="gauge-value" :style="{ color: indicator.color }">
          {{ indicator.value }}
          <span style="font-size: 14px; font-weight: 400; color: var(--text-tertiary)">/{{ indicator.max }}</span>
        </div>
        <div style="margin-top: 4px; font-size: 12px; color: var(--text-tertiary)">
          {{ indicator.desc }}
          <span :style="{ color: indicator.trend === 'up' ? 'var(--color-danger)' : 'var(--color-success)' }">
            {{ indicator.trend === 'up' ? '↑' : '→' }}
          </span>
        </div>
      </div>
    </div>

    <!-- Two Column Layout -->
    <div class="full-width-module">
      <!-- Left: Alert Signals -->
      <a-card :bordered="false" class="glass-card">
        <template #title>
          <div class="card-title"><WarningOutlined /> 异常交易信号</div>
        </template>

        <div class="risk-alert-list">
          <div
            v-for="(alert, idx) in alertSignals"
            :key="idx"
            class="risk-alert-item"
          >
            <div>
              <strong style="font-size: 14px">{{ alert.signal }}</strong>
              <div style="font-size: 12px; color: var(--text-tertiary); margin-top: 2px">
                {{ alert.industry }}
              </div>
            </div>
            <div style="text-align: right">
              <div style="font-weight: 600; font-family: 'Inter', sans-serif">{{ alert.value }}</div>
              <div :style="{ fontSize: '12px', color: alert.change.startsWith('+') ? 'var(--color-danger)' : 'var(--color-success)' }">
                {{ alert.change }}
              </div>
            </div>
            <span :class="`alert-severity ${severityConfig[alert.severity].class}`">
              {{ severityConfig[alert.severity].label }}
            </span>
          </div>
        </div>
      </a-card>

      <!-- Right: Industry Heat + Actions -->
      <div>
        <!-- Industry Risk Heatmap -->
        <a-card :bordered="false" class="glass-card" style="margin-bottom: 16px">
          <template #title>
            <div class="card-title"><FundOutlined /> 行业风险热力</div>
          </template>
          <div style="display: flex; flex-direction: column; gap: 8px">
            <div
              v-for="item in industryHeatData"
              :key="item.name"
              style="display: flex; align-items: center; gap: 10px"
            >
              <span style="width: 60px; font-size: 13px; text-align: right; color: var(--text-secondary)">{{ item.name }}</span>
              <div style="flex: 1; height: 8px; background: var(--border-color-light); border-radius: 4px; overflow: hidden">
                <div
                  :style="{
                    width: item.risk + '%',
                    height: '100%',
                    borderRadius: '4px',
                    background: item.color,
                    transition: 'width 0.6s ease',
                  }"
                ></div>
              </div>
              <span style="width: 36px; font-size: 12px; font-weight: 600; color: item.color">{{ item.risk }}</span>
            </div>
          </div>
        </a-card>

        <!-- Recommended Actions -->
        <a-card :bordered="false" class="glass-card">
          <template #title>
            <div class="card-title"><AlertOutlined /> 建议处置措施</div>
          </template>
          <div style="display: flex; flex-direction: column; gap: 8px">
            <div
              v-for="(item, idx) in recommendedActions"
              :key="idx"
              style="display: flex; align-items: center; gap: 10px; padding: 8px 12px; border-radius: var(--border-radius); background: var(--bg-base); border: 1px solid var(--border-color)"
            >
              <span style="font-weight: 700; color: var(--primary-500); font-size: 14px">{{ idx + 1 }}</span>
              <span style="flex: 1; font-size: 14px">{{ item.action }}</span>
              <a-tag :color="item.color" style="font-size: 11px">{{ item.priority }}</a-tag>
            </div>
          </div>
        </a-card>
      </div>
    </div>
  </section>
</template>
