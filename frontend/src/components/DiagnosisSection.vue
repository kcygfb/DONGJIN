<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import { fetchTopology } from '../services/topologyService'
import { locateFault } from '../services/trainingService'

const emit = defineEmits(['diagnosed'])

const featureDefinitions = [
  { key: 'voltagePu', label: '电压标幺值' },
  { key: 'currentPu', label: '电流标幺值' },
  { key: 'activePowerPu', label: '有功功率标幺值' },
  { key: 'reactivePowerPu', label: '无功功率标幺值' },
  { key: 'temperatureC', label: '温度（℃）' },
  { key: 'connectivityRatio', label: '连接率' },
  { key: 'alarmCount', label: '告警数量' },
  { key: 'topologyDegree', label: '拓扑连接度' },
]

const faultDefinitions = [
  { type: 'DEVICE_OFFLINE', label: '设备离线', targetKind: 'NODE' },
  { type: 'VOLTAGE_ANOMALY', label: '电压异常', targetKind: 'NODE' },
  { type: 'LINE_OVERLOAD', label: '线路过载', targetKind: 'EDGE' },
  { type: 'LINE_DISCONNECTED', label: '线路断开', targetKind: 'EDGE' },
]

const faultLabels = Object.fromEntries(
  faultDefinitions.map((definition) => [definition.type, definition.label]),
)

const topology = ref({ nodes: [], edges: [] })
const groundTruth = ref(null)
const diagnosisResult = ref(null)
const isLoadingTopology = ref(false)
const isDiagnosing = ref(false)
const errorMessage = ref('')

const comparison = computed(() => {
  if (!groundTruth.value || !diagnosisResult.value) {
    return null
  }
  const locationMatch = groundTruth.value.targetKind === diagnosisResult.value.target.kind
    && groundTruth.value.targetId === diagnosisResult.value.target.id
  const typeMatch = groundTruth.value.faultType === diagnosisResult.value.prediction.predictedFaultType
  return {
    locationMatch,
    typeMatch,
    exactMatch: locationMatch && typeMatch,
  }
})

onMounted(loadTopology)

async function loadTopology() {
  isLoadingTopology.value = true
  errorMessage.value = ''
  try {
    topology.value = await fetchTopology()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '当前拓扑加载失败'
  } finally {
    isLoadingTopology.value = false
  }
}

