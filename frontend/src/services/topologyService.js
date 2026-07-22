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

export async function generateStandardTopology() {
  const response = await fetch('/api/topology/generate', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      regions: 3,
      substationsPerRegion: 3,
      feedersPerSubstation: 4,
      loadsPerFeeder: 3,
      seed: 20260717,
      replaceGenerated: true,
    }),
  })

  if (!response.ok) {
    let message = `电网生成接口请求失败：${response.status}`
    try {
      const body = await response.json()
      message = body.message || message
    } catch {
      // 保留默认错误信息。
    }
    throw new Error(message)
  }

  return response.json()
}
