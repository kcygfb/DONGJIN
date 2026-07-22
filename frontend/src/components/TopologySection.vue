<script setup>
import { computed, onMounted, ref } from 'vue'
import { fetchTopology, generateStandardTopology } from '../services/topologyService'

const props = defineProps({
  diagnosis: {
    type: Object,
    default: null,
  },
})

const emit = defineEmits(['topologyChanged'])

const minimumCanvasWidth = 960
const minimumCanvasHeight = 560
const topology = ref({ nodes: [], edges: [] })
const isLoading = ref(false)
const isGenerating = ref(false)
const errorMessage = ref('')
const generationMessage = ref('')

const layout = computed(() => layoutNodes(topology.value.nodes, topology.value.edges))
const positionedNodes = computed(() => layout.value.nodes)
const canvasWidth = computed(() => layout.value.width)
const canvasHeight = computed(() => layout.value.height)
const upstreamNodeIds = computed(() => new Set(
  props.diagnosis?.trace?.upstream?.map((step) => step.nodeId) || [],
))
const downstreamNodeIds = computed(() => new Set(
  props.diagnosis?.trace?.downstream?.map((step) => step.nodeId) || [],
))
const upstreamEdgeIds = computed(() => new Set(
  props.diagnosis?.trace?.upstream?.map((step) => step.viaEdgeId).filter(Boolean) || [],
))
const downstreamEdgeIds = computed(() => new Set(
  props.diagnosis?.trace?.downstream?.map((step) => step.viaEdgeId).filter(Boolean) || [],
))

const positionedNodeMap = computed(() => {
  return new Map(positionedNodes.value.map((node) => [node.id, node]))
})

const visibleEdges = computed(() => {
  return topology.value.edges
    .map((edge) => ({
      ...edge,
      sourceNode: positionedNodeMap.value.get(edge.source),
      targetNode: positionedNodeMap.value.get(edge.target),
    }))
    .filter((edge) => edge.sourceNode && edge.targetNode)
})

const summaryText = computed(() => {
  return `${topology.value.nodes.length} 个设备 / ${topology.value.edges.length} 条连接`
})

const traceSummary = computed(() => {
  if (!props.diagnosis?.trace) {
    return ''
  }
  return `正在高亮 ${props.diagnosis.trace.nodeIds.length} 个溯源设备 / ${props.diagnosis.trace.edgeIds.length} 条路径连接`
})

onMounted(() => {
  loadTopology()
})

async function loadTopology() {
  isLoading.value = true
  errorMessage.value = ''

  try {
    topology.value = await fetchTopology()
    emit('topologyChanged')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '拓扑加载失败'
  } finally {
    isLoading.value = false
  }
}

async function handleGenerateTopology() {
  const confirmed = window.confirm(
    '将生成约 193 个设备和 200 多条连接，并替换上一次由程序生成的电网；手工创建的节点不会删除。是否继续？',
  )
  if (!confirmed) {
    return
  }

  isGenerating.value = true
  errorMessage.value = ''
  generationMessage.value = ''
  try {
    const result = await generateStandardTopology()
    generationMessage.value = `已生成 ${result.nodeCount} 个设备、${result.edgeCount} 条连接`
    await loadTopology()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '标准电网生成失败'
  } finally {
    isGenerating.value = false
  }
}

function layoutNodes(nodes, edges) {
  if (!nodes.length) {
    return { nodes: [], width: minimumCanvasWidth, height: minimumCanvasHeight }
  }

  const nodeIds = new Set(nodes.map((node) => node.id))
  const outgoing = new Map(nodes.map((node) => [node.id, []]))
  const incomingCount = new Map(nodes.map((node) => [node.id, 0]))

  edges.forEach((edge) => {
    if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target)) {
      return
    }

    outgoing.get(edge.source).push(edge.target)
    incomingCount.set(edge.target, incomingCount.get(edge.target) + 1)
  })

  const roots = nodes
    .filter((node) => incomingCount.get(node.id) === 0)
    .map((node) => node.id)

  const queue = roots.length ? [...roots] : [nodes[0].id]
  const levelById = new Map(queue.map((id) => [id, 0]))

  while (queue.length) {
    const currentId = queue.shift()
    const nextLevel = levelById.get(currentId) + 1

    outgoing.get(currentId).forEach((targetId) => {
      if (!levelById.has(targetId)) {
        levelById.set(targetId, nextLevel)
        queue.push(targetId)
      }
    })
  }

  const disconnectedLevel = Math.max(...levelById.values()) + 1
  nodes.forEach((node) => {
    if (!levelById.has(node.id)) {
      levelById.set(node.id, disconnectedLevel)
    }
  })

  const levels = new Map()
  nodes.forEach((node) => {
    const level = levelById.get(node.id)
    const group = levels.get(level) ?? []
    group.push(node)
    levels.set(level, group)
  })

  const maxRowsPerColumn = 12
  const horizontalGap = 150
  const verticalGap = 82
  const positionById = new Map()
  let nextX = 90
  let maximumRows = 1

  Array.from(levels.keys()).sort((left, right) => left - right).forEach((level) => {
    const group = levels.get(level)
    const columnCount = Math.max(1, Math.ceil(group.length / maxRowsPerColumn))
    maximumRows = Math.max(maximumRows, Math.min(group.length, maxRowsPerColumn))
    group.forEach((node, index) => {
      const column = Math.floor(index / maxRowsPerColumn)
      const row = index % maxRowsPerColumn
      positionById.set(node.id, {
        x: nextX + column * horizontalGap,
        y: 80 + row * verticalGap,
      })
    })
    nextX += columnCount * horizontalGap
  })

  return {
    width: Math.max(minimumCanvasWidth, nextX + 30),
    height: Math.max(minimumCanvasHeight, 160 + (maximumRows - 1) * verticalGap),
    nodes: nodes.map((node) => ({
      ...node,
      ...positionById.get(node.id),
    })),
  }
}

