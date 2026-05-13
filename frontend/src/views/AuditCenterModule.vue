<script setup>
import { computed, ref } from 'vue'
import {
  AuditOutlined,
  ClearOutlined,
  ClockCircleOutlined,
  FileTextOutlined,
  SearchOutlined,
  WarningOutlined,
} from '@ant-design/icons-vue'
import { usePlatformStore } from '../composables/usePlatformStore'

const { logs, formatTime } = usePlatformStore()

const searchQuery = ref('')
const logTypeFilter = ref('')
const expanded = ref(true)

const filteredLogs = computed(() => {
  let result = logs
  if (searchQuery.value) {
    const q = searchQuery.value.toLowerCase()
    result = result.filter(
      (l) =>
        l.text.toLowerCase().includes(q) ||
        l.type.toLowerCase().includes(q)
    )
  }
  if (logTypeFilter.value) {
    result = result.filter((l) => l.type === logTypeFilter.value)
  }
  return result
})

const logTypes = computed(() => {
  const types = new Set(logs.map((l) => l.type))
  return Array.from(types)
})

const stats = computed(() => {
  const total = logs.length
  const errorCount = logs.filter((l) => l.type === '异常').length
  const warningCount = logs.filter((l) => l.type === '演示').length
  const infoCount = total - errorCount - warningCount
  return { total, errorCount, warningCount, infoCount }
})

function clearFilters() {
  searchQuery.value = ''
  logTypeFilter.value = ''
}

function getLogIcon(type) {
  if (type === '异常') return WarningOutlined
  if (type === '演示') return ClockCircleOutlined
  return FileTextOutlined
}

function getLogColor(type) {
  if (type === '异常') return 'var(--color-danger)'
  if (type === '演示') return 'var(--color-warning)'
  if (type === '推演') return 'var(--color-success)'
  return 'var(--color-info)'
}

function getLogBg(type) {
  if (type === '异常') return 'var(--color-danger-bg)'
  if (type === '演示') return 'var(--color-warning-bg)'
  if (type === '推演') return 'var(--color-success-bg)'
  return 'var(--color-info-bg)'
}
</script>

<template>
  <section class="single-module">
    <!-- Stats Row -->
    <div class="audit-stats">
      <div class="audit-stat-card">
        <div class="stat-value">{{ stats.total }}</div>
        <div class="stat-label">总事件数</div>
      </div>
      <div class="audit-stat-card">
        <div class="stat-value" style="color: var(--color-success)">{{ stats.infoCount }}</div>
        <div class="stat-label">正常操作</div>
      </div>
      <div class="audit-stat-card">
        <div class="stat-value" style="color: var(--color-warning)">{{ stats.warningCount }}</div>
        <div class="stat-label">推演演示</div>
      </div>
      <div class="audit-stat-card">
        <div class="stat-value" style="color: var(--color-danger)">{{ stats.errorCount }}</div>
        <div class="stat-label">异常事件</div>
      </div>
    </div>

    <a-card :bordered="false" class="glass-card">
      <template #title>
        <div class="card-title"><AuditOutlined /> 审计日志</div>
      </template>

      <!-- Toolbar -->
      <div class="audit-toolbar">
        <a-input
          v-model:value="searchQuery"
          placeholder="搜索日志..."
          style="width: 200px"
          allow-clear
          size="small"
        >
          <template #prefix><SearchOutlined /></template>
        </a-input>
        <a-select
          v-model:value="logTypeFilter"
          placeholder="按类型筛选"
          style="width: 130px"
          size="small"
          allow-clear
          :options="logTypes.map((t) => ({ label: t, value: t }))"
        />
        <a-button size="small" @click="clearFilters">
          <template #icon><ClearOutlined /></template>
          清除筛选
        </a-button>
        <span style="margin-left: auto; font-size: 12px; color: var(--text-tertiary)">
          共 {{ filteredLogs.length }} 条记录
        </span>
      </div>

      <!-- Log Timeline -->
      <a-timeline v-if="filteredLogs.length" style="margin-top: 8px">
        <a-timeline-item
          v-for="(log, idx) in filteredLogs"
          :key="idx"
          :color="log.type === '异常' ? 'red' : log.type === '演示' ? 'orange' : log.type === '推演' ? 'green' : 'blue'"
        >
          <template #dot>
            <span
              :style="{
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: '24px',
                height: '24px',
                borderRadius: '50%',
                background: getLogBg(log.type),
              }"
            >
              <component
                :is="getLogIcon(log.type)"
                :style="{ fontSize: '12px', color: getLogColor(log.type) }"
              />
            </span>
          </template>
          <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap">
            <a-tag :color="log.type === '异常' ? 'red' : log.type === '演示' ? 'orange' : log.type === '推演' ? 'green' : 'blue'" style="font-size: 11px">
              {{ log.type }}
            </a-tag>
            <span style="font-size: 14px">{{ log.text }}</span>
          </div>
          <div class="result-time" style="margin-top: 4px">{{ formatTime(log.time) }}</div>
        </a-timeline-item>
      </a-timeline>

      <a-empty v-else description="暂无审计日志" style="padding: 32px 0">
        <template #image>
          <AuditOutlined style="font-size: 48px; color: var(--text-tertiary)" />
        </template>
      </a-empty>
    </a-card>
  </section>
</template>
