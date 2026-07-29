import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { WorkbenchPage } from '../src/routes/workbench-page'

const projectId = '00000000-0000-0000-0000-000000000001'
const agentId = '00000000-0000-0000-0000-000000000002'
const conversationId = '00000000-0000-0000-0000-000000000003'
const executionId = '00000000-0000-0000-0000-000000000004'
const secondConversationId = '00000000-0000-0000-0000-000000000006'

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

function response(value: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(value), { status }))
}

function renderWorkbench() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={queryClient}><MemoryRouter><WorkbenchPage /></MemoryRouter></QueryClientProvider>)
}

beforeEach(() => {
  vi.stubGlobal('WebSocket', class MockWebSocket {
    static OPEN = 1
    addEventListener() { /* 由测试在需要时替换 */ }
    close() { return undefined }
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('WorkbenchPage', () => {
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
    expect(screen.getByRole('option', { name: 'DeepSeek' })).toBeInTheDocument()
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
})
