export interface ReadyHealth {
  status: 'ready'
  service: string
  checks: {
    configuration: 'ok'
  }
}

/**
 * 查询后端基础就绪状态。Phase 1 只验证应用配置已加载，不代表数据库或 Agent 可用。
 */
export async function fetchReadyHealth(signal?: AbortSignal): Promise<ReadyHealth> {
  const response = await fetch('/health/ready', {
    headers: { Accept: 'application/json' },
    signal,
  })

  if (!response.ok) {
    throw new Error('API 暂不可用')
  }

  return (await response.json()) as ReadyHealth
}

