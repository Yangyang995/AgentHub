import { Bot, Menu, MessageSquare, PanelLeftClose, Search } from 'lucide-react'
import { useState } from 'react'

import { HealthStatus } from '../components/health-status'

export function WorkbenchPage() {
  const [sidebarOpen, setSidebarOpen] = useState(false)

  function closeSidebar() {
    setSidebarOpen(false)
  }

  return (
    <div className="workbench-shell">
      <header className="mobile-header">
        <button
          className="icon-button"
          type="button"
          aria-label="打开会话列表"
          title="打开会话列表"
          onClick={() => {
            setSidebarOpen(true)
          }}
        >
          <Menu aria-hidden="true" size={19} />
        </button>
        <span className="mobile-brand">AgentHub</span>
        <HealthStatus />
      </header>

      {sidebarOpen ? (
        <button
          className="sidebar-backdrop"
          type="button"
          aria-label="关闭会话列表"
          onClick={closeSidebar}
        />
      ) : null}

      <aside className={`workspace-sidebar${sidebarOpen ? ' workspace-sidebar--open' : ''}`}>
        <div className="brand-row">
          <div className="brand-mark" aria-hidden="true">
            <Bot size={19} strokeWidth={1.8} />
          </div>
          <div>
            <strong>AgentHub</strong>
            <span>本地工作台</span>
          </div>
          <button
            className="icon-button sidebar-close"
            type="button"
            aria-label="关闭会话列表"
            title="关闭会话列表"
            onClick={closeSidebar}
          >
            <PanelLeftClose aria-hidden="true" size={18} />
          </button>
        </div>

        <label className="search-field">
          <Search aria-hidden="true" size={16} />
          <span className="sr-only">搜索会话</span>
          <input type="search" placeholder="搜索会话" disabled />
        </label>

        <nav className="conversation-nav" aria-label="会话列表">
          <span className="section-label">会话</span>
          <div className="sidebar-empty">
            <MessageSquare aria-hidden="true" size={18} />
            <span>暂无会话</span>
          </div>
        </nav>

        <footer className="sidebar-footer">
          <HealthStatus />
          <span>Phase 1</span>
        </footer>
      </aside>

      <main className="workspace-main">
        <header className="workspace-toolbar">
          <div>
            <span className="toolbar-context">工作区</span>
            <h1>当前会话</h1>
          </div>
          <span className="local-badge">LOCAL</span>
        </header>

        <section className="empty-workspace" aria-labelledby="empty-title">
          <div className="empty-symbol" aria-hidden="true">
            <Bot size={28} strokeWidth={1.5} />
          </div>
          <h2 id="empty-title">暂无活动会话</h2>
          <p>工作区已就绪</p>
        </section>
      </main>
    </div>
  )
}
