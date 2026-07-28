import { useQuery } from '@tanstack/react-query'

import { fetchReadyHealth } from '../api/health'

export function HealthStatus() {
  const health = useQuery({
    queryKey: ['health', 'ready'],
    queryFn: ({ signal }) => fetchReadyHealth(signal),
  })

  if (health.isPending) {
    return <span className="health-status health-status--pending">正在连接</span>
  }

  if (health.isError) {
    return <span className="health-status health-status--error">API 离线</span>
  }

  return <span className="health-status health-status--ready">API 就绪</span>
}

