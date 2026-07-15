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
