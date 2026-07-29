import { CircleAlert, LoaderCircle, PlugZap } from 'lucide-react'

import type { ConnectionState } from '../hooks/use-conversation-events'

const labels: Record<ConnectionState, string> = {
  connecting: '连接中',
  connected: '已连接',
  disconnected: '连接已断开',
  reconnecting: '重连中',
}

export function ConnectionStatus({ state }: { state: ConnectionState }) {
  const Icon = state === 'connected' ? PlugZap : state === 'disconnected' ? CircleAlert : LoaderCircle
  return (
    <span className={`connection-status connection-status--${state}`} role="status">
      <Icon aria-hidden="true" size={14} className={state.includes('ing') ? 'spin' : ''} />
      {labels[state]}
    </span>
  )
}
