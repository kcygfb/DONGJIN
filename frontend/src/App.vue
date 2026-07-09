<script setup>
import DiagnosisSection from './components/DiagnosisSection.vue'
import TopologySection from './components/TopologySection.vue'
import TrainingSection from './components/TrainingSection.vue'
</script>

<template>
  <main class="app-shell">
    <header class="page-header">
      <p class="page-eyebrow">Grid Analysis</p>
      <h1>电网分析工作台</h1>
    </header>

    <section class="dashboard-grid" aria-label="页面功能区块">
      <TopologySection />
      <TrainingSection />
      <DiagnosisSection />
    </section>
  </main>
</template>
