<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { init, use } from 'echarts/core'

use([GridComponent, TooltipComponent, BarChart, CanvasRenderer])

const props = defineProps({
  agentActions: { type: Array, default: () => [] },
  theme: { type: String, default: 'light' }
})

const chartRef = ref(null)
let chart

const lightColors = {
  text: '#546683',
  textDim: '#8496b0',
  gridLine: 'rgba(16,24,40,0.08)',
  barConf: '#1a6bff',
  barStrength: '#079455'
}

const darkColors = {
  text: '#8b949e',
  textDim: '#6e7681',
  gridLine: 'rgba(240,246,252,0.08)',
  barConf: '#58a6ff',
  barStrength: '#3fb950'
}

function scoreAction(actionText) {
  const text = String(actionText || '')
  if (text.includes('Panic') || text.includes('抛售')) return 86
  if (text.includes('Leverage') || text.includes('杠杆')) return 78
  if (text.includes('Short') || text.includes('做空')) return 74
  if (text.includes('Buy') || text.includes('抄底')) return 66
  if (text.includes('Hedge') || text.includes('对冲')) return 52
  if (text.includes('Sector') || text.includes('轮动')) return 40
  if (text.includes('Wait') || text.includes('观望')) return 34
  return 48
}

function buildOption() {
  const isDark = props.theme === 'dark'
  const c = isDark ? darkColors : lightColors

  const labels = props.agentActions.map(item => {
    const type = String(item.agent_type || '')
    if (type.includes('Aggressive') || type.includes('激进')) return '激进型'
    if (type.includes('Conservative') || type.includes('保守')) return '保守型'
    if (type.includes('Institutional') || type.includes('机构')) return '机构型'
    return type
  })
  const confidence = props.agentActions.map(item => Math.round(Number(item.confidence || 0) * 100))
  const behaviorScore = props.agentActions.map(item => scoreAction(item.action_type))

  return {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 14, right: 14, top: 10, bottom: 18, containLabel: true },
    xAxis: {
      type: 'category',
      data: labels,
      axisLabel: { color: c.text, fontSize: 11 },
      axisLine: { lineStyle: { color: c.gridLine } },
      axisTick: { show: false }
    },
    yAxis: {
      type: 'value',
      max: 100,
      axisLabel: { color: c.textDim, fontSize: 11 },
      splitLine: { lineStyle: { color: c.gridLine } }
    },
    series: [
      {
        name: '置信度(%)',
        type: 'bar',
        barMaxWidth: 36,
        barGap: '30%',
        data: confidence,
        itemStyle: {
          color: c.barConf,
          borderRadius: [6, 6, 0, 0]
        }
      },
      {
        name: '策略强度',
        type: 'bar',
        barMaxWidth: 36,
        data: behaviorScore,
        itemStyle: {
          color: c.barStrength,
          borderRadius: [6, 6, 0, 0]
        }
      }
    ]
  }
}

async function renderChart() {
  await nextTick()
  if (!chartRef.value) return
  if (!chart) chart = init(chartRef.value)
  chart.setOption(buildOption(), true)
}

onMounted(() => {
  renderChart()
  window.addEventListener('resize', renderChart)
})

watch(() => [props.agentActions, props.theme], () => renderChart(), { deep: true })

onBeforeUnmount(() => {
  window.removeEventListener('resize', renderChart)
  if (chart) { chart.dispose(); chart = null }
})
</script>

<template>
  <div class="agent-matrix-chart" ref="chartRef"></div>
</template>

<style scoped>
.agent-matrix-chart {
  height: 260px;
  width: 100%;
}
</style>