function nodeClass(node) {
  const classes = [
    'topology-node',
    `topology-node--${normalizeToken(node.type)}`,
    `topology-node--${normalizeToken(node.status)}`,
  ]
  if (props.diagnosis?.target?.kind === 'NODE' && props.diagnosis.target.id === node.id) {
    classes.push('topology-node--trace-target')
  }
  if (upstreamNodeIds.value.has(node.id)) {
    classes.push('topology-node--trace-upstream')
  }
  if (downstreamNodeIds.value.has(node.id)) {
    classes.push('topology-node--trace-downstream')
  }
  return classes
}

function edgeClass(edge) {
  const classes = [
    'topology-edge',
    `topology-edge--${normalizeToken(edge.status)}`,
  ]
  if (props.diagnosis?.target?.kind === 'EDGE' && props.diagnosis.target.id === edge.id) {
    classes.push('topology-edge--trace-target')
  }
  if (upstreamEdgeIds.value.has(edge.id)) {
    classes.push('topology-edge--trace-upstream')
  }
  if (downstreamEdgeIds.value.has(edge.id)) {
    classes.push('topology-edge--trace-downstream')
  }
  return classes
}

function normalizeToken(value) {
  return String(value || 'unknown').toLowerCase().replace(/[^a-z0-9-]/g, '-')
}
</script>

<template>
  <section class="workspace-section topology-section" aria-labelledby="topology-title">
    <div class="section-header">
      <p class="section-eyebrow">Topology</p>
      <h2 id="topology-title">电网拓扑区</h2>
    </div>

    <div class="topology-toolbar">
      <div>
        <span>{{ summaryText }}</span>
        <small v-if="generationMessage" class="topology-generation-message">{{ generationMessage }}</small>
        <small v-if="traceSummary" class="topology-trace-message">{{ traceSummary }}</small>
      </div>
      <div class="topology-toolbar-actions">
        <button
          class="secondary-action topology-refresh"
          type="button"
          :disabled="isLoading || isGenerating"
          @click="handleGenerateTopology"
        >
          {{ isGenerating ? '正在生成…' : '生成标准电网' }}
        </button>
        <button
          class="secondary-action topology-refresh"
          type="button"
          :disabled="isLoading || isGenerating"
          @click="loadTopology"
        >
          刷新拓扑
        </button>
      </div>
    </div>

    <div class="topology-canvas" aria-label="电网拓扑图">
      <div v-if="isLoading" class="topology-state">正在加载 Neo4j 拓扑数据...</div>

      <div v-else-if="errorMessage" class="topology-state topology-state--error">
        <p>{{ errorMessage }}</p>
        <button class="primary-action" type="button" @click="loadTopology">重试</button>
      </div>

      <div v-else-if="!topology.nodes.length" class="topology-state">
        Neo4j 中还没有 Device 节点
      </div>

      <svg
        v-else
        class="topology-svg"
        :viewBox="`0 0 ${canvasWidth} ${canvasHeight}`"
        :style="{ width: `${canvasWidth}px`, height: `${canvasHeight}px` }"
        role="img"
        aria-label="电网设备连接拓扑"
      >
        <defs>
          <marker
            id="topology-arrow"
            markerWidth="10"
            markerHeight="10"
            refX="9"
            refY="3"
            orient="auto"
            markerUnits="strokeWidth"
          >
            <path d="M0,0 L0,6 L9,3 z" class="topology-arrow" />
          </marker>
          <marker
            id="topology-arrow-upstream"
            markerWidth="10"
            markerHeight="10"
            refX="9"
            refY="3"
            orient="auto"
            markerUnits="strokeWidth"
          >
            <path d="M0,0 L0,6 L9,3 z" class="topology-arrow topology-arrow--upstream" />
          </marker>
          <marker
            id="topology-arrow-downstream"
            markerWidth="10"
            markerHeight="10"
            refX="9"
            refY="3"
            orient="auto"
            markerUnits="strokeWidth"
          >
            <path d="M0,0 L0,6 L9,3 z" class="topology-arrow topology-arrow--downstream" />
          </marker>
        </defs>

        <g class="topology-edges">
          <line
            v-for="edge in visibleEdges"
            :key="edge.id"
            :class="edgeClass(edge)"
            :x1="edge.sourceNode.x"
            :y1="edge.sourceNode.y"
            :x2="edge.targetNode.x"
            :y2="edge.targetNode.y"
            :marker-end="upstreamEdgeIds.has(edge.id)
              ? 'url(#topology-arrow-upstream)'
              : downstreamEdgeIds.has(edge.id)
                ? 'url(#topology-arrow-downstream)'
                : 'url(#topology-arrow)'"
          />
        </g>

        <g class="topology-edge-labels">
          <text
            v-for="edge in visibleEdges"
            :key="`${edge.id}-label`"
            class="topology-edge-label"
            :x="(edge.sourceNode.x + edge.targetNode.x) / 2"
            :y="(edge.sourceNode.y + edge.targetNode.y) / 2 - 8"
          >
            {{ edge.name }}
          </text>
        </g>

        <g class="topology-nodes">
          <g
            v-for="node in positionedNodes"
            :key="node.id"
            :class="nodeClass(node)"
            :transform="`translate(${node.x}, ${node.y})`"
          >
            <circle r="28" />
            <text class="topology-node-name" y="48">{{ node.name }}</text>
            <text class="topology-node-meta" y="66">{{ node.type }} · {{ node.voltageLevel }}</text>
          </g>
        </g>
      </svg>
    </div>
  </section>
</template>
