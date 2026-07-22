<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  fetchActiveModel,
  fetchFaults,
  fetchTrainingJob,
  generateFaults,
  resetTraining,
  startTraining,
} from '../services/trainingService'

const faultOptions = [
  { value: 'DEVICE_OFFLINE', label: '设备离线' },
  { value: 'VOLTAGE_ANOMALY', label: '电压异常' },
  { value: 'LINE_OVERLOAD', label: '线路过载' },
  { value: 'LINE_DISCONNECTED', label: '线路断开' },
]

const count = ref(500)
const seed = ref(Date.now())
const selectedTypes = ref(faultOptions.map((option) => option.value))
const samples = ref([])
const distribution = ref({})
const isGenerating = ref(false)
const isStartingTraining = ref(false)
const isResetting = ref(false)
const message = ref('')
const errorMessage = ref('')
const trainingJob = ref(null)
const activeModel = ref(null)
let pollTimer = null

const canTrain = computed(() => {
  const typeCount = new Set(samples.value.map((sample) => sample.faultType)).size
  return samples.value.length >= 8
    && typeCount >= 2
    && !isStartingTraining.value
    && !isTraining.value
    && !isResetting.value
})

const isTraining = computed(() => ['QUEUED', 'RUNNING'].includes(trainingJob.value?.status))
const trainingResult = computed(() => trainingJob.value?.result || activeModel.value)
const trainingSummary = computed(() => ({
  trainingCount: trainingResult.value?.trainingSampleCount,
  testCount: trainingResult.value?.evaluationSampleCount,
  accuracy: trainingResult.value?.metrics?.accuracy,
  locationAccuracy: trainingResult.value?.metrics?.locationAccuracy,
  primaryModelName: trainingResult.value?.primaryModelName,
}))

onMounted(async () => {
  const [faultsResult, modelResult] = await Promise.allSettled([
    fetchFaults(),
    fetchActiveModel(),
  ])

  if (faultsResult.status === 'fulfilled') {
    const existing = faultsResult.value
    samples.value = Array.isArray(existing) ? existing : []
    distribution.value = summarize(samples.value)
  }

  if (modelResult.status === 'fulfilled') {
    activeModel.value = modelResult.value
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

async function handleReset() {
  const confirmed = window.confirm('重置后将删除当前模型、历史模型文件、故障样本和训练记录，是否继续？')
  if (!confirmed) {
    return
  }

  isResetting.value = true
  errorMessage.value = ''
  message.value = ''
  stopPolling()
  try {
    const result = await resetTraining()
    samples.value = []
    distribution.value = {}
    trainingJob.value = null
    activeModel.value = null
    seed.value = Date.now()
    const deletedCount = result?.pythonService?.deletedModelCount || 0
    message.value = `训练环境已重置，已删除 ${deletedCount} 个模型。现在可以重新生成样本并训练。`
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '训练重置失败'
  } finally {
    isResetting.value = false
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
      if (trainingJob.value.progress >= 35) {
        message.value = 'Python 正在执行 GNN 图训练，标准电网通常约需 1 分钟；完成后进度会直接跳到 100%。'
      }
      schedulePoll()
    } else if (trainingJob.value.status === 'SUCCEEDED') {
      activeModel.value = trainingJob.value.result
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
  if (type === 'NORMAL') {
    return '正常运行'
  }
  return faultOptions.find((option) => option.value === type)?.label || type
}

function shortId(value) {
  return String(value || '').replace('batch-', '').slice(0, 8)
}

function percent(value) {
  return Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : '--'
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

      <button
        class="secondary-action"
        type="button"
        :disabled="isGenerating || isTraining || isStartingTraining || isResetting"
        @click="handleGenerate"
      >
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
      <small v-if="trainingJob.progress >= 35 && trainingJob.progress < 100">
        Python GNN 训练中 · {{ trainingJob.sampleCount }} 张完整拓扑图 · 完成后跳至 100%
      </small>
      <small v-else>{{ trainingJob.progress || 0 }}% · {{ trainingJob.sampleCount }} 张完整拓扑图</small>
    </div>

    <div class="training-result-summary" aria-label="最近一次训练结果">
      <p>训练结果 · 主模型 {{ trainingSummary.primaryModelName || 'GNN' }}</p>
      <div class="metric-grid">
        <div>
          <span>训练量</span>
          <strong>{{ trainingSummary.trainingCount ?? '--' }}</strong>
          <small>条样本</small>
        </div>
        <div>
          <span>测试集</span>
          <strong>{{ trainingSummary.testCount ?? '--' }}</strong>
          <small>条样本</small>
        </div>
        <div>
          <span>GNN最终准确率</span>
          <strong>{{ percent(trainingSummary.accuracy) }}</strong>
          <small>位置与类型同时正确</small>
        </div>
      </div>
      <div class="model-metric-detail">
        <div>
          <span>GNN定位准确率</span>
          <strong>{{ percent(trainingSummary.locationAccuracy) }}</strong>
        </div>
      </div>
    </div>

    <p v-if="message" class="training-message" aria-live="polite">{{ message }}</p>
    <p v-if="errorMessage" class="training-message training-message--error" role="alert">{{ errorMessage }}</p>

    <div class="training-action-row">
      <button class="primary-action training-start" type="button" :disabled="!canTrain" @click="handleTrain">
        {{ isStartingTraining || isTraining ? '训练进行中…' : '开始训练' }}
      </button>
      <button
        class="secondary-action training-reset"
        type="button"
        :disabled="isTraining || isGenerating || isStartingTraining || isResetting"
        @click="handleReset"
      >
        {{ isResetting ? '正在重置…' : '重置训练' }}
      </button>
    </div>
  </section>
</template>
