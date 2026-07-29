import type { components } from './generated/schema'

export type Conversation = components['schemas']['ConversationResponse']
export type Message = components['schemas']['MessageResponse']
export type Submission = components['schemas']['MessageSubmissionResponse']
export type ExecutionStatus = components['schemas']['ExecutionStatus']
export type AgentEvent = components['schemas']['AgentEvent']
type EventEnvelope = components['schemas']['EventEnvelope']

type EventPayload<T extends AgentEvent> = Omit<
  T,
  'event_id' | 'execution_id' | 'sequence' | 'timestamp' | 'event_type'
>

export type RealtimeEvent = AgentEvent extends infer Event
  ? Event extends AgentEvent
    ? Omit<EventEnvelope, 'type' | 'payload'> & {
        type: Event['event_type']
        payload: EventPayload<Event>
      }
    : never
  : never

export const workspaceConfig = {
  projectId: import.meta.env.VITE_PROJECT_ID ?? '00000000-0000-0000-0000-000000000001',
  agentId: import.meta.env.VITE_AGENT_ID ?? '00000000-0000-0000-0000-000000000002',
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  headers.set('Accept', 'application/json')
  headers.set('Content-Type', 'application/json')
  const response = await fetch(path, {
    ...init,
    headers,
  })
  if (!response.ok) {
    throw new Error(response.status === 404 ? '未找到对应资源，请检查工作区配置' : '请求失败，请稍后重试')
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

const basePath = `/api/v1/projects/${workspaceConfig.projectId}`

export function listConversations(signal?: AbortSignal): Promise<Conversation[]> {
  return request(`${basePath}/conversations`, { signal })
}

export function createConversation(agentId: string): Promise<Conversation> {
  return request(`${basePath}/conversations`, {
    method: 'POST',
    body: JSON.stringify({ title: null, agent_id: agentId, conversation_type: 'direct' }),
  })
}

export function deleteConversation(conversationId: string): Promise<undefined> {
  return request<undefined>(`${basePath}/conversations/${conversationId}`, { method: 'DELETE' })
}

export function listMessages(conversationId: string, signal?: AbortSignal): Promise<Message[]> {
  return request(`${basePath}/conversations/${conversationId}/messages`, { signal })
}

export function submitMessage(conversationId: string, content: string): Promise<Submission> {
  return request(`${basePath}/conversations/${conversationId}/messages`, {
    method: 'POST',
    body: JSON.stringify({ content, content_type: 'markdown' }),
  })
}

export function cancelExecution(executionId: string): Promise<RealtimeEvent> {
  return request<RealtimeEvent>(`${basePath}/executions/${executionId}/cancel`, { method: 'POST' })
}

export function buildWebSocketUrl(
  conversationId: string,
  executionId?: string,
  lastSequence = -1,
): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const params = new URLSearchParams({ project_id: workspaceConfig.projectId })
  if (executionId !== undefined) {
    params.set('execution_id', executionId)
    params.set('last_sequence', String(lastSequence))
  }
  return `${protocol}//${window.location.host}/ws/conversations/${conversationId}?${params.toString()}`
}
