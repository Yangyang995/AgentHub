import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { WorkbenchPage } from '../src/routes/workbench-page'

const projectId = '00000000-0000-0000-0000-000000000001'
const agentId = '00000000-0000-0000-0000-000000000002'
const conversationId = '00000000-0000-0000-0000-000000000003'
const executionId = '00000000-0000-0000-0000-000000000004'
const secondConversationId = '00000000-0000-0000-0000-000000000006'
const secondAgentId = '00000000-0000-0000-0000-000000000007'
const secondExecutionId = '00000000-0000-0000-0000-000000000008'

const conversation = {
  id: conversationId,
  project_id: projectId,
  agent_id: agentId,
  agent_name: 'DeepSeek',
  agent_type: 'openai_compatible' as const,
  title: '登录问题',
  conversation_type: 'direct' as const,
  created_at: '2026-07-29T00:00:00Z',
  updated_at: '2026-07-29T00:00:00Z',
}

const secondConversation = {
  ...conversation,
  id: secondConversationId,
  title: '第二个会话',
}

const agents = [
  {
    id: agentId,
    project_id: projectId,
    name: 'Code',
    agent_type: 'mock' as const,
    capabilities: ['code_generation'],
    status: 'enabled' as const,
    adapter_config_ref: null,
    created_at: '2026-07-29T00:00:00Z',
    updated_at: '2026-07-29T00:00:00Z',
  },
  {
    id: secondAgentId,
    project_id: projectId,
    name: 'Coder',
    agent_type: 'mock' as const,
    capabilities: ['code_review'],
    status: 'enabled' as const,
    adapter_config_ref: null,
    created_at: '2026-07-29T00:00:00Z',
    updated_at: '2026-07-29T00:00:00Z',
  },
]

const groupConversation = {
  id: conversationId,
  project_id: projectId,
  agent_id: null,
  title: '并发检查',
  conversation_type: 'group' as const,
  status: 'idle' as const,
  participants: agents.map(({ id, name, agent_type, capabilities, status }) => ({
    id,
    name,
    agent_type,
    capabilities,
    status,
  })),
  created_at: '2026-07-29T00:00:00Z',
  updated_at: '2026-07-29T00:00:00Z',
}

class MockWebSocket {
  static OPEN = 1
  static instances: MockWebSocket[] = []
  private listeners = new Map<string, ((event: Event | MessageEvent<string>) => void)[]>()

  constructor() {
    MockWebSocket.instances.push(this)
  }

  addEventListener(type: string, listener: (event: Event | MessageEvent<string>) => void) {
    const listeners = this.listeners.get(type) ?? []
    listeners.push(listener)
    this.listeners.set(type, listeners)
  }

  close() { return undefined }

  emitMessage(value: object) {
    const event = new MessageEvent('message', { data: JSON.stringify(value) })
    this.listeners.get('message')?.forEach((listener) => { listener(event) })
  }
}

function response(value: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(value), { status }))
}

function renderWorkbench() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={queryClient}><MemoryRouter><WorkbenchPage /></MemoryRouter></QueryClientProvider>)
}

