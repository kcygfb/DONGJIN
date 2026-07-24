<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  closeShadowSession,
  createShadowSession,
  diagnoseCurrentSnapshot,
  diagnoseShadowSession,
  fetchDiagnosisMonitor,
  fetchInferenceModels,
  revealShadowSession,
  rollbackInferenceModel,
  runShortCircuitAnalysis,
  selectInferenceModel,
  startDiagnosisMonitor,
  stopDiagnosisMonitor,
} from '../services/diagnosisService'

const emit = defineEmits(['diagnosed'])
const models = ref([])
const selectedModel = ref(null)
const chosenModelId = ref('')
const diagnosis = ref(null)
const shadow = ref(null)
const comparison = ref(null)
const shortCircuit = ref(null)
const shadowEventType = ref('RANDOM')
const shadowTargetId = ref('')
const shortCircuitTargetId = ref('')
const shortCircuitType = ref('3ph')
const shortCircuitPowerMva = ref('')
const shortCircuitRx = ref('')
const busy = ref('')
const errorMessage = ref('')
const message = ref('')
const monitor = ref({ state: 'STOPPED' })

const shadowEvents = [
  'RANDOM',
  'LINE_OUTAGE',
  'TRANSFORMER_OUTAGE',
  'SWITCH_MISOPERATION',
  'LOAD_SURGE',
  'GENERATION_DROP',
  'MEASUREMENT_BIAS',
  'MEASUREMENT_DROPOUT',
  'MEASUREMENT_FROZEN',
  'MEASUREMENT_DRIFT',
  'MEASUREMENT_DELAY',
  'MEASUREMENT_QUANTIZATION',
  'TAP_POSITION_ANOMALY',
]

const modelState = computed(() => {
  if (!selectedModel.value) return '尚未人工选择在线模型'
  return `${selectedModel.value.modelId} · ${selectedModel.value.selectedBy || 'manual'}`
})

onMounted(async () => {
  await loadModels()
  try {
    monitor.value = await fetchDiagnosisMonitor()
  } catch {
    // 主错误区由用户执行操作时再显示。
  }
})

async function run(action, callback) {
  busy.value = action
  errorMessage.value = ''
  message.value = ''
  try {
    return await callback()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
    return null
  } finally {
    busy.value = ''
  }
}

async function loadModels() {
  await run('models', async () => {
    const result = await fetchInferenceModels()
    models.value = result.models || []
    selectedModel.value = result.selectedModel
    chosenModelId.value = selectedModel.value?.modelId || models.value[0]?.modelId || ''
  })
}

async function handleSelectModel() {
  if (!chosenModelId.value) return
  if (!window.confirm(`确认将 ${chosenModelId.value} 设为在线推理模型？该操作不会启动训练或研判。`)) return
  await run('select', async () => {
    selectedModel.value = await selectInferenceModel(chosenModelId.value)
    message.value = '模型兼容检查通过，人工选择记录已保存。'
    await loadModels()
  })
}

async function handleRollback() {
  if (!window.confirm('确认回滚到上一次人工模型选择？')) return
  await run('rollback', async () => {
    selectedModel.value = await rollbackInferenceModel()
    message.value = `已人工回滚到 ${selectedModel.value.modelId}`
    await loadModels()
  })
}

async function handleCurrentDiagnosis() {
  await run('current', async () => {
    diagnosis.value = await diagnoseCurrentSnapshot()
    comparison.value = null
    emitHighlight(diagnosis.value)
  })
}

async function handleMonitor(action) {
  await run(`monitor-${action}`, async () => {
    monitor.value = action === 'start'
      ? await startDiagnosisMonitor()
      : await stopDiagnosisMonitor()
    message.value = action === 'start'
      ? '周期研判已由你显式启动；页面刷新本身不会触发研判。'
      : '周期研判已停止。'
  })
}

