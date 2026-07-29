import { expect, test, type Page, type WebSocketRoute } from '@playwright/test'

const projectId = '00000000-0000-0000-0000-000000000001'
const agentId = '00000000-0000-0000-0000-000000000002'
const conversationId = '00000000-0000-0000-0000-000000000003'
const executionId = '00000000-0000-0000-0000-000000000004'

const conversation = {
  id: conversationId,
  project_id: projectId,
  agent_id: agentId,
  agent_name: 'DeepSeek',
  agent_type: 'openai_compatible',
  title: '交付检查',
  conversation_type: 'direct',
  created_at: '2026-07-29T00:00:00Z',
  updated_at: '2026-07-29T00:00:00Z',
}

type ConversationFixture = Omit<typeof conversation, 'title'> & { title: string | null }

function message(id: string, role: 'user' | 'agent', content: string, sequence: number) {
  return {
    id,
    conversation_id: conversationId,
    project_id: projectId,
    parent_message_id: null,
    role,
    agent_id: role === 'agent' ? agentId : null,
    content,
    content_type: 'markdown',
    sequence,
    created_at: '2026-07-29T00:00:00Z',
  }
}

function event(sequence: number, type: string, payload: object) {
  return JSON.stringify({
    event_id: `00000000-0000-0000-0000-${String(sequence + 10).padStart(12, '0')}`,
    conversation_id: conversationId,
    execution_id: executionId,
    sequence,
    type,
    timestamp: '2026-07-29T00:00:00Z',
    payload,
  })
}

async function mockChatApi(page: Page, initialConversations = [conversation]) {
  let conversations: ConversationFixture[] = [...initialConversations]
  let messages: ReturnType<typeof message>[] = []
  let socket: WebSocketRoute | undefined

  await page.routeWebSocket('**/ws/**', (route) => {
    socket = route
  })
  await page.route('**/api/v1/projects/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.pathname.endsWith('/conversations') && request.method() === 'GET') {
      await route.fulfill({ json: conversations })
      return
    }
    if (url.pathname.endsWith('/conversations') && request.method() === 'POST') {
      const created = { ...conversation, title: '新对话' }
      conversations = [created, ...conversations]
      await route.fulfill({ status: 201, json: created })
      return
    }
    if (/\/conversations\/[^/]+$/.exec(url.pathname) !== null && request.method() === 'DELETE') {
      conversations = conversations.filter((item) => item.id !== url.pathname.split('/').at(-1))
      await route.fulfill({ status: 204, body: '' })
      return
    }
    if (url.pathname.endsWith('/messages') && request.method() === 'GET') {
      await route.fulfill({ json: messages })
      return
    }
    if (url.pathname.endsWith('/messages') && request.method() === 'POST') {
      const body = request.postDataJSON() as { content: string }
      const userMessage = message('00000000-0000-0000-0000-000000000005', 'user', body.content, 0)
      messages = [userMessage]
      await route.fulfill({
        status: 202,
        json: {
          message: userMessage,
          execution: {
            id: executionId,
            project_id: projectId,
            message_id: userMessage.id,
            agent_id: agentId,
            conversation_id: conversationId,
            status: 'pending',
            sequence: -1,
            error_code: null,
            error_message: null,
            started_at: null,
            completed_at: null,
            created_at: userMessage.created_at,
          },
        },
      })
      return
    }
    if (url.pathname.endsWith('/cancel')) {
      const cancelled = JSON.parse(
        event(4, 'execution.status', { status: 'cancelled', message: null }),
      ) as unknown
      await route.fulfill({ json: cancelled })
      return
    }
    await route.fulfill({ status: 404, json: { detail: 'Not found' } })
  })

  return {
    socket: () => {
      if (socket === undefined) throw new Error('WebSocket 尚未连接')
      return socket
    },
    persistAgentReply: (content: string) => {
      messages = [...messages, message('00000000-0000-0000-0000-000000000006', 'agent', content, 1)]
    },
  }
}

test('空会话可新建并切换', async ({ page }) => {
  await mockChatApi(page, [])
  await page.goto('/')

  await expect(page.getByRole('heading', { name: '暂无活动会话' })).toBeVisible()
  const viewport = page.viewportSize()
  if (viewport !== null && viewport.width <= 720) {
    await page.getByRole('button', { name: '打开会话列表' }).click()
  }
  await page.getByRole('button', { name: '新建会话' }).first().click()
  await page.getByRole('button', { name: '创建' }).click()

  await expect(page.getByRole('heading', { name: '新对话' })).toBeVisible()
  await expect(page.getByText('发送第一条消息开始协作')).toBeVisible()
})

