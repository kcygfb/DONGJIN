async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
    ...options,
  })

  if (!response.ok) {
    let message = `训练接口请求失败：${response.status}`
    try {
      const body = await response.json()
      message = body.message || body.detail || message
    } catch {
      // 后端未返回 JSON 时保留状态码提示。
    }
    throw new Error(message)
  }

  if (response.status === 204) {
    return null
  }
  return response.json()
}

export function generateFaults(payload) {
  return request('/api/training/errors/generate', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function fetchFaults() {
  return request('/api/training/errors')
}

export function startTraining(payload) {
  return request('/api/training/jobs', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function fetchTrainingJob(jobId) {
  return request(`/api/training/jobs/${encodeURIComponent(jobId)}`)
}
