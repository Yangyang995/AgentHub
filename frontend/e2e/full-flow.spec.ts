import { expect, test, type Page, type WebSocketRoute } from '@playwright/test'

const projectId = '00000000-0000-0000-0000-000000000001'
const deepseekAgentId = '00000000-0000-0000-0000-000000000002'
const codeAgentId = '00000000-0000-0000-0000-000000000007'
const conversationId = '00000000-0000-0000-0000-000000000003'
const executionId = '00000000-0000-0000-0000-000000000004'

function event(type: string, execId: string, seq: number, payload: Record<string, unknown>) {
  return JSON.stringify({ event_id: `00000000-0000-0000-0000-${String(seq + 10).padStart(12, '0')}`, conversation_id: conversationId, execution_id: execId, sequence: seq, type, timestamp: '2026-08-04T00:00:00Z', payload })
}

async function setupBaseRoutes(page: Page) {
  await page.route('**/health/**', async (route) => { await route.fulfill({ json: { status: 'alive' } }) })
  await page.route('**/api/v1/projects', async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({ json: [{ id: projectId, name: 'E2E项目', root_path: '/tmp', description: '', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' }] })
    } else { await route.fulfill({ status: 404 }) }
  })
  await page.route('**/api/v1/projects/*/agents', async (route) => {
    await route.fulfill({ json: [{ id: deepseekAgentId, project_id: projectId, name: 'DeepSeek', agent_type: 'openai_compatible', capabilities: null, status: 'enabled', adapter_config_ref: null, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' }] })
  })
  await page.route('**/api/v1/projects/*/knowledge/**', async (route) => { await route.fulfill({ json: { results: [], total: 0 } }) })
  await page.route('**/api/v1/projects/*/approvals', async (route) => { await route.fulfill({ json: [] }) })
  await page.route('**/api/v1/projects/*/deployments', async (route) => { await route.fulfill({ json: [] }) })
}

test.describe('全流程 E2E', () => {

  test('单聊——发送消息并接收流式回复', async ({ page }) => {
    await setupBaseRoutes(page)
    let socket: WebSocketRoute | undefined
    await page.routeWebSocket('**/ws/**', (route) => { socket = route })

    const conversations = [{
      id: conversationId, project_id: projectId, agent_id: deepseekAgentId,
      agent_name: 'DeepSeek', agent_type: 'openai_compatible', title: '测试对话',
      conversation_type: 'direct', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    }]

    await page.route('**/api/v1/projects/*/conversations', async (route) => {
      const req = route.request()
      if (req.method() === 'GET') { await route.fulfill({ json: conversations }); return }
      if (req.method() === 'POST') {
        await route.fulfill({ status: 201, json: { ...conversations[0], title: '新对话' } })
        return
      }
      await route.fulfill({ status: 404 })
    })
    await page.route('**/api/v1/projects/*/conversations/*/messages', async (route) => {
      const req = route.request()
      if (req.method() === 'GET') { await route.fulfill({ json: [] }); return }
      if (req.method() === 'POST') {
        const userMsg = { id: 'msg-001', conversation_id: conversationId, project_id: projectId, role: 'user', agent_id: null, content: '你好', content_type: 'markdown', sequence: 0, created_at: '2026-01-01T00:00:00Z' }
        await route.fulfill({ status: 202, json: { message: userMsg, execution: { id: executionId, project_id: projectId, message_id: userMsg.id, agent_id: deepseekAgentId, conversation_id: conversationId, status: 'pending', sequence: -1 } } })
        return
      }
      await route.fulfill({ status: 404 })
    })

    await page.goto('/')
    await page.getByLabel('输入消息').fill('你好，世界')
    await page.getByRole('button', { name: '发送消息' }).click()
    await page.waitForTimeout(200)

    if (socket === undefined) throw new Error('WS 未连接')
    socket.send(event('content.delta', executionId, 1, { delta: '你好！我是 AgentHub 助手。', content_type: 'markdown' }))
    socket.send(event('execution.status', executionId, 2, { status: 'succeeded', message: null }))

    await expect(page.locator('.message-row--streaming')).toContainText('你好！我是 AgentHub 助手。', { timeout: 5000 })
  })

  test('Orchestrator——隐式拆解展示计划', async ({ page }) => {
    await setupBaseRoutes(page)
    let socket: WebSocketRoute | undefined
    await page.routeWebSocket('**/ws/**', (route) => { socket = route })

    const agents = [
      { id: codeAgentId, project_id: projectId, name: '需求分析专家', agent_type: 'openai_compatible', capabilities: ['requirement_analysis'], status: 'enabled', adapter_config_ref: 'requirement_analysis', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' },
    ]
    await page.route('**/api/v1/projects/*/agents', async (route) => { await route.fulfill({ json: agents }) })

    const groupConv = {
      id: conversationId, project_id: projectId, agent_id: null, title: '自动拆解', conversation_type: 'group', status: 'idle',
      participants: agents.map(a => ({ id: a.id, name: a.name, agent_type: a.agent_type, capabilities: a.capabilities, status: a.status })),
      created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    }

    await page.route('**/api/v1/projects/*/conversations', async (route) => {
      if (route.request().method() === 'GET') { await route.fulfill({ json: [groupConv] }); return }
      await route.fulfill({ status: 404 })
    })
    await page.route('**/api/v1/projects/*/conversations/*/messages', async (route) => {
      const req = route.request()
      if (req.method() === 'GET') { await route.fulfill({ json: [] }); return }
      if (req.method() === 'POST') {
        const userMsg = { id: 'msg-003', conversation_id: conversationId, project_id: projectId, role: 'user', agent_id: null, content: '设计登录系统', content_type: 'markdown', sequence: 0, created_at: '2026-01-01T00:00:00Z' }
        await route.fulfill({ status: 202, json: { message: userMsg, pipeline: true, executions: [{ id: executionId, project_id: projectId, message_id: userMsg.id, agent_id: codeAgentId, conversation_id: conversationId, status: 'pending', sequence: -1 }] } })
        return
      }
      await route.fulfill({ status: 404 })
    })

    await page.goto('/')
    await page.getByLabel('输入消息').fill('设计一个用户登录系统')
    await page.getByRole('button', { name: '发送消息' }).click()
    await page.waitForTimeout(200)

    if (socket === undefined) throw new Error('WS 未连接')
    socket.send(event('orchestrator.plan', executionId, 1, { plan: [{ task: '需求分析', agent: '需求分析专家' }, { task: '架构设计', agent: '架构设计专家' }] }))
    await expect(page.locator('.message-row--streaming')).toContainText('需求分析', { timeout: 5000 })
  })
})
