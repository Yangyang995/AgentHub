import { expect, test } from '@playwright/test'

test('工作台基础布局可访问', async ({ page }, testInfo) => {
  await page.route('**/health/ready', async (route) => {
    await route.fulfill({
      json: {
        status: 'ready',
        service: 'AgentHub API',
        checks: { configuration: 'ok' },
      },
    })
  })

  await page.goto('/')

  await expect(page.getByRole('heading', { name: '暂无活动会话' })).toBeVisible()
  await expect(page.getByRole('navigation', { name: '会话列表' })).toBeVisible()
  // 桌面端状态位于侧栏，移动端状态位于顶部；按项目选择对应语义区域，避免依赖重复文本顺序。
  const status = testInfo.project.name.startsWith('mobile')
    ? page.locator('.mobile-header .health-status--ready')
    : page.locator('.sidebar-footer .health-status--ready')
  await expect(status).toBeVisible()
})