async function handleCreateShadow() {
  if (!selectedModel.value) {
    errorMessage.value = '请先在独立模型管理区人工选择在线模型。'
    return
  }
  await run('shadow-create', async () => {
    if (shadow.value?.sessionId) await closeShadowSession(shadow.value.sessionId)
    shadow.value = await createShadowSession({
      eventType: shadowEventType.value === 'RANDOM' ? null : shadowEventType.value,
      targetBusinessId: shadowTargetId.value || null,
    })
    diagnosis.value = null
    comparison.value = null
    message.value = `影子会话 ${shadow.value.sessionId} 已隔离生成；真值尚未揭示。`
  })
}

async function handleShadowDiagnosis() {
  if (!shadow.value) return
  await run('shadow-diagnose', async () => {
    diagnosis.value = await diagnoseShadowSession(shadow.value.sessionId)
    emitHighlight(diagnosis.value)
  })
}

async function handleReveal() {
  if (!shadow.value || !diagnosis.value) return
  await run('shadow-reveal', async () => {
    comparison.value = await revealShadowSession(shadow.value.sessionId)
  })
}

async function handleShortCircuit() {
  if (!shortCircuitTargetId.value) {
    errorMessage.value = '请输入P1母线businessId。'
    return
  }
  if (!Number(shortCircuitPowerMva.value) || !Number(shortCircuitRx.value)) {
    errorMessage.value = 'SimBench P1不含外部电网短路参数，请输入已知的短路容量和R/X。'
    return
  }
  await run('short-circuit', async () => {
    shortCircuit.value = await runShortCircuitAnalysis({
      targetBusinessId: shortCircuitTargetId.value,
      faultType: shortCircuitType.value,
      case: 'max',
      sScMva: Number(shortCircuitPowerMva.value),
      rx: Number(shortCircuitRx.value),
    })
  })
}

function emitHighlight(result) {
  if (!result?.targetBusinessId) {
    emit('diagnosed', null)
    return
  }
  emit('diagnosed', {
    target: { kind: 'NODE', id: result.targetBusinessId },
    trace: {
      upstream: (result.neo4jTrace?.upstream || result.upstreamTrace || []).map((item) => ({
        nodeId: item.businessId,
        depth: item.depth,
      })),
      downstream: (result.neo4jTrace?.downstream || result.downstreamTrace || []).map((item) => ({
        nodeId: item.businessId,
        depth: item.depth,
      })),
      nodeIds: [
        result.targetBusinessId,
        ...(result.neighborTrace || []).map((item) => item.businessId),
      ],
      edgeIds: [],
    },
  })
}

function percent(value) {
  return Number.isFinite(value) ? `${(value * 100).toFixed(2)}%` : '--'
}
</script>

