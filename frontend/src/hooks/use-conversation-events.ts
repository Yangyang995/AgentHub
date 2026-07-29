import { useCallback, useEffect, useRef, useState } from 'react'

import { buildWebSocketUrl, type RealtimeEvent } from '../api/chat'

export type ConnectionState = 'connecting' | 'connected' | 'disconnected' | 'reconnecting'

interface Cursor {
  executionId?: string
  sequence: number
}

export function useConversationEvents(
  conversationId: string | null,
  onEvent: (event: RealtimeEvent) => void,
) {
  const [connection, setConnection] = useState<ConnectionState>('disconnected')
  const cursorRef = useRef<Cursor>({ sequence: -1 })
  const eventIdsRef = useRef(new Set<string>())
  const eventHandlerRef = useRef(onEvent)

  useEffect(() => {
    eventHandlerRef.current = onEvent
  }, [onEvent])

  useEffect(() => {
    cursorRef.current = { sequence: -1 }
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
      const cursor = cursorRef.current
      socket = new WebSocket(
        buildWebSocketUrl(selectedConversationId, cursor.executionId, cursor.sequence),
      )
      socket.addEventListener('open', () => {
        attempts = 0
        setConnection('connected')
      })
      socket.addEventListener('message', (message: MessageEvent<string>) => {
        const event = JSON.parse(message.data) as RealtimeEvent
        if (eventIdsRef.current.has(event.event_id)) return
        eventIdsRef.current.add(event.event_id)
        cursorRef.current = { executionId: event.execution_id, sequence: event.sequence }
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
    if (cursorRef.current.executionId !== executionId) {
      cursorRef.current = { executionId, sequence: -1 }
    }
  }, [])

  return { connection, followExecution }
}
