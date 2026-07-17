<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  fetchFaults,
  fetchTrainingJob,
  generateFaults,
  startTraining,
} from '../services/trainingService'

const faultOptions = [
  { value: 'DEVICE_OFFLINE', label: '设备离线' },
  { value: 'VOLTAGE_ANOMALY', label: '电压异常' },
  { value: 'LINE_OVERLOAD', label: '线路过载' },
  { value: 'LINE_DISCONNECTED', label: '线路断开' },
]

const count = ref(120)
const seed = ref(Date.now())
const selectedTypes = ref(faultOptions.map((option) => option.value))
const samples = ref([])
const distribution = ref({})
const isGenerating = ref(false)
const isStartingTraining = ref(false)
const message = ref('')
const errorMessage = ref('')
const trainingJob = ref(null)
let pollTimer = null

const canTrain = computed(() => {
  const typeCount = new Set(samples.value.map((sample) => sample.faultType)).size
  return samples.value.length >= 8 && typeCount >= 2 && !isStartingTraining.value && !isTraining.value
})

const isTraining = computed(() => ['QUEUED', 'RUNNING'].includes(trainingJob.value?.status))
const metrics = computed(() => trainingJob.value?.result?.metrics)

onMounted(async () => {
  try {
    const existing = await fetchFaults()
    samples.value = Array.isArray(existing) ? existing : []
    distribution.value = summarize(samples.value)
  } catch {
    // 后端尚未启动时不阻塞页面首次展示。
  }
})

onBeforeUnmount(() => {
  stopPolling()
})

async function handleGenerate() {
  if (selectedTypes.value.length < 2) {
    errorMessage.value = '请至少选择两种故障类型，以便后续训练分类模型。'
    return
  }

  isGenerating.value = true
  errorMessage.value = ''
  message.value = ''
  trainingJob.value = null
  stopPolling()
  try {
    const result = await generateFaults({
      count: Number(count.value),
      seed: Number(seed.value),
      faultTypes: selectedTypes.value,
    })
    samples.value = result.samples || []
    distribution.value = result.distribution || summarize(samples.value)
    message.value = `已生成 ${result.count} 条可复现故障样本，批次 ${shortId(result.batchId)}`
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '故障生成失败'
  } finally {
    isGenerating.value = false
  }
}

async function handleTrain() {
  isStartingTraining.value = true
  errorMessage.value = ''
  message.value = ''
  try {
    trainingJob.value = await startTraining({
      datasetName: `grid-fault-${new Date().toISOString()}`,
      sampleIds: samples.value.map((sample) => sample.id),
    })
    message.value = '训练任务已提交，正在等待 Python 服务处理。'
    schedulePoll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '训练任务提交失败'
  } finally {
    isStartingTraining.value = false
  }
}

function schedulePoll() {
  stopPolling()
  pollTimer = window.setTimeout(pollTrainingJob, 900)
}

async function pollTrainingJob() {
  if (!trainingJob.value?.id) {
    return
  }
  try {
    trainingJob.value = await fetchTrainingJob(trainingJob.value.id)
    if (isTraining.value) {
      schedulePoll()
    } else if (trainingJob.value.status === 'SUCCEEDED') {
      message.value = `训练完成，模型 ${trainingJob.value.result.modelVersion} 已自动激活。`
    } else if (trainingJob.value.status === 'FAILED') {
      errorMessage.value = trainingJob.value.error || '训练失败'
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '训练状态查询失败'
    stopPolling()
  }
}

function stopPolling() {
  if (pollTimer !== null) {
    window.clearTimeout(pollTimer)
    pollTimer = null
  }
}

function summarize(values) {
  return values.reduce((result, sample) => {
    result[sample.faultType] = (result[sample.faultType] || 0) + 1
    return result
  }, {})
}

function faultLabel(type) {
  return faultOptions.find((option) => option.value === type)?.label || type
}

function shortId(value) {
  return String(value || '').replace('batch-', '').slice(0, 8)
}

function percent(value) {
  return `${((value || 0) * 100).toFixed(1)}%`
}
</script>

<template>
  <section class="workspace-section action-section training-panel" aria-labelledby="training-title">
    <div class="section-header training-heading">
      <div>
        <p class="section-eyebrow">Training</p>
        <h2 id="training-title">故障生成与训练</h2>
      </div>
      <span v-if="samples.length" class="training-count">{{ samples.length }} 条样本</span>
    </div>

    <div class="training-form">
      <div class="training-field-row">
        <label class="training-field">
          <span>生成数量</span>
          <input v-model.number="count" type="number" min="8" max="5000" step="4" />
        </label>
        <label class="training-field">
          <span>随机种子</span>
          <input v-model.number="seed" type="number" />
        </label>
      </div>

      <fieldset class="fault-type-fieldset">
        <legend>故障类型</legend>
        <label v-for="option in faultOptions" :key="option.value" class="fault-type-option">
          <input v-model="selectedTypes" type="checkbox" :value="option.value" />
          <span>{{ option.label }}</span>
        </label>
      </fieldset>

      <button class="secondary-action" type="button" :disabled="isGenerating || isTraining" @click="handleGenerate">
        {{ isGenerating ? '正在生成…' : '生成故障样本' }}
      </button>
    </div>

    <div v-if="samples.length" class="sample-summary">
      <div class="sample-distribution">
        <span v-for="(value, type) in distribution" :key="type">
          {{ faultLabel(type) }} {{ value }}
        </span>
      </div>
      <div class="sample-preview" aria-label="故障样本预览">
        <div v-for="sample in samples.slice(0, 3)" :key="sample.id" class="sample-preview-row">
          <span>{{ sample.faultName }}</span>
          <strong>{{ sample.targetName }}</strong>
          <small>严重度 {{ Math.round(sample.severity * 100) }}%</small>
        </div>
      </div>
    </div>

    <div v-if="trainingJob" class="training-progress-card">
      <div class="training-status-line">
        <span>训练状态</span>
        <strong>{{ trainingJob.status }}</strong>
      </div>
      <div class="training-progress-track" aria-label="训练进度">
        <span :style="{ width: `${trainingJob.progress || 0}%` }"></span>
      </div>
      <small>{{ trainingJob.progress || 0 }}% · {{ trainingJob.sampleCount }} 条样本</small>
    </div>

    <div v-if="metrics" class="metric-grid">
      <div><span>准确率</span><strong>{{ percent(metrics.accuracy) }}</strong></div>
      <div><span>宏平均 F1</span><strong>{{ percent(metrics.macroF1) }}</strong></div>
      <div><span>召回率</span><strong>{{ percent(metrics.macroRecall) }}</strong></div>
    </div>

    <p v-if="message" class="training-message" aria-live="polite">{{ message }}</p>
    <p v-if="errorMessage" class="training-message training-message--error" role="alert">{{ errorMessage }}</p>

    <button class="primary-action training-start" type="button" :disabled="!canTrain" @click="handleTrain">
      {{ isStartingTraining || isTraining ? '训练进行中…' : '开始训练' }}
    </button>
  </section>
</template>
