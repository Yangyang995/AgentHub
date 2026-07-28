import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { WorkbenchPage } from '../src/routes/workbench-page'

function renderWorkbench() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <WorkbenchPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('WorkbenchPage', () => {
  it('展示工作台基础语义结构和 API 就绪状态', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            status: 'ready',
            service: 'AgentHub API',
            checks: { configuration: 'ok' },
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    )

    renderWorkbench()

    expect(screen.getByRole('navigation', { name: '会话列表' })).toBeInTheDocument()
    expect(screen.getByRole('main')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '暂无活动会话' })).toBeInTheDocument()
    expect((await screen.findAllByText('API 就绪')).length).toBeGreaterThan(0)
  })

  it('在后端不可达时显示离线状态', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network error')))

    renderWorkbench()

    expect((await screen.findAllByText('API 离线')).length).toBeGreaterThan(0)
  })

  it('移动端会话列表按钮可打开并关闭侧栏', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network error')))
    const user = userEvent.setup()

    renderWorkbench()
    await user.click(screen.getByRole('button', { name: '打开会话列表' }))

    const sidebar = screen.getByRole('complementary')
    expect(sidebar).toHaveClass('workspace-sidebar--open')

    await user.click(screen.getByTitle('关闭会话列表'))
    expect(sidebar).not.toHaveClass('workspace-sidebar--open')
  })
})