<template>
  <section class="workspace-section diagnosis-section" aria-labelledby="diagnosis-title">
    <div class="section-header diagnosis-heading">
      <div>
        <p class="section-eyebrow">White-box Diagnosis</p>
        <h2 id="diagnosis-title">在线错误研判与影子盲测</h2>
      </div>
      <span class="diagnosis-model-state">{{ modelState }}</span>
    </div>

    <p v-if="errorMessage" class="diagnosis-error" role="alert">{{ errorMessage }}</p>
    <p v-if="message" class="topology-generation-message">{{ message }}</p>

    <section class="ground-truth-card">
      <div>
        <span>独立模型管理</span>
        <strong>人工选择，不自动替换</strong>
      </div>
      <select v-model="chosenModelId" :disabled="busy">
        <option v-for="item in models" :key="item.modelId" :value="item.modelId">
          {{ item.modelId }} · {{ item.compatibility?.compatible ? '兼容' : '不兼容' }}
        </option>
      </select>
      <button class="secondary-action" :disabled="busy || !chosenModelId" @click="handleSelectModel">
        兼容检查并选择
      </button>
      <button class="secondary-action" :disabled="busy" @click="handleRollback">
        人工回滚
      </button>
    </section>

    <button class="primary-action blind-start" :disabled="busy || !selectedModel" @click="handleCurrentDiagnosis">
      {{ busy === 'current' ? '正在读取当前完整快照…' : '研判正式Redis当前快照' }}
    </button>
    <div class="topology-toolbar-actions">
      <button
        v-if="monitor.state !== 'RUNNING'"
        class="secondary-action"
        :disabled="busy || !selectedModel"
        @click="handleMonitor('start')"
      >
        手动启动周期研判
      </button>
      <button
        v-else
        class="secondary-action"
        :disabled="busy"
        @click="handleMonitor('stop')"
      >
        停止周期研判
      </button>
      <small>{{ monitor.state }} · 已研判 {{ monitor.runCount || 0 }} 帧</small>
    </div>

    <section class="ground-truth-card">
      <div>
        <span>隔离影子会话</span>
        <strong>pandapower物理/量测错误</strong>
        <small>不会修改正式Redis活动快照或Neo4j拓扑</small>
      </div>
      <select v-model="shadowEventType" :disabled="busy">
        <option v-for="event in shadowEvents" :key="event" :value="event">{{ event }}</option>
      </select>
      <input v-model.trim="shadowTargetId" placeholder="可选：指定设备businessId" :disabled="busy">
      <button class="secondary-action" :disabled="busy || !selectedModel" @click="handleCreateShadow">
        生成影子错误
      </button>
      <button class="secondary-action" :disabled="busy || !shadow" @click="handleShadowDiagnosis">
        盲判
      </button>
      <button class="secondary-action" :disabled="busy || !diagnosis || !shadow" @click="handleReveal">
        揭示答案
      </button>
      <small v-if="shadow">会话：{{ shadow.sessionId }} · {{ shadow.state }}</small>
      <strong v-if="comparison">
        {{ comparison.exactMatch ? '类型和位置均正确' : '研判与真值存在偏差' }}
      </strong>
    </section>

    <p class="result-label">研判结果</p>
    <output class="result-box diagnosis-result" aria-live="polite">
      <template v-if="diagnosis">
        <div class="diagnosis-target-result">
          <span>{{ diagnosis.status }}</span>
          <strong>{{ diagnosis.predictedEventType || '数据不足' }}</strong>
          <small>{{ diagnosis.targetBusinessId || '无故障定位' }}</small>
        </div>
        <div class="diagnosis-result-primary">
          <span>模型原始判断</span>
          <strong>异常分数 {{ percent(diagnosis.anomalyScore) }}</strong>
          <em>置信度 {{ percent(diagnosis.confidence) }} · 阈值 {{ percent(diagnosis.anomalyThreshold) }}</em>
        </div>
        <p>{{ diagnosis.summary }}</p>
        <small>诊断ID：{{ diagnosis.diagnosisId }}</small>
        <small>白箱档案：{{ diagnosis.artifactPath }}</small>
      </template>
      <span v-else class="diagnosis-empty">选择模型后，可以分别研判正式当前快照或隔离影子错误。</span>
    </output>

    <section class="ground-truth-card">
      <div>
        <span>独立短路分析</span>
        <strong>不写入连续潮流</strong>
      </div>
      <input v-model.trim="shortCircuitTargetId" placeholder="母线businessId" :disabled="busy">
      <select v-model="shortCircuitType" :disabled="busy">
        <option value="3ph">三相短路</option>
        <option value="2ph">两相短路</option>
        <option value="1ph">单相接地</option>
      </select>
      <input v-model.trim="shortCircuitPowerMva" type="number" min="0" placeholder="外部电网短路容量 MVA" :disabled="busy">
      <input v-model.trim="shortCircuitRx" type="number" min="0" step="0.01" placeholder="外部电网 R/X" :disabled="busy">
      <button class="secondary-action" :disabled="busy" @click="handleShortCircuit">执行短路分析</button>
      <small v-if="shortCircuit">
        {{ shortCircuit.analysisId }} · {{ shortCircuit.status }} · {{ shortCircuit.artifactPath }}
      </small>
    </section>
  </section>
</template>
