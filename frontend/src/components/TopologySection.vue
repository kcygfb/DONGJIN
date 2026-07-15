<script setup>
import { computed, onMounted, ref } from 'vue'
import { fetchTopology } from '../services/topologyService'

const canvasWidth = 960
const canvasHeight = 560
const topology = ref({ nodes: [], edges: [] })
const isLoading = ref(false)
const errorMessage = ref('')

const positionedNodes = computed(() =>
  layoutNodes(topology.value.nodes, topology.value.edges),
)

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

onMounted(() => {
  loadTopology()
})

async function loadTopology() {
  isLoading.value = true
  errorMessage.value = ''

  try {
    topology.value = await fetchTopology()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '拓扑加载失败'
  } finally {
    isLoading.value = false
  }
}

function layoutNodes(nodes, edges) {
  if (!nodes.length) {
    return []
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
      if (!levelById.has(targetId) || nextLevel > levelById.get(targetId)) {
        levelById.set(targetId, nextLevel)
        queue.push(targetId)
      }
    })
  }

  nodes.forEach((node) => {
    if (!levelById.has(node.id)) {
      levelById.set(node.id, Math.max(...levelById.values()) + 1)
    }
  })

  const levels = new Map()
  nodes.forEach((node) => {
    const level = levelById.get(node.id)
    const group = levels.get(level) ?? []
    group.push(node)
    levels.set(level, group)
  })

  const maxLevel = Math.max(...levels.keys())
  const horizontalGap = maxLevel === 0 ? 0 : (canvasWidth - 180) / maxLevel

  return nodes.map((node) => {
    const level = levelById.get(node.id)
    const group = levels.get(level)
    const index = group.findIndex((item) => item.id === node.id)
    const verticalGap = group.length <= 1 ? 0 : (canvasHeight - 160) / (group.length - 1)

    return {
      ...node,
      x: 90 + level * horizontalGap,
      y: group.length <= 1 ? canvasHeight / 2 : 80 + index * verticalGap,
    }
  })
}

function nodeClass(node) {
  return [
    'topology-node',
    `topology-node--${normalizeToken(node.type)}`,
    `topology-node--${normalizeToken(node.status)}`,
  ]
}

function edgeClass(edge) {
  return [
    'topology-edge',
    `topology-edge--${normalizeToken(edge.status)}`,
  ]
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
      <span>{{ summaryText }}</span>
      <button class="secondary-action topology-refresh" type="button" @click="loadTopology">
        刷新拓扑
      </button>
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
            marker-end="url(#topology-arrow)"
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
