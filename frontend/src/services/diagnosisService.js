async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
    ...options,
  })
  if (!response.ok) {
    let message = `服务接口请求失败：${response.status}`
    try {
      const body = await response.json()
      message = body.message || body.detail || message
    } catch {
      // 非JSON错误保留HTTP状态。
    }
    throw new Error(message)
  }
  return response.status === 204 ? null : response.json()
}

export const fetchInferenceModels = () => request('/api/inference/models')
export const fetchInferenceModelHistory = () => request('/api/inference/model/history')
export const selectInferenceModel = (modelId) => request('/api/inference/model/select', {
  method: 'POST',
  body: JSON.stringify({ modelId, actor: 'web-user' }),
})
export const rollbackInferenceModel = () => request('/api/inference/model/rollback', {
  method: 'POST',
  body: '{}',
})
export const diagnoseCurrentSnapshot = () => request('/api/diagnosis/current', {
  method: 'POST',
  body: '{}',
})
export const startDiagnosisMonitor = () => request('/api/diagnosis/monitor/start', {
  method: 'POST',
  body: JSON.stringify({ intervalSeconds: 5 }),
})
export const fetchDiagnosisMonitor = () => request('/api/diagnosis/monitor/status')
export const stopDiagnosisMonitor = () => request('/api/diagnosis/monitor/stop', {
  method: 'POST',
  body: '{}',
})
export const createShadowSession = (payload) => request('/api/shadow-sessions', {
  method: 'POST',
  body: JSON.stringify(payload),
})
export const diagnoseShadowSession = (sessionId) => request(
  `/api/shadow-sessions/${encodeURIComponent(sessionId)}/diagnose`,
  { method: 'POST', body: '{}' },
)
export const revealShadowSession = (sessionId) => request(
  `/api/shadow-sessions/${encodeURIComponent(sessionId)}/reveal`,
  { method: 'POST', body: '{}' },
)
export const closeShadowSession = (sessionId) => request(
  `/api/shadow-sessions/${encodeURIComponent(sessionId)}`,
  { method: 'DELETE' },
)
export const runShortCircuitAnalysis = (payload) => request('/api/short-circuit-analyses', {
  method: 'POST',
  body: JSON.stringify(payload),
})
