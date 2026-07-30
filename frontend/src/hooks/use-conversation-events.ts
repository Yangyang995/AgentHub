import { useCallback, useEffect, useRef, useState } from 'react'

import { buildWebSocketUrl, type RealtimeEvent } from '../api/chat'

export type ConnectionState = 'connecting' | 'connected' | 'disconnected' | 'reconnecting'

export function useConversationEvents(
  conversationId: string | null,
  onEvent: (event: RealtimeEvent) => void,
) {
  const [connection, setConnection] = useState<ConnectionState>('disconnected')
  const cursorsRef = useRef(new Map<string, number>())
  const eventIdsRef = useRef(new Set<string>())
  const eventHandlerRef = useRef(onEvent)

  useEffect(() => {
    eventHandlerRef.current = onEvent
  }, [onEvent])

  useEffect(() => {
    cursorsRef.current.clear()
    eventIdsRef.current.clear()
    if (conversationId === null) return
    const selectedConversationId: string = conversationId

    let socket: WebSocket | null = null
    let retryTimer: number | undefined
    let stopped = false
    let attempts = 0

    function connect() {
      if (stopped) return
      setConnection(attempts === 0 ? 'connecting' : 'reconnecting')
      socket = new WebSocket(
        buildWebSocketUrl(selectedConversationId, undefined, -1, cursorsRef.current),
      )
      socket.addEventListener('open', () => {
        attempts = 0
        setConnection('connected')
      })
      socket.addEventListener('message', (message: MessageEvent<string>) => {
        const event = JSON.parse(message.data) as RealtimeEvent
        if (eventIdsRef.current.has(event.event_id)) return
        eventIdsRef.current.add(event.event_id)
        cursorsRef.current.set(
          event.execution_id,
          Math.max(cursorsRef.current.get(event.execution_id) ?? -1, event.sequence),
        )
        eventHandlerRef.current(event)
      })
      socket.addEventListener('close', () => {
        if (stopped) return
        setConnection('disconnected')
        attempts += 1
        retryTimer = window.setTimeout(connect, Math.min(500 * 2 ** (attempts - 1), 5000))
      })
    }

    connect()
    return () => {
      stopped = true
      if (retryTimer !== undefined) window.clearTimeout(retryTimer)
      socket?.close()
    }
  }, [conversationId])

  const followExecution = useCallback((executionId: string) => {
    if (!cursorsRef.current.has(executionId)) cursorsRef.current.set(executionId, -1)
  }, [])

  return { connection, followExecution }
}