test('发送、流式去重、断连重连和取消执行', async ({ page }) => {
  const backend = await mockChatApi(page)
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '交付检查' })).toBeVisible()
  await expect(page.getByText('已连接').last()).toBeVisible()

  await page.getByLabel('输入消息').fill('检查实现')
  await page.getByLabel('输入消息').press('Enter')
  await expect(page.getByText('检查实现')).toBeVisible()

  backend.socket().send(event(0, 'execution.status', { status: 'running', message: null }))
  const firstDelta = event(1, 'content.delta', { delta: '正在检查', content_type: 'markdown' })
  backend.socket().send(firstDelta)
  backend.socket().send(firstDelta)
  await expect(page.getByText('正在检查')).toHaveCount(1)

  await backend.socket().close()
  await expect(page.getByText(/连接已断开|重连中/).last()).toBeVisible()
  await expect.poll(() => page.locator('.connection-status--connected').count()).toBeGreaterThan(0)

  await page.getByRole('button', { name: '取消' }).click()
  await expect(page.getByText('已取消')).toBeVisible()
})

test('长 Markdown 和代码块限制在消息区内', async ({ page }) => {
  const backend = await mockChatApi(page)
  const longToken = 'LONG_TOKEN_'.repeat(80)
  backend.persistAgentReply(`# 检查结果\n\n| 文件 | 状态 |\n| --- | --- |\n| app.ts | 通过 |\n\n\`\`\`typescript\nconst value = '${longToken}'\n\`\`\``)
  await page.goto('/')

  await expect(page.getByRole('heading', { name: '检查结果' })).toBeVisible()
  const overflow = await page.locator('.message-row--agent').evaluate((element) => {
    const pageWidth = document.documentElement.clientWidth
    const rect = element.getBoundingClientRect()
    const pre = element.querySelector('pre')
    return {
      rowInsideViewport: rect.left >= 0 && rect.right <= pageWidth,
      codeScrollable: pre !== null && pre.scrollWidth > pre.clientWidth,
      documentOverflow: document.documentElement.scrollWidth > pageWidth,
    }
  })
  expect(overflow.rowInsideViewport).toBe(true)
  expect(overflow.codeScrollable).toBe(true)
  expect(overflow.documentOverflow).toBe(false)
})

test('消息区内容过长时保持独立滚动', async ({ page }) => {
  const backend = await mockChatApi(page)
  backend.persistAgentReply('长内容\n\n'.repeat(40))
  await page.goto('/')
  const list = page.locator('.message-list')
  await expect(list).toBeVisible()
  await expect.poll(async () => list.evaluate((element) => element.scrollHeight > element.clientHeight)).toBe(true)
  await expect(page.getByRole('button', { name: '回到最新消息' })).toBeVisible()
  await page.getByRole('button', { name: '回到最新消息' }).click()
  await expect.poll(async () => list.evaluate((element) => element.scrollHeight - element.scrollTop - element.clientHeight)).toBeLessThanOrEqual(1)
  await expect(page.getByRole('button', { name: '回到最新消息' })).toBeHidden()
  await list.evaluate((element) => { element.scrollTop = 0; element.dispatchEvent(new Event('scroll')) })
  await expect(page.getByRole('button', { name: '回到最新消息' })).toBeVisible()
  const input = page.getByLabel('输入消息')
  await expect(input).toBeVisible()
  const layout = await page.locator('.conversation-panel').evaluate((panel) => {
    const list = panel.querySelector<HTMLElement>('.message-list')
    const composer = panel.querySelector<HTMLElement>('.composer')
    if (list === null || composer === null) throw new Error('会话布局结构不完整')
    const listRect = list.getBoundingClientRect()
    const composerRect = composer.getBoundingClientRect()
    window.scrollTo(0, document.documentElement.scrollHeight)
    return {
      documentScrollY: window.scrollY,
      composerInsideViewport: composerRect.top >= 0 && composerRect.bottom <= window.innerHeight,
      listEndsBeforeComposer: listRect.bottom <= composerRect.top,
    }
  })
  expect(layout.documentScrollY).toBe(0)
  expect(layout.composerInsideViewport).toBe(true)
  expect(layout.listEndsBeforeComposer).toBe(true)
})
