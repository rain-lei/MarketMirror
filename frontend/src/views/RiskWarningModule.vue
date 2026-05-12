<script setup>
import { AlertOutlined } from '@ant-design/icons-vue'
</script>

<template>
  <section class="single-module">
    <a-card :bordered="false" class="glass-card">
      <template #title>
        <div class="card-title"><AlertOutlined /> 风险预警中心</div>
      </template>
      <div class="kpi-row">
        <a-statistic title="系统性风险评分" :value="72" :value-style="{ color: '#cf1322' }" suffix="/100" />
        <a-statistic title="流动性压力" :value="64" suffix="/100" />
        <a-statistic title="异常交易信号" :value="18" suffix="条" />
      </div>
      <p class="result-note">当前风险等级偏高，建议触发行业级压力测试并提升保证金阈值。</p>
    </a-card>
  </section>
</template>
