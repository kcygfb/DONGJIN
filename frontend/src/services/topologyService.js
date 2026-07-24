export async function fetchTopology() {
  const response = await fetch('/api/topology')

  if (!response.ok) {
    if (response.status === 502) {
      throw new Error('后端服务未启动，或 8080 端口不可访问')
    }

    throw new Error(`拓扑接口请求失败：${response.status}`)
  }

  const data = await response.json()

  return {
    nodes: Array.isArray(data.nodes) ? data.nodes : [],
    edges: Array.isArray(data.edges) ? data.edges : [],
  }
}

export async function fetchGridSource() {
  const response = await fetch('/api/topology/source')

  if (response.status === 404 || response.status === 502) {
    return null
  }
  if (!response.ok) {
    throw new Error(`标准电网源查询失败：${response.status}`)
  }
  return response.json()
}

export async function generateStandardTopology() {
  const response = await fetch('/api/topology/generate', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      topologyVersion: 'v1',
      force: false,
    }),
  })

  if (!response.ok) {
    let message = `电网生成接口请求失败：${response.status}`
    try {
      const body = await response.json()
      message = body.message || body.detail || message
    } catch {
      // 保留默认错误信息。
    }
    throw new Error(message)
  }

  return response.json()
}

export async function fetchSimulationStatus() {
  return requestJson('/api/simulation/status')
}

export async function startSimulation(options = {}) {
  return requestJson('/api/simulation/start', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(options),
  })
}

export async function pauseSimulation() {
  return requestJson('/api/simulation/pause', { method: 'POST' })
}

export async function resumeSimulation() {
  return requestJson('/api/simulation/resume', { method: 'POST' })
}

export async function stopSimulation() {
  return requestJson('/api/simulation/stop', { method: 'POST' })
}

export async function fetchCurrentSnapshot() {
  const response = await fetch('/api/snapshots/current')
  if (response.status === 404) {
    return null
  }
  return parseResponse(response, '当前潮流快照查询失败')
}

async function requestJson(url, options) {
  const response = await fetch(url, options)
  return parseResponse(response, '电网运行接口请求失败')
}

async function parseResponse(response, fallbackMessage) {
  if (!response.ok) {
    let message = `${fallbackMessage}：${response.status}`
    try {
      const body = await response.json()
      message = body.message || body.detail || message
    } catch {
      // 保留HTTP状态错误。
    }
    throw new Error(message)
  }
  return response.json()
}
