<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { init, use } from 'echarts/core'

use([GridComponent, TooltipComponent, BarChart, CanvasRenderer])

const props = defineProps({
  agentActions: { type: Array, default: () => [] },
  theme: { type: String, default: 'light' },
})

const chartRef = ref(null)
let chart

const lightColors = {
  text: '#64748b',
  textDim: '#94a3b8',
  textPrimary: '#1e293b',
  gridLine: 'rgba(15, 23, 42, 0.06)',
  barConf: '#3b82f6',
  barConfGrad: ['#60a5fa', '#3b82f6'],
  barStrength: '#10b981',
  barStrengthGrad: ['#34d399', '#10b981'],
}

const darkColors = {
  text: '#94a3b8',
  textDim: '#64748b',
  textPrimary: '#f1f5f9',
  gridLine: 'rgba(241, 245, 249, 0.06)',
  barConf: '#60a5fa',
  barConfGrad: ['#93c5fd', '#60a5fa'],
  barStrength: '#34d399',
  barStrengthGrad: ['#6ee7b7', '#34d399'],
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
  const isDark = props.theme === 'dark' || props.theme === 'auto'
  const c = isDark ? darkColors : lightColors

  const labels = props.agentActions.map((item) => {
    const type = String(item.agent_type || '')
    if (type.includes('Aggressive') || type.includes('激进')) return '激进型(R)'
    if (type.includes('Conservative') || type.includes('保守')) return '保守型(C)'
    if (type.includes('Institutional') || type.includes('机构')) return '机构型(I)'
    return type
  })

  const confidence = props.agentActions.map((item) => Math.round(Number(item.confidence || 0) * 100))
  const behaviorScore = props.agentActions.map((item) => scoreAction(item.action_type))

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      backgroundColor: isDark ? '#1e293b' : '#ffffff',
      borderColor: isDark ? '#334155' : '#e2e8f0',
      textStyle: {
        color: isDark ? '#f1f5f9' : '#1e293b',
        fontSize: 13,
        fontFamily: 'Inter, Noto Sans SC, sans-serif',
      },
    },
    legend: {
      data: ['置信度(%)', '策略强度'],
      bottom: 0,
      textStyle: { color: c.textDim, fontSize: 11 },
      itemWidth: 12,
      itemHeight: 8,
    },
    grid: { left: 10, right: 10, top: 12, bottom: 32, containLabel: true },
    xAxis: {
      type: 'category',
      data: labels,
      axisLabel: {
        color: c.textPrimary,
        fontSize: 12,
        fontWeight: 500,
      },
      axisLine: { lineStyle: { color: c.gridLine } },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      max: 100,
      axisLabel: { color: c.textDim, fontSize: 11 },
      splitLine: { lineStyle: { color: c.gridLine, type: 'dashed' } },
    },
    series: [
      {
        name: '置信度(%)',
        type: 'bar',
        barMaxWidth: 32,
        barGap: '35%',
        data: confidence,
        itemStyle: {
          borderRadius: [6, 6, 0, 0],
          color: {
            type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: c.barConfGrad[0] },
              { offset: 1, color: c.barConfGrad[1] },
            ],
          },
        },
        label: {
          show: true,
          position: 'top',
          color: c.text,
          fontSize: 10,
          fontFamily: 'Inter, sans-serif',
          fontWeight: 600,
        },
        emphasis: {
          itemStyle: { shadowBlur: 8, shadowColor: c.barConf },
        },
      },
      {
        name: '策略强度',
        type: 'bar',
        barMaxWidth: 32,
        data: behaviorScore,
        itemStyle: {
          borderRadius: [6, 6, 0, 0],
          color: {
            type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: c.barStrengthGrad[0] },
              { offset: 1, color: c.barStrengthGrad[1] },
            ],
          },
        },
        label: {
          show: true,
          position: 'top',
          color: c.text,
          fontSize: 10,
          fontFamily: 'Inter, sans-serif',
          fontWeight: 600,
        },
        emphasis: {
          itemStyle: { shadowBlur: 8, shadowColor: c.barStrength },
        },
      },
    ],
  }
}

async function renderChart() {
  await nextTick()
  if (!chartRef.value) return
  if (!chart) {
    chart = init(chartRef.value)
  }
  chart.setOption(buildOption(), { notMerge: true })
}

onMounted(() => {
  renderChart()
  window.addEventListener('resize', renderChart)
})

watch(
  () => [props.agentActions, props.theme],
  () => renderChart(),
  { deep: true },
)

onBeforeUnmount(() => {
  window.removeEventListener('resize', renderChart)
  if (chart) {
    chart.dispose()
    chart = null
  }
})
</script>

<template>
  <div class="agent-matrix-chart" ref="chartRef"></div>
</template>

<style scoped>
.agent-matrix-chart {
  height: 280px;
  width: 100%;
}
</style>