async function generateAndDiagnose() {
  if (!topology.value.nodes.length || !topology.value.edges.length) {
    errorMessage.value = '当前拓扑不完整，请先在第一板块生成标准电网。'
    return
  }

  isDiagnosing.value = true
  errorMessage.value = ''
  diagnosisResult.value = null
  emit('diagnosed', null)

  try {
    const testCase = createBlindTestCase(topology.value)
    groundTruth.value = testCase.groundTruth

    // Let the written ground truth render before sending the unlabeled observations.
    await nextTick()
    diagnosisResult.value = await locateFault({
      observations: testCase.observations,
      topK: 4,
      traceDepth: 4,
    })
    emit('diagnosed', diagnosisResult.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '自动定位研判失败'
  } finally {
    isDiagnosing.value = false
  }
}

function createBlindTestCase(currentTopology) {
  const degreeByNodeId = calculateDegrees(currentTopology.nodes, currentTopology.edges)
  const definition = randomItem(faultDefinitions)
  const targets = definition.targetKind === 'NODE' ? currentTopology.nodes : currentTopology.edges
  const target = randomItem(targets)
  const observations = [
    ...currentTopology.nodes.map((node) => ({
      targetKind: 'NODE',
      targetId: node.id,
      features: normalFeatures(degreeByNodeId.get(node.id) || 0),
    })),
    ...currentTopology.edges.map((edge) => ({
      targetKind: 'EDGE',
      targetId: edge.id,
      features: normalFeatures(2),
    })),
  ]
  const targetObservation = observations.find((observation) => {
    return observation.targetKind === definition.targetKind && observation.targetId === target.id
  })
  const targetDegree = definition.targetKind === 'NODE' ? degreeByNodeId.get(target.id) || 0 : 2
  targetObservation.features = faultFeatures(definition.type, targetDegree)
  const affectedObservationCount = applyObservationPropagation(
    observations,
    currentTopology,
    definition.targetKind,
    target.id,
    targetObservation.features,
  )

  return {
    groundTruth: {
      injectionId: `blind-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
      generatedAt: new Date().toISOString(),
      faultType: definition.type,
      faultLabel: definition.label,
      targetKind: definition.targetKind,
      targetId: target.id,
      targetName: target.name,
      features: targetObservation.features,
      observationCount: observations.length,
      affectedObservationCount,
    },
    observations,
  }
}

function applyObservationPropagation(observations, currentTopology, targetKind, targetId, faultValues) {
  const keyOf = (kind, id) => `${kind}:${id}`
  const neighbors = new Map(observations.map((item) => [keyOf(item.targetKind, item.targetId), []]))
  currentTopology.edges.forEach((edge) => {
    const edgeKey = keyOf('EDGE', edge.id)
    for (const nodeId of [edge.source, edge.target]) {
      const nodeKey = keyOf('NODE', nodeId)
      neighbors.get(nodeKey)?.push(edgeKey)
      neighbors.get(edgeKey)?.push(nodeKey)
    }
  })

  const sourceKey = keyOf(targetKind, targetId)
  const distances = new Map([[sourceKey, 0]])
  const queue = [sourceKey]
  while (queue.length) {
    const current = queue.shift()
    const distance = distances.get(current)
    if (distance >= 2) continue
    for (const neighbor of neighbors.get(current) || []) {
      if (!distances.has(neighbor)) {
        distances.set(neighbor, distance + 1)
        queue.push(neighbor)
      }
    }
  }

  let affectedCount = 1
  observations.forEach((observation) => {
    const distance = distances.get(keyOf(observation.targetKind, observation.targetId))
    const factor = distance === 1 ? 0.22 : distance === 2 ? 0.08 : 0
    if (!factor) return
    affectedCount += 1
    observation.features = blendFeatures(observation.features, faultValues, factor)
  })
  return affectedCount
}

function blendFeatures(normal, fault, factor) {
  const blended = { ...normal }
  Object.keys(blended).forEach((key) => {
    if (key === 'topologyDegree') return
    const value = blended[key] * (1 - factor) + fault[key] * factor
    blended[key] = key === 'alarmCount' ? Math.round(value) : round(value)
  })
  blended.connectivityRatio = Math.max(0, Math.min(1, blended.connectivityRatio))
  return blended
}

function calculateDegrees(nodes, edges) {
  const degrees = new Map(nodes.map((node) => [node.id, 0]))
  edges.forEach((edge) => {
    degrees.set(edge.source, (degrees.get(edge.source) || 0) + 1)
    degrees.set(edge.target, (degrees.get(edge.target) || 0) + 1)
  })
  return degrees
}

function normalFeatures(degree) {
  return featureSet(
    randomBetween(0.975, 1.025),
    randomBetween(0.35, 0.75),
    randomBetween(0.30, 0.66),
    randomBetween(0.07, 0.23),
    randomBetween(32, 48),
    randomBetween(0.96, 1),
    Math.random() < 0.86 ? 0 : 1,
    degree,
  )
}

function faultFeatures(type, degree) {
  switch (type) {
    case 'DEVICE_OFFLINE':
      return featureSet(
        randomBetween(0.01, 0.05), randomBetween(0, 0.03), randomBetween(0, 0.02),
        randomBetween(0, 0.01), randomBetween(28, 38), randomBetween(0.03, 0.16),
        randomInteger(5, 7), degree,
      )
    case 'VOLTAGE_ANOMALY': {
      const voltage = Math.random() < 0.5 ? randomBetween(0.62, 0.78) : randomBetween(1.23, 1.36)
      return featureSet(
        voltage, randomBetween(0.72, 0.94), randomBetween(0.59, 0.76),
        randomBetween(0.29, 0.42), randomBetween(54, 66), randomBetween(0.82, 0.9),
        randomInteger(3, 5), degree,
      )
    }
    case 'LINE_OVERLOAD':
      return featureSet(
        randomBetween(0.86, 0.93), randomBetween(1.48, 1.75), randomBetween(1.31, 1.58),
        randomBetween(0.43, 0.58), randomBetween(82, 101), randomBetween(0.85, 0.92),
        randomInteger(4, 6), degree,
      )
    case 'LINE_DISCONNECTED':
      return featureSet(
        randomBetween(0.12, 0.24), randomBetween(0, 0.025), randomBetween(0, 0.02),
        randomBetween(0, 0.02), randomBetween(29, 40), randomBetween(0.03, 0.16),
        randomInteger(7, 9), degree,
      )
    default:
      throw new Error(`不支持的测试故障：${type}`)
  }
}

function featureSet(
  voltagePu,
  currentPu,
  activePowerPu,
  reactivePowerPu,
  temperatureC,
  connectivityRatio,
  alarmCount,
  topologyDegree,
) {
  return {
    voltagePu: round(voltagePu),
    currentPu: round(currentPu),
    activePowerPu: round(activePowerPu),
    reactivePowerPu: round(reactivePowerPu),
    temperatureC: round(temperatureC),
    connectivityRatio: round(connectivityRatio),
    alarmCount,
    topologyDegree,
  }
}

function randomItem(values) {
  return values[Math.floor(Math.random() * values.length)]
}

function randomBetween(minimum, maximum) {
  return minimum + Math.random() * (maximum - minimum)
}

function randomInteger(minimum, maximum) {
  return Math.floor(randomBetween(minimum, maximum + 1))
}

function round(value) {
  return Math.round(value * 10_000) / 10_000
}

function faultLabel(type) {
  return faultLabels[type] || type || '未知故障'
}

function percent(value) {
  return Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : '--'
}
</script>

<template>
  <section class="workspace-section diagnosis-section" aria-labelledby="diagnosis-title">
    <div class="section-header diagnosis-heading">
      <div>
        <p class="section-eyebrow">Blind Diagnosis</p>
        <h2 id="diagnosis-title">全拓扑盲判与溯源</h2>
      </div>
      <span class="diagnosis-model-state">真值仅保存在前端</span>
    </div>

    <div class="blind-workflow-note">
      <strong>测试隔离流程</strong>
      <span>前端注入真值 → 生成全拓扑无标签观测 → Java/Python 盲判 → 前端对比答案</span>
    </div>

    <button
      class="primary-action blind-start"
      type="button"
      :disabled="isLoadingTopology || isDiagnosing || !topology.nodes.length"
      @click="generateAndDiagnose"
    >
      {{ isDiagnosing ? '正在扫描全拓扑并研判…' : groundTruth ? '重新生成隐藏故障并盲判' : '生成隐藏故障并开始盲判' }}
    </button>

    <p v-if="errorMessage" class="diagnosis-error" role="alert">{{ errorMessage }}</p>

    <section v-if="groundTruth" class="ground-truth-card" aria-labelledby="ground-truth-title">
      <div>
        <span>测试真值 · 仅浏览器持有</span>
        <strong id="ground-truth-title">{{ groundTruth.faultLabel }}</strong>
        <small>{{ groundTruth.targetName }}（{{ groundTruth.targetId }}）</small>
      </div>
      <dl>
        <template v-for="feature in featureDefinitions" :key="feature.key">
          <dt>{{ feature.label }}</dt>
          <dd>{{ groundTruth.features[feature.key] }}</dd>
        </template>
      </dl>
      <p>
        已为当前拓扑生成 {{ groundTruth.observationCount }} 条观测，其中 {{ groundTruth.affectedObservationCount }} 个对象包含衰减传播信号。
        发送给后端的请求不包含以上故障类型和真实位置。
      </p>
    </section>

    <p class="result-label">盲判结果与真值对比</p>
    <output class="result-box diagnosis-result" aria-live="polite">
      <template v-if="diagnosisResult">
        <div :class="['blind-comparison', { 'blind-comparison--success': comparison.exactMatch }]">
          <strong>{{ comparison.exactMatch ? '研判完全正确' : '研判存在偏差' }}</strong>
          <span>位置 {{ comparison.locationMatch ? '一致' : '不一致' }}</span>
          <span>类型 {{ comparison.typeMatch ? '一致' : '不一致' }}</span>
        </div>

        <div class="diagnosis-target-result">
          <span>模型定位结果</span>
          <strong>{{ diagnosisResult.target.name }}</strong>
          <small>{{ diagnosisResult.target.id }}</small>
        </div>

        <div class="diagnosis-result-primary">
          <span>模型研判类型</span>
          <strong>{{ faultLabel(diagnosisResult.prediction.predictedFaultType) }}</strong>
          <em>
            异常分数 {{ percent(diagnosisResult.prediction.anomalyScore) }} ·
            分类置信度 {{ percent(diagnosisResult.prediction.confidence) }}
          </em>
        </div>

        <div class="location-ranking">
          <h3>定位候选 Top 5</h3>
          <ol>
            <li v-for="candidate in diagnosisResult.locationCandidates" :key="`${candidate.targetKind}-${candidate.targetId}`">
              <span>{{ candidate.targetName }}</span>
              <small>{{ faultLabel(candidate.predictedFaultType) }}</small>
              <strong>{{ percent(candidate.anomalyScore) }}</strong>
            </li>
          </ol>
        </div>

        <div class="trace-columns">
          <section>
            <h3>向上游溯源</h3>
            <p v-if="!diagnosisResult.trace.upstream.length">已到达电源侧边界</p>
            <ol v-else>
              <li v-for="step in diagnosisResult.trace.upstream" :key="`up-${step.nodeId}`">
                <span>{{ step.nodeName }}</span>
                <small>第 {{ step.depth }} 层 · {{ step.viaEdgeName || '线路上游端点' }}</small>
              </li>
            </ol>
          </section>
          <section>
            <h3>向下游追踪</h3>
            <p v-if="!diagnosisResult.trace.downstream.length">已到达负荷侧边界</p>
            <ol v-else>
              <li v-for="step in diagnosisResult.trace.downstream" :key="`down-${step.nodeId}`">
                <span>{{ step.nodeName }}</span>
                <small>第 {{ step.depth }} 层 · {{ step.viaEdgeName || '线路下游端点' }}</small>
              </li>
            </ol>
          </section>
        </div>

        <small>
          主模型：{{ diagnosisResult.prediction.modelType || 'GNN_GCN' }} ·
          模型版本：{{ diagnosisResult.prediction.modelVersion }} ·
          盲判观测数：{{ diagnosisResult.observationCount }}
        </small>
      </template>
      <span v-else-if="isDiagnosing" class="diagnosis-empty">
        真实情况已展示；Java/Python 正在对无标签观测执行盲判。
      </span>
      <span v-else class="diagnosis-empty">
        点击按钮生成一次测试故障，并自动完成全拓扑定位、类型研判和双向溯源。
      </span>
    </output>
  </section>
</template>
