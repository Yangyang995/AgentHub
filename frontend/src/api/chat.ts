import type { components } from './generated/schema'

export type Agent = components['schemas']['AgentResponse']
export type ChatProvider = 'deepseek'
export type DirectConversation = components['schemas']['ConversationResponse']
export type GroupConversation = components['schemas']['GroupConversationResponse']
export type Conversation = DirectConversation | GroupConversation
export type Message = components['schemas']['MessageResponse']
export type DirectSubmission = components['schemas']['MessageSubmissionResponse']
export type GroupSubmission = components['schemas']['GroupMessageSubmissionResponse']
export type Submission = DirectSubmission | GroupSubmission
export type Execution = components['schemas']['AgentExecutionResponse']
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
    const body = (await response.json().catch(() => null)) as { detail?: unknown } | null
    const detail = typeof body?.detail === 'string' ? body.detail : null
    throw new Error(
      detail ??
        (response.status === 404
          ? '未找到对应资源，请检查工作区配置'
          : '请求失败，请稍后重试'),
    )
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

const basePath = `/api/v1/projects/${workspaceConfig.projectId}`

export function listConversations(signal?: AbortSignal): Promise<Conversation[]> {
  return request(`${basePath}/conversations`, { signal })
}

export function createConversation(provider: ChatProvider): Promise<Conversation> {
  return request(`${basePath}/conversations`, {
    method: 'POST',
    body: JSON.stringify({ title: null, provider, conversation_type: 'direct' }),
  })
}

export function listAgents(signal?: AbortSignal): Promise<Agent[]> {
  return request(`${basePath}/agents`, { signal })
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

export function submissionExecutions(submission: Submission): Execution[] {
  return 'executions' in submission ? submission.executions : [submission.execution]
}

export function isGroupConversation(conversation: Conversation): conversation is GroupConversation {
  return 'participants' in conversation
}

export function cancelExecution(executionId: string): Promise<RealtimeEvent> {
  return request<RealtimeEvent>(`${basePath}/executions/${executionId}/cancel`, { method: 'POST' })
}

export function buildWebSocketUrl(
  conversationId: string,
  executionId?: string,
  lastSequence = -1,
  cursors?: ReadonlyMap<string, number>,
): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const params = new URLSearchParams({ project_id: workspaceConfig.projectId })
  if (executionId !== undefined) {
    params.set('execution_id', executionId)
    params.set('last_sequence', String(lastSequence))
  }
  cursors?.forEach((sequence, id) => {
    params.append('cursor', `${id}:${String(sequence)}`)
  })
  return `${protocol}//${window.location.host}/ws/conversations/${conversationId}?${params.toString()}`
}
