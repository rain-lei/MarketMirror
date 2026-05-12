<script setup>
import { SettingOutlined } from '@ant-design/icons-vue'
import { usePlatformStore } from '../composables/usePlatformStore'

const { API_BASE_URL } = usePlatformStore()
</script>

<template>
  <section class="single-module">
    <a-card :bordered="false" class="glass-card">
      <template #title>
        <div class="card-title"><SettingOutlined /> 平台配置</div>
      </template>
      <a-descriptions bordered :column="1">
        <a-descriptions-item label="前端框架">Vue 3 + Vite</a-descriptions-item>
        <a-descriptions-item label="组件库">Ant Design Vue</a-descriptions-item>
        <a-descriptions-item label="图表引擎">Apache ECharts</a-descriptions-item>
        <a-descriptions-item label="API Base URL">{{ API_BASE_URL }}</a-descriptions-item>
      </a-descriptions>
    </a-card>
  </section>
</template>