beforeEach(() => {
  MockWebSocket.instances = []
  vi.stubGlobal('WebSocket', MockWebSocket)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('WorkbenchPage', () => {
  it('新建单聊固定提供三种运行提供方且不暴露 Agent 管理', async () => {
    let submittedBody: unknown = null
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url.endsWith('/conversations') && init?.method === 'POST') {
        submittedBody = JSON.parse(init.body as string)
        return response({
          ...conversation,
          id: '00000000-0000-0000-0000-000000000099',
          agent_name: 'DeepSeek',
          title: '新对话',
        }, 201)
      }
      if (url.endsWith('/agents')) return response(agents)
      if (url.endsWith('/conversations')) return response([conversation])
      if (url.endsWith('/messages')) return response([])
      return response({})
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    renderWorkbench()
    await screen.findByRole('heading', { name: '登录问题' })

    expect(screen.queryByRole('button', { name: '管理 Agent' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '新建会话' }))
    expect(screen.getByRole('radio', { name: /DeepSeek/ })).toBeInTheDocument()
    expect(screen.getAllByRole('radio')).toHaveLength(1)
    expect(screen.queryByLabelText('Agent 名称')).not.toBeInTheDocument()
    await user.click(screen.getByRole('radio', { name: /DeepSeek/ }))
    await user.click(screen.getByRole('button', { name: '创建' }))

    await waitFor(() => {
      expect(submittedBody).toEqual({
        title: null,
        conversation_type: 'direct',
        provider: 'deepseek',
      })
    })
    await waitFor(() => {
      expect(screen.getAllByText('DeepSeek').length).toBeGreaterThan(0)
    })
  })

  it('显示空会话并支持打开新建表单', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => {
      if (url.includes('/conversations')) return response([])
      return response({ status: 'ready', service: 'AgentHub API', checks: { configuration: 'ok' } })
    }))
    const user = userEvent.setup()
    renderWorkbench()
    expect(await screen.findByRole('heading', { name: '暂无活动会话' })).toBeInTheDocument()
    const newConversationButtons = screen.getAllByRole('button', { name: '新建会话' })
    const mainButton = newConversationButtons[1]
    if (mainButton === undefined) throw new Error('缺少主新建会话按钮')
    await user.click(mainButton)
    expect(screen.getByRole('heading', { name: '暂无活动会话' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '创建' })).toBeInTheDocument()
    expect(screen.queryByLabelText('标题')).not.toBeInTheDocument()
  })

  it('加载会话、发送消息并按 event_id 去重增量', async () => {
    let messages: unknown[] = []
    vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url.endsWith('/conversations')) return response([conversation])
      if (url.endsWith('/messages') && init?.method === 'POST') {
        const body = JSON.parse(init.body as string) as { content: string }
        const message = { id: '00000000-0000-0000-0000-000000000005', conversation_id: conversationId, project_id: projectId, parent_message_id: null, role: 'user', agent_id: null, content: body.content, content_type: 'markdown', sequence: 0, created_at: '2026-07-29T00:00:00Z' }
        messages = [message]
        return response({ message, execution: { id: executionId, project_id: projectId, message_id: message.id, agent_id: agentId, conversation_id: conversationId, status: 'pending', sequence: -1, error_code: null, error_message: null, started_at: null, completed_at: null, created_at: message.created_at } }, 202)
      }
      if (url.endsWith('/messages')) return response(messages)
      return response({})
    }))
    const user = userEvent.setup()
    renderWorkbench()
    await screen.findByRole('heading', { name: '登录问题' })
    await user.type(screen.getByLabelText('输入消息'), '请检查登录')
    await user.click(screen.getByRole('button', { name: '发送消息' }))
    expect(await screen.findByText('请检查登录')).toBeInTheDocument()
    expect(screen.getByText('等待内容…')).toBeInTheDocument()
  })

  it('新会话使用默认标题，首条消息后刷新为问题摘要', async () => {
    let conversations = [conversation]
    vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url.endsWith('/conversations') && init?.method === 'POST') return response({ ...conversation, title: '新对话' }, 201)
      if (url.endsWith('/conversations') && init?.method !== 'POST') return response(conversations)
      if (url.endsWith('/messages') && init?.method === 'POST') {
        conversations = [{ ...conversation, title: '如何修复登录问题' }]
        const message = { id: '00000000-0000-0000-0000-000000000005', conversation_id: conversationId, project_id: projectId, parent_message_id: null, role: 'user', agent_id: null, content: '如何修复登录问题', content_type: 'markdown', sequence: 0, created_at: '2026-07-29T00:00:00Z' }
        return response({ message, execution: { id: executionId, project_id: projectId, message_id: message.id, agent_id: agentId, conversation_id: conversationId, status: 'pending', sequence: -1, error_code: null, error_message: null, started_at: null, completed_at: null, created_at: message.created_at } }, 202)
      }
      if (url.endsWith('/messages')) return response([])
      return response({})
    }))
    const user = userEvent.setup()
    renderWorkbench()
    await screen.findByRole('heading', { name: '登录问题' })
    await user.click(screen.getByRole('button', { name: '新建会话' }))
    await user.click(screen.getByRole('button', { name: '创建' }))
    expect(await screen.findByRole('heading', { name: '新对话' })).toBeInTheDocument()
    await user.type(screen.getByLabelText('输入消息'), '如何修复登录问题')
    await user.click(screen.getByRole('button', { name: '发送消息' }))
    await waitFor(() => { expect(screen.getByRole('heading', { name: '如何修复登录问题' })).toBeInTheDocument() })
  })

  it('切换会话时不串接或丢失其他会话的流状态', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url.endsWith('/conversations')) return response([conversation, secondConversation])
      if (url.endsWith('/messages') && init?.method === 'POST') {
        const message = { id: '00000000-0000-0000-0000-000000000005', conversation_id: conversationId, project_id: projectId, parent_message_id: null, role: 'user', agent_id: null, content: '检查隔离', content_type: 'markdown', sequence: 0, created_at: '2026-07-29T00:00:00Z' }
        return response({ message, execution: { id: executionId, project_id: projectId, message_id: message.id, agent_id: agentId, conversation_id: conversationId, status: 'pending', sequence: -1, error_code: null, error_message: null, started_at: null, completed_at: null, created_at: message.created_at } }, 202)
      }
      if (url.endsWith('/messages')) return response([])
      return response({})
    }))
    const user = userEvent.setup()
    renderWorkbench()
    await screen.findByRole('heading', { name: '登录问题' })
    await user.type(screen.getByLabelText('输入消息'), '检查隔离')
    await user.click(screen.getByRole('button', { name: '发送消息' }))
    expect(await screen.findByText('等待内容…')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /第二个会话/ }))
    expect(screen.queryByText('等待内容…')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /登录问题/ }))
    expect(screen.getByText('等待内容…')).toBeInTheDocument()
  })

  it('显示 DeepSeek 并删除历史会话', async () => {
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url.endsWith('/conversations') && init?.method === 'DELETE') return response(undefined, 204)
      if (url.endsWith('/conversations')) return response([conversation, secondConversation])
      if (url.endsWith('/messages')) return response([])
      return response({})
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    renderWorkbench()
    await screen.findByRole('heading', { name: '登录问题' })
    expect(screen.getByText('DeepSeek', { selector: '.agent-badge' })).toBeInTheDocument()
    const deleteButtons = screen.getAllByRole('button', { name: '删除会话' })
    const firstDeleteButton = deleteButtons[0]
    if (firstDeleteButton === undefined) throw new Error('缺少删除会话按钮')
    await user.click(firstDeleteButton)
    await waitFor(() => { expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining(`/conversations/${conversationId}`), expect.objectContaining({ method: 'DELETE' })) })
  })

  it('失败时显示重试入口', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => {
      if (url.endsWith('/conversations')) return response([conversation])
      if (url.endsWith('/messages')) return response([], 503)
      return response({})
    }))
    renderWorkbench()
    await waitFor(() => { expect(screen.getByRole('alert')).toHaveTextContent('消息加载失败') })
    expect(screen.getByRole('button', { name: /重试/ })).toBeInTheDocument()
  })

  it('群聊提供 @ 建议并保持两个 Agent 的交错流内容独立', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url.endsWith('/agents')) return response(agents)
      if (url.endsWith('/conversations')) return response([groupConversation])
      if (url.endsWith('/messages') && init?.method === 'POST') {
        const body = JSON.parse(init.body as string) as { content: string }
        const userMessage = {
          id: '00000000-0000-0000-0000-000000000009',
          conversation_id: conversationId,
          project_id: projectId,
          parent_message_id: null,
          role: 'user',
          agent_id: null,
          content: body.content,
          content_type: 'markdown',
          sequence: 0,
          created_at: '2026-07-29T00:00:00Z',
        }
        const execution = (id: string, targetAgentId: string) => ({
          id,
          project_id: projectId,
          message_id: userMessage.id,
          agent_id: targetAgentId,
          conversation_id: conversationId,
          status: 'pending',
          sequence: -1,
          error_code: null,
          error_message: null,
          started_at: null,
          completed_at: null,
          created_at: userMessage.created_at,
        })
        return response({
          message: userMessage,
          executions: [execution(executionId, agentId), execution(secondExecutionId, secondAgentId)],
        }, 202)
      }
      if (url.endsWith('/messages')) return response([])
      return response({})
    }))
    const user = userEvent.setup()
    renderWorkbench()
    await screen.findByRole('heading', { name: '并发检查' })

    const input = screen.getByLabelText('输入消息')
    await user.type(input, '@Co')
    const suggestions = screen.getByRole('listbox', { name: '@Agent 建议' })
    expect(within(suggestions).getAllByRole('option')).toHaveLength(2)
    await user.keyboard('{Enter}')
    expect(input).toHaveValue('@Code ')

    await user.type(input, '@Coder 并发检查')
    await user.click(screen.getByRole('button', { name: '发送消息' }))
    await screen.findByText('@Code @Coder 并发检查')
    await waitFor(() => {
      expect(screen.getAllByText('等待内容…')).toHaveLength(2)
    })

    const socket = MockWebSocket.instances.at(-1)
    if (socket === undefined) throw new Error('WebSocket 未创建')
    const realtimeEvent = (
      id: string,
      targetExecutionId: string,
      sequence: number,
      delta: string,
    ) => ({
      event_id: id,
      conversation_id: conversationId,
      execution_id: targetExecutionId,
      sequence,
      type: 'content.delta',
      timestamp: '2026-07-29T00:00:00Z',
      payload: { delta, content_type: 'markdown' },
    })
    act(() => {
      socket.emitMessage(realtimeEvent('00000000-0000-0000-0000-000000000011', secondExecutionId, 1, 'Coder-1'))
      socket.emitMessage(realtimeEvent('00000000-0000-0000-0000-000000000012', executionId, 1, 'Code-1'))
      socket.emitMessage(realtimeEvent('00000000-0000-0000-0000-000000000013', secondExecutionId, 2, ' Coder-2'))
    })

    const rows = screen.getAllByText(/Code|Coder/, { selector: '.message-row--streaming strong' })
      .map((heading) => heading.closest('article'))
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('Code')
    expect(rows[0]).toHaveTextContent('Code-1')
    expect(rows[0]).not.toHaveTextContent('Coder-1')
    expect(rows[1]).toHaveTextContent('Coder')
    expect(rows[1]).toHaveTextContent('Coder-1 Coder-2')
    expect(rows[1]).not.toHaveTextContent('Code-1')
  })
})
