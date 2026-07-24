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
      // 后端未返回 JSON 时保留状态码提示。
    }
    throw new Error(message)
  }

  if (response.status === 204) {
    return null
  }
  return response.json()
}

export function locateFault(payload) {
  return request('/api/diagnosis/locate', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}
