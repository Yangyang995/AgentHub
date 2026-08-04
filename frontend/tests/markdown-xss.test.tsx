import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MarkdownContent } from '../src/components/markdown-content'

describe('Markdown XSS 防护', () => {
  it('应将 script 标签 HTML 编码为纯文本', () => {
    const xssPayload = '<script>alert("xss")</script>'
    render(MarkdownContent({ content: xssPayload }))

    const container = screen.getByTestId('markdown-content')
    // react-markdown 默认 HTML 编码危险标签——验证没有真实 DOM 节点
    expect(container.querySelector('script')).toBeNull()
    // 文本内容应以编码形式出现（纯文本，不执行）
    expect(container.textContent).toContain('<script>')
  })

  it('不应渲染 javascript: 协议的可点击链接', () => {
    const xssPayload = '[点击这里](javascript:alert("xss"))'
    render(MarkdownContent({ content: xssPayload }))

    const container = screen.getByTestId('markdown-content')
    const links = container.querySelectorAll('a')
    for (const link of links) {
      const href = link.getAttribute('href')
      if (href !== null) {
        expect(href).not.toMatch(/^javascript:/i)
      }
    }
  })

  it('应将 HTML 事件处理器编码为纯文本', () => {
    const xssPayloads = [
      '<img src=x onerror="alert(1)">',
      '<div onmouseover="alert(2)">悬停</div>',
    ]

    for (const payload of xssPayloads) {
      const { container } = render(MarkdownContent({ content: payload }))
      // 不应有真实 img 标签（被编码为文本）
      expect(container.querySelector('img')).toBeNull()
      // 不应有真实 div 标签
      expect(container.querySelector('div.onmouseover')).toBeNull()
    }
  })

  it('应安全渲染普通 Markdown', () => {
    const normalContent = '# 标题\n\n**粗体**\n\n- 列表项\n\n[安全链接](https://example.com)'
    render(MarkdownContent({ content: normalContent }))

    const container = screen.getByTestId('markdown-content')
    expect(container.textContent).toContain('标题')
    expect(container.innerHTML).toContain('粗体')
    expect(container.textContent).toContain('列表项')
  })

  it('应处理空内容', () => {
    render(MarkdownContent({ content: '' }))
    const container = screen.getByTestId('markdown-content')
    expect(container.textContent).toBe('')
  })

  it('应转义原生 HTML 标签', () => {
    const contentWithHtml = '<div>这是HTML</div> & <span>更多</span>'
    render(MarkdownContent({ content: contentWithHtml }))

    const container = screen.getByTestId('markdown-content')
    // react-markdown 默认不渲染原生 HTML——div/span 不应成为 DOM 节点
    expect(container.querySelector('div')).toBeNull()
    expect(container.querySelector('span')).toBeNull()
  })
})
