import type { components } from './generated/schema'

export type Agent = components['schemas']['AgentResponse']
export type Project = components['schemas']['ProjectResponse']
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

const STORAGE_KEY = 'agenthub_project_id'

function getStoredProjectId(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY)
  } catch {
    return null
  }
}

function storeProjectId(id: string): void {
  try {
    localStorage.setItem(STORAGE_KEY, id)
  } catch {
    // localStorage 不可用时静默忽略
  }
}

/** 确保项目存在：优先 localStorage，其次环境变量，最后自动创建 */
export async function ensureProject(): Promise<string> {
  // 环境变量优先（VITE_PROJECT_ID）
  const envId = import.meta.env.VITE_PROJECT_ID
  if (envId) {
    try {
      await request(`/api/v1/projects/${envId}`)
      storeProjectId(envId)
      return envId
    } catch {
      // 环境变量项目不存在，继续
    }
  }
  // 其次尝试 localStorage 缓存
  const stored = getStoredProjectId()
  if (stored !== null && stored !== envId) {
    try {
      await request(`/api/v1/projects/${stored}`)
      return stored
    } catch {
      localStorage.removeItem(STORAGE_KEY)
    }
  }
  // 自动创建新项目（后端会自动播种 6 个预设 Agent）
  try {
    const project = await request<{ id: string }>('/api/v1/projects', {
      method: 'POST',
      body: JSON.stringify({
        name: 'default-workspace',
        root_path: '.',
        description: 'AgentHub 默认工作区',
      }),
    })
    storeProjectId(project.id)
    return project.id
  } catch {
    // 创建失败（如项目已存在），回退到列出现有项目
    const projects: { id: string }[] = await request<{ id: string }[]>('/api/v1/projects')
    const first = projects[0]
    if (first) {
      storeProjectId(first.id)
      return first.id
    }
    throw new Error('无法初始化工作区：没有可用项目且自动创建失败')
  }
}

/** 当前项目 ID（由 ensureProject 初始化后设置） */
export const workspaceConfig = {
  projectId: '',
  async init(): Promise<string> {
    this.projectId = await ensureProject()
    return this.projectId
  },
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

function getBasePath(): string {
  return `/api/v1/projects/${workspaceConfig.projectId}`
}

export function listConversations(signal?: AbortSignal): Promise<Conversation[]> {
  return request(`${getBasePath()}/conversations`, { signal })
}

/** 创建单聊会话（默认使用 DeepSeek） */
export function createConversation(): Promise<Conversation> {
  return request(`${getBasePath()}/conversations`, {
    method: 'POST',
    body: JSON.stringify({ title: null, conversation_type: 'direct' }),
  })
}

/** 创建群聊会话（默认包含项目中全部启用的 Agent） */
export function createGroupConversation(): Promise<GroupConversation> {
  return request(`${getBasePath()}/conversations`, {
    method: 'POST',
    body: JSON.stringify({ title: null, conversation_type: 'group' }),
  })
}

export function listAgents(signal?: AbortSignal): Promise<Agent[]> {
  return request(`${getBasePath()}/agents`, { signal })
}

export function updateAgent(
  agentId: string,
  data: {
    name?: string
    capabilities?: string[]
    status?: string
  },
): Promise<Agent> {
  return request(`${getBasePath()}/agents/${agentId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  })
}

export function deleteConversation(conversationId: string): Promise<undefined> {
  return request<undefined>(`${getBasePath()}/conversations/${conversationId}`, { method: 'DELETE' })
}

export function listMessages(conversationId: string, signal?: AbortSignal): Promise<Message[]> {
  return request(`${getBasePath()}/conversations/${conversationId}/messages`, { signal })
}

export function submitMessage(conversationId: string, content: string): Promise<Submission> {
  return request(`${getBasePath()}/conversations/${conversationId}/messages`, {
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

export function resumePipeline(
  conversationId: string,
  action: string,
  feedback: string = "",
): Promise<{ status: string; action: string }> {
  return request(`${getBasePath()}/conversations/${conversationId}/pipeline/resume`, {
    method: "POST",
    body: JSON.stringify({ action, feedback }),
  })
}

export function cancelExecution(executionId: string): Promise<RealtimeEvent> {
  return request<RealtimeEvent>(`${getBasePath()}/executions/${executionId}/cancel`, { method: 'POST' })
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


