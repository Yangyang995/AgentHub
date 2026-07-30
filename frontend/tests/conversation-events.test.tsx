import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { RealtimeEvent } from '../src/api/chat'
import { useConversationEvents } from '../src/hooks/use-conversation-events'

class MockWebSocket {
  static instances: MockWebSocket[] = []
  readonly url: string
  private listeners = new Map<string, ((event: Event | MessageEvent<string>) => void)[]>()

  constructor(url: string | URL) {
    this.url = String(url)
    MockWebSocket.instances.push(this)
  }

  addEventListener(type: string, listener: (event: Event | MessageEvent<string>) => void) {
    const current = this.listeners.get(type) ?? []
    current.push(listener)
    this.listeners.set(type, current)
  }

  close() { return undefined }

  emit(type: string, event: Event | MessageEvent<string> = new Event(type)) {
    this.listeners.get(type)?.forEach((listener) => { listener(event) })
  }
}

const deltaEvent: RealtimeEvent = {
  event_id: '00000000-0000-0000-0000-000000000010',
  conversation_id: '00000000-0000-0000-0000-000000000003',
  execution_id: '00000000-0000-0000-0000-000000000004',
  sequence: 1,
  type: 'content.delta',
  timestamp: '2026-07-29T00:00:00Z',
  payload: { delta: 'hello', content_type: 'markdown' },
}

beforeEach(() => {
  MockWebSocket.instances = []
  vi.stubGlobal('WebSocket', MockWebSocket)
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('useConversationEvents', () => {
  it('按 event_id 去重并在断开后使用执行游标重连', () => {
    vi.useFakeTimers()
    const onEvent = vi.fn()
    const { result } = renderHook(() =>
      useConversationEvents('00000000-0000-0000-0000-000000000003', onEvent),
    )
    const first = MockWebSocket.instances[0]
    if (first === undefined) throw new Error('WebSocket 未创建')

    act(() => { first.emit('open') })
    expect(result.current.connection).toBe('connected')
    act(() => {
      const message = new MessageEvent('message', { data: JSON.stringify(deltaEvent) })
      first.emit('message', message)
      first.emit('message', message)
    })
    expect(onEvent).toHaveBeenCalledTimes(1)

    act(() => {
      first.emit('close')
      vi.advanceTimersByTime(500)
    })
    expect(MockWebSocket.instances).toHaveLength(2)
    const second = MockWebSocket.instances[1]
    if (second === undefined) throw new Error('重连 WebSocket 未创建')
    expect(second.url).toContain(
      `cursor=${encodeURIComponent(`${deltaEvent.execution_id}:1`)}`,
    )
  })
})
