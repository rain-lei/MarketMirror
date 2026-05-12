<script setup>
import { AuditOutlined } from '@ant-design/icons-vue'
import { usePlatformStore } from '../composables/usePlatformStore'

const { logs, formatTime } = usePlatformStore()
</script>

<template>
  <section class="single-module">
    <a-card :bordered="false" class="glass-card">
      <template #title>
        <div class="card-title"><AuditOutlined /> 审计中心</div>
      </template>
      <a-timeline v-if="logs.length">
        <a-timeline-item v-for="(log, idx) in logs" :key="idx">
          <strong>[{{ log.type }}]</strong> {{ log.text }}
          <div class="result-time">{{ formatTime(log.time) }}</div>
        </a-timeline-item>
      </a-timeline>
      <a-empty v-else description="暂无审计日志" />
    </a-card>
  </section>
</template>
