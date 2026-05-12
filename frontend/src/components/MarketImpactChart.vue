<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { init, use } from 'echarts/core'

use([TooltipComponent, GridComponent, LineChart, CanvasRenderer])

const props = defineProps({
  impact: { type: Number, default: 0 },
  scenarioTag: { type: String, default: '' },
  theme: { type: String, default: 'light' }
})

const elRef = ref(null)
let chart

const lightColors = {
  text: '#546683',
  textDim: '#8496b0',
  gridLine: 'rgba(16, 24, 40, 0.08)',
  up: '#079455',
  upFill: ['rgba(7,148,85,0.28)', 'rgba(7,148,85,0.02)'],
  down: '#d92d20',
  downFill: ['rgba(217,45,32,0.28)', 'rgba(217,45,32,0.02)']
}

const darkColors = {
  text: '#8b949e',
  textDim: '#6e7681',
  gridLine: 'rgba(240,246,252,0.08)',
  up: '#3fb950',
  upFill: ['rgba(63,185,80,0.3)', 'rgba(63,185,80,0.02)'],
  down: '#f85149',
  downFill: ['rgba(248,81,73,0.3)', 'rgba(248,81,73,0.02)']
}

function buildOption() {
  const isDark = props.theme === 'dark'
  const c = isDark ? darkColors : lightColors
  const base = Number(props.impact || 0)

  // Generate a smooth propagation curve from the impact base value
  const stages = 10
  const points = Array.from({ length: stages }, (_, i) => {
    const phase = i / (stages - 1)
    const amplitude = 0.15
    const wave = Math.sin(phase * Math.PI * 1.6) * amplitude
    const decay = 1 - phase * 0.2
    return Number((base + wave * decay).toFixed(3))
  })

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      formatter: p => `T+${p[0].axisValue} 冲击值 <b>${p[0].value}</b>`
    },
    grid: {
      left: 16,
      right: 12,
      top: 12,
      bottom: 20,
      containLabel: true
    },
    xAxis: {
      type: 'category',
      data: points.map((_, i) => `${i + 1}`),
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: {
        color: c.textDim,
        fontSize: 11,
        formatter: v => `T+${v}`
      }
    },
    yAxis: {
      type: 'value',
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { lineStyle: { color: c.gridLine } },
      axisLabel: { color: c.textDim, fontSize: 11 }
    },
    series: [{
      data: points,
      type: 'line',
      smooth: true,
      symbol: 'circle',
      symbolSize: 6,
      lineStyle: { width: 2.5, color: base < 0 ? c.down : c.up },
      itemStyle: { color: base < 0 ? c.down : c.up },
      areaStyle: {
        color: {
          type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: base < 0 ? c.downFill[0] : c.upFill[0] },
            { offset: 1, color: base < 0 ? c.downFill[1] : c.upFill[1] }
          ]
        }
      }
    }]
  }
}

async function renderChart() {
  await nextTick()
  if (!elRef.value) return
  if (!chart) chart = init(elRef.value)
  chart.setOption(buildOption(), true)
}

onMounted(() => {
  renderChart()
  window.addEventListener('resize', renderChart)
})

watch(() => [props.impact, props.scenarioTag, props.theme], renderChart)

onBeforeUnmount(() => {
  window.removeEventListener('resize', renderChart)
  if (chart) { chart.dispose(); chart = null }
})
</script>

<template>
  <div class="impact-chart" ref="elRef"></div>
</template>

<style scoped>
.impact-chart {
  width: 100%;
  height: 220px;
}
</style>
