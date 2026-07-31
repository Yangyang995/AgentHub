import { CircleAlert, PlugZap } from 'lucide-react'

import type { ConnectionState } from '../hooks/use-conversation-events'

const labels: Record<ConnectionState, string> = {
  connected: '已连接',
  disconnected: '连接已断开',
}

export function ConnectionStatus({ state }: { state: ConnectionState }) {
  const Icon = state === 'connected' ? PlugZap : CircleAlert
  if (state === 'connected') return null
  return (
    <span className={`connection-status connection-status--${state}`} role="status">
      <Icon aria-hidden="true" size={14} />
      {labels[state]}
    </span>
  )
}
