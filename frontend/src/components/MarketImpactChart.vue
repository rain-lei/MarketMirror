<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, MarkLineComponent } from 'echarts/components'
import { init, use } from 'echarts/core'

use([TooltipComponent, GridComponent, MarkLineComponent, LineChart, CanvasRenderer])

const props = defineProps({
  impact: { type: Number, default: 0 },
  scenarioTag: { type: String, default: '' },
  theme: { type: String, default: 'light' },
})

const elRef = ref(null)
let chart

const lightColors = {
  text: '#64748b',
  textDim: '#94a3b8',
  textPrimary: '#1e293b',
  gridLine: 'rgba(15, 23, 42, 0.06)',
  up: '#10b981',
  upFill: ['rgba(16, 185, 129, 0.25)', 'rgba(16, 185, 129, 0.0)'],
  down: '#ef4444',
  downFill: ['rgba(239, 68, 68, 0.25)', 'rgba(239, 68, 68, 0.0)'],
}

const darkColors = {
  text: '#94a3b8',
  textDim: '#64748b',
  textPrimary: '#f1f5f9',
  gridLine: 'rgba(241, 245, 249, 0.06)',
  up: '#34d399',
  upFill: ['rgba(52, 211, 153, 0.3)', 'rgba(52, 211, 153, 0.0)'],
  down: '#f87171',
  downFill: ['rgba(248, 113, 113, 0.3)', 'rgba(248, 113, 113, 0.0)'],
}

function buildOption() {
  const isDark = props.theme === 'dark' || props.theme === 'auto'
  const c = isDark ? darkColors : lightColors
  const base = Number(props.impact || 0)

  // Smooth propagation curve
  const stages = 12
  const points = Array.from({ length: stages }, (_, i) => {
    const phase = i / (stages - 1)
    const amplitude = 0.12
    const wave = Math.sin(phase * Math.PI * 1.8) * amplitude
    const decay = 1 - phase * 0.25
    return Number((base + wave * decay).toFixed(3))
  })

  const lineColor = base < 0 ? c.down : c.up
  const areaFill = base < 0 ? c.downFill : c.upFill

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: isDark ? '#1e293b' : '#ffffff',
      borderColor: isDark ? '#334155' : '#e2e8f0',
      textStyle: {
        color: isDark ? '#f1f5f9' : '#1e293b',
        fontSize: 13,
        fontFamily: 'Inter, Noto Sans SC, sans-serif',
      },
      formatter: (p) => {
        const vals = Array.isArray(p) ? p : [p]
        return vals.map((v) => `
          <div style="display:flex;align-items:center;gap:8px">
            <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${lineColor}"></span>
            <span>T+${v.axisValue}</span>
            <b>${v.value}</b>
          </div>
        `).join('')
      },
    },
    grid: {
      left: 8,
      right: 16,
      top: 16,
      bottom: 24,
      containLabel: true,
    },
    xAxis: {
      type: 'category',
      data: points.map((_, i) => `${i}`),
      boundaryGap: false,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: {
        color: c.textDim,
        fontSize: 11,
        fontFamily: 'Inter, sans-serif',
        formatter: (v) => `T+${v}`,
      },
    },
    yAxis: {
      type: 'value',
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { lineStyle: { color: c.gridLine, type: 'dashed' } },
      axisLabel: {
        color: c.textDim,
        fontSize: 11,
        fontFamily: 'Inter, sans-serif',
      },
    },
    series: [
      {
        data: points,
        type: 'line',
        smooth: 0.4,
        symbol: 'circle',
        symbolSize: 5,
        showSymbol: false,
        emphasis: {
          focus: 'series',
          symbolSize: 8,
        },
        lineStyle: {
          width: 3,
          color: lineColor,
          shadowBlur: 8,
          shadowColor: lineColor,
          shadowOffsetY: 2,
        },
        itemStyle: {
          color: lineColor,
          borderColor: isDark ? '#1e293b' : '#fff',
          borderWidth: 2,
        },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: areaFill[0] },
              { offset: 1, color: areaFill[1] },
            ],
          },
        },
        markLine: {
          silent: true,
          symbol: 'none',
          lineStyle: {
            color: c.textDim,
            type: 'dashed',
            width: 1,
          },
          label: {
            color: c.text,
            fontSize: 11,
            fontFamily: 'Inter, sans-serif',
            formatter: '基线 {c}',
          },
          data: [{ yAxis: 0 }],
        },
      },
    ],
  }
}

async function renderChart() {
  await nextTick()
  if (!elRef.value) return
  if (!chart) {
    chart = init(elRef.value)
  }
  chart.setOption(buildOption(), { notMerge: true })
}

onMounted(() => {
  renderChart()
  window.addEventListener('resize', renderChart)
})

watch(
  () => [props.impact, props.scenarioTag, props.theme],
  () => renderChart(),
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
  <div class="impact-chart" ref="elRef"></div>
</template>

<style scoped>
.impact-chart {
  width: 100%;
  height: 240px;
}
</style>
