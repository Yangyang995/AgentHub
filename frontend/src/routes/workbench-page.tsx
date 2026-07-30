import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowDown,
  Bot,
  CircleStop,
  Menu,
  MessageSquare,
  PanelLeftClose,
  Plus,
  RefreshCw,
  Send,
  Trash2,
  X,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import {
  cancelExecution,
  createConversation,
  deleteConversation,
  isGroupConversation,
  listAgents,
  listConversations,
  listMessages,
  submitMessage,
  submissionExecutions,
  workspaceConfig,
  type Agent,
  type ChatProvider,
  type Conversation,
  type ExecutionStatus,
  type Message,
  type RealtimeEvent,
} from '../api/chat'
import { ConnectionStatus } from '../components/connection-status'
import { MarkdownContent } from '../components/markdown-content'
import { useConversationEvents } from '../hooks/use-conversation-events'

interface StreamMessage {
  conversationId: string
  executionId: string
  agentId: string
  agentName: string
  content: string
  status: ExecutionStatus
  error?: string
}

const providerOptions: readonly { value: ChatProvider; label: string; detail: string }[] = [
  { value: 'deepseek', label: 'DeepSeek', detail: '模型 API' },
]

function statusLabel(status: ExecutionStatus) {
  const labels: Record<ExecutionStatus, string> = {
    pending: '等待执行',
    running: '正在回复',
    succeeded: '已完成',
    failed: '执行失败',
    cancelled: '已取消',
  }
  return labels[status]
}

export function WorkbenchPage() {
  const queryClient = useQueryClient()
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [newConversationOpen, setNewConversationOpen] = useState(false)
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [selectedProvider, setSelectedProvider] = useState<ChatProvider>('deepseek')
  const [streams, setStreams] = useState<Record<string, StreamMessage>>({})
  const [failedDraft, setFailedDraft] = useState<string | null>(null)
  const [showScrollToBottom, setShowScrollToBottom] = useState(false)
  const messageListRef = useRef<HTMLDivElement>(null)

  const conversations = useQuery({
    queryKey: ['conversations', workspaceConfig.projectId],
    queryFn: ({ signal }) => listConversations(signal),
  })

  const agents = useQuery({
    queryKey: ['agents', workspaceConfig.projectId],
    queryFn: ({ signal }) => listAgents(signal),
  })

  const selectedConversationId = activeConversationId ?? conversations.data?.[0]?.id ?? null

  const messages = useQuery({
    queryKey: ['messages', selectedConversationId],
    queryFn: ({ signal }) => {
      if (selectedConversationId === null) return Promise.resolve([])
      return listMessages(selectedConversationId, signal)
    },
    enabled: selectedConversationId !== null,
  })

  const activeConversation = conversations.data?.find(
    (conversation) => conversation.id === selectedConversationId,
  )

  const knownAgents = useMemo(() => {
    if (Array.isArray(agents.data)) return agents.data
    const inferred = new Map<string, Agent>()
    conversations.data?.forEach((conversation) => {
      if (!isGroupConversation(conversation) && conversation.agent_id !== null) {
        inferred.set(conversation.agent_id, {
          id: conversation.agent_id,
          project_id: conversation.project_id,
          name: conversation.agent_name ?? 'Agent',
          agent_type: conversation.agent_type ?? 'mock',
          capabilities: null,
          status: 'enabled',
          adapter_config_ref: null,
          created_at: conversation.created_at,
          updated_at: conversation.updated_at,
        })
      }
    })
    return [...inferred.values()]
  }, [agents.data, conversations.data])
  const agentsById = useMemo(
    () => new Map(knownAgents.map((agent) => [agent.id, agent])),
    [knownAgents],
  )

  function handleRealtimeEvent(event: RealtimeEvent) {
    setStreams((current) => {
      const existing = current[event.execution_id] ?? {
        conversationId: event.conversation_id,
        executionId: event.execution_id,
        agentId: '',
        agentName: 'Agent',
        content: '',
        status: 'pending' as const,
      }
      if (event.type === 'content.delta') {
        return {
          ...current,
          [event.execution_id]: { ...existing, content: existing.content + event.payload.delta },
        }
      }
      if (event.type === 'execution.error') {
        return {
          ...current,
          [event.execution_id]: { ...existing, status: 'failed', error: event.payload.error_message },
        }
      }
      if (event.type === 'execution.status') {
        return {
          ...current,
          [event.execution_id]: { ...existing, status: event.payload.status },
        }
      }
      return current
    })
    if (event.type === 'execution.status' && ['succeeded', 'failed', 'cancelled'].includes(event.payload.status)) {
      void queryClient.invalidateQueries({ queryKey: ['messages', event.conversation_id] })
    }
  }

  const { connection, followExecution } = useConversationEvents(
    selectedConversationId,
    handleRealtimeEvent,
  )

  const createMutation = useMutation({
    mutationFn: () => createConversation(selectedProvider),
    onSuccess: (conversation) => {
      queryClient.setQueryData(
        ['conversations', workspaceConfig.projectId],
        (current: typeof conversations.data) => [conversation, ...(current ?? [])],
      )
      setActiveConversationId(conversation.id)
      setNewConversationOpen(false)
      setSidebarOpen(false)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: deleteConversation,
    onSuccess: (_data, conversationId) => {
      queryClient.setQueryData<Conversation[]>(
        ['conversations', workspaceConfig.projectId],
        (current = []) => current.filter((conversation) => conversation.id !== conversationId),
      )
      if (selectedConversationId === conversationId) setActiveConversationId(null)
    },
  })

  const sendMutation = useMutation({
    mutationFn: ({ conversationId, content }: { conversationId: string; content: string }) => {
      return submitMessage(conversationId, content)
    },
    onSuccess: (submission, variables) => {
      const executions = submissionExecutions(submission)
      if (selectedConversationId === variables.conversationId) {
        executions.forEach((execution) => { followExecution(execution.id) })
      }
      queryClient.setQueryData<Message[]>(['messages', variables.conversationId], (current = []) => {
        if (current.some((message) => message.id === submission.message.id)) return current
        return [...current, submission.message]
      })
      setStreams((current) => {
        const next = { ...current }
        executions.forEach((execution) => {
          const agent = agentsById.get(execution.agent_id)
          next[execution.id] = {
            conversationId: variables.conversationId,
            executionId: execution.id,
            agentId: execution.agent_id,
            agentName: agent?.name ?? 'Agent',
            content: current[execution.id]?.content ?? '',
            status: current[execution.id]?.status ?? execution.status,
          }
        })
        return next
      })
      void queryClient.invalidateQueries({ queryKey: ['conversations', workspaceConfig.projectId] })
      setFailedDraft(null)
    },
    onError: (_error, variables) => {
      setFailedDraft(variables.content)
    },
  })

  const cancelMutation = useMutation({
    mutationFn: cancelExecution,
    onSuccess: handleRealtimeEvent,
  })

  function sendCurrentDraft() {
    const content = draft.trim()
    if (content === '' || selectedConversationId === null || sendMutation.isPending) return
    setDraft('')
    sendMutation.mutate({ conversationId: selectedConversationId, content })
  }

  const visibleStreams = Object.values(streams).filter((stream) => {
    if (stream.conversationId !== selectedConversationId) return false
    if (stream.status !== 'succeeded') return true
    return !messages.data?.some(
      (message) =>
        message.role === 'agent' &&
        message.agent_id === stream.agentId &&
        message.content === stream.content,
    )
  })
  const cancellableExecution = visibleStreams.find(
    (stream) => stream.status === 'pending' || stream.status === 'running',
  )

  const mentionSuggestions = useMemo(() => {
    if (activeConversation === undefined || !isGroupConversation(activeConversation)) return []
    const match = /(?:^|\s)@([^\s@]*)$/.exec(draft)
    if (match === null) return []
    const query = (match[1] ?? '').toLocaleLowerCase()
    return activeConversation.participants.filter(
      (agent) => agent.status === 'enabled' && agent.name.toLocaleLowerCase().startsWith(query),
    )
  }, [activeConversation, draft])

  function insertMention(name: string) {
    setDraft((current) => current.replace(/(?:^|\s)@[^\s@]*$/, (token) => `${token.startsWith(' ') ? ' ' : ''}@${name} `))
  }

  const updateScrollToBottomVisibility = useCallback(() => {
    const list = messageListRef.current
    if (list === null) return
    const distanceToBottom = list.scrollHeight - list.scrollTop - list.clientHeight
    setShowScrollToBottom(distanceToBottom > 96)
  }, [])

  useEffect(() => {
    const frame = window.requestAnimationFrame(updateScrollToBottomVisibility)
    return () => { window.cancelAnimationFrame(frame) }
  }, [messages.data, selectedConversationId, streams, updateScrollToBottomVisibility])

  function scrollToLatestMessage() {
    messageListRef.current?.scrollTo({ top: messageListRef.current.scrollHeight, behavior: 'smooth' })
  }

  return (
    <div className="workbench-shell">
      <header className="mobile-header">
        <button className="icon-button" type="button" aria-label="打开会话列表" title="打开会话列表" onClick={() => { setSidebarOpen(true); }}>
          <Menu aria-hidden="true" size={19} />
        </button>
        <strong>AgentHub</strong>
        <ConnectionStatus state={connection} />
      </header>

      {sidebarOpen ? <button className="sidebar-backdrop" type="button" aria-label="关闭会话列表" onClick={() => { setSidebarOpen(false); }} /> : null}

      <aside className={`workspace-sidebar${sidebarOpen ? ' workspace-sidebar--open' : ''}`}>
        <div className="brand-row">
          <div className="brand-mark" aria-hidden="true"><Bot size={19} /></div>
          <div><strong>AgentHub</strong><span>本地工作台</span></div>
          <button className="icon-button sidebar-close" type="button" aria-label="关闭会话列表" title="关闭会话列表" onClick={() => { setSidebarOpen(false); }}>
            <PanelLeftClose aria-hidden="true" size={18} />
          </button>
        </div>

        <div className="sidebar-actions">
          <span className="section-label">会话</span>
          <button className="icon-button" type="button" aria-label="新建会话" title="新建会话" onClick={() => { setNewConversationOpen(true); }}>
            <Plus aria-hidden="true" size={18} />
          </button>
        </div>

        {newConversationOpen ? (
          <form className="new-conversation-form" onSubmit={(event) => { event.preventDefault(); createMutation.mutate() }}>
            <div className="form-heading"><strong>新建会话</strong><button className="icon-button" type="button" aria-label="关闭新建会话" onClick={() => { setNewConversationOpen(false); }}><X aria-hidden="true" size={16} /></button></div>
            <p className="new-conversation-note">新会话初始名称为“新对话”，发送第一条消息后会自动生成短标题。</p>
            <fieldset className="provider-picker"><legend>运行提供方</legend>{providerOptions.map((provider) => <label key={provider.value} className='is-selected'><input type="radio" name="provider" value={provider.value} checked onChange={() => { setSelectedProvider(provider.value) }} /><span><strong>{provider.label}</strong><small>{provider.detail}</small></span></label>)}</fieldset>
            {createMutation.isError ? <p className="inline-error" role="alert">创建失败，请检查提供方和本地运行环境。</p> : null}
            <button className="primary-button" type="submit" disabled={createMutation.isPending}>{createMutation.isPending ? '创建中' : '创建'}</button>
          </form>
        ) : null}

        <nav className="conversation-nav" aria-label="会话列表">
          {conversations.isPending ? <p className="sidebar-note" role="status">正在加载会话…</p> : null}
          {conversations.isError ? <div className="sidebar-error" role="alert"><span>会话加载失败</span><button type="button" onClick={() => void conversations.refetch()}><RefreshCw aria-hidden="true" size={14} />重试</button></div> : null}
          {conversations.data?.length === 0 ? <div className="sidebar-empty"><MessageSquare aria-hidden="true" size={18} /><span>暂无会话</span></div> : null}
          {conversations.data?.map((conversation) => (
            <div key={conversation.id} className={`conversation-item${conversation.id === selectedConversationId ? ' conversation-item--active' : ''}`}>
              <MessageSquare aria-hidden="true" size={16} />
              <button className="conversation-item__select" type="button" onClick={() => { setActiveConversationId(conversation.id); setFailedDraft(null); setSidebarOpen(false) }}>
                <strong>{conversation.title ?? '新对话'}</strong><small>{new Date(conversation.updated_at).toLocaleString()}</small>
              </button>
              <button className="conversation-item__delete" type="button" aria-label="删除会话" title="删除会话" disabled={deleteMutation.isPending} onClick={() => { deleteMutation.mutate(conversation.id) }}>
                <Trash2 aria-hidden="true" size={14} />
              </button>
            </div>
          ))}
        </nav>
        <footer className="sidebar-footer"><ConnectionStatus state={connection} /><span>Phase 6</span></footer>
      </aside>

      <main className="workspace-main">
        <header className="workspace-toolbar">
          <div><span className="toolbar-context">{activeConversation && isGroupConversation(activeConversation) ? '群聊工作区' : '单聊工作区'}</span><h1>{activeConversation?.title ?? (activeConversation ? '新对话' : '选择会话')}</h1></div>
          {activeConversation ? <span className="agent-badge">{isGroupConversation(activeConversation) ? activeConversation.participants.map((agent) => agent.name).join(' · ') : activeConversation.agent_name ?? 'Agent'}</span> : null}
        </header>

        {selectedConversationId === null ? (
          <section className="empty-workspace" aria-labelledby="empty-title">
            <div className="empty-symbol" aria-hidden="true"><Bot size={28} /></div>
            <h2 id="empty-title">暂无活动会话</h2>
            <p>从左侧新建单聊会话开始</p>
            <button className="primary-button" type="button" onClick={() => { setNewConversationOpen(true); setSidebarOpen(true) }}><Plus aria-hidden="true" size={16} />新建会话</button>
          </section>
        ) : (
          <section className="conversation-panel" aria-label="当前会话">
            {connection !== 'connected' ? <div className="connection-notice" role="status"><ConnectionStatus state={connection} /><span>消息仍可查看，恢复后会自动补发遗漏事件。</span></div> : null}
            <div ref={messageListRef} className="message-list" aria-live="polite" aria-busy={messages.isPending} onScroll={updateScrollToBottomVisibility}>
              {messages.isPending ? <div className="center-state" role="status">正在加载消息…</div> : null}
              {messages.isError ? <div className="center-state" role="alert"><span>消息加载失败</span><button className="secondary-button" type="button" onClick={() => void messages.refetch()}><RefreshCw aria-hidden="true" size={15} />重试</button></div> : null}
              {messages.data?.length === 0 && visibleStreams.length === 0 ? <div className="center-state"><MessageSquare aria-hidden="true" size={22} /><span>发送第一条消息开始协作</span></div> : null}
              {messages.data?.map((message) => <MessageRow key={message.id} message={message} agentName={message.agent_id ? agentsById.get(message.agent_id)?.name : undefined} />)}
              {visibleStreams.map((stream) => (
                <article key={`execution-${stream.executionId}`} className="message-row message-row--agent message-row--streaming" data-status={stream.status}>
                  <header><Bot aria-hidden="true" size={15} /><strong>{stream.agentName}</strong><span>{statusLabel(stream.status)}</span></header>
                  {stream.content ? <MarkdownContent content={stream.content} /> : <p className="stream-placeholder">等待内容…</p>}
                  {stream.error ? <p className="inline-error" role="alert">{stream.error}</p> : null}
                </article>
              ))}
            </div>
            {showScrollToBottom ? (
              <button className="scroll-to-bottom" type="button" aria-label="回到最新消息" title="回到最新消息" onClick={scrollToLatestMessage}>
                <ArrowDown aria-hidden="true" size={20} />
              </button>
            ) : null}

            <form className="composer" onSubmit={(event) => { event.preventDefault(); sendCurrentDraft() }}>
              {sendMutation.isError ? <div className="composer-error" role="alert"><span>消息发送失败。</span><button type="button" onClick={() => { if (failedDraft !== null) sendMutation.mutate({ conversationId: selectedConversationId, content: failedDraft }); }}><RefreshCw aria-hidden="true" size={14} />重试</button></div> : null}
              <label className="sr-only" htmlFor="message-input">输入消息</label>
              <div className="composer-input-wrap">
                <textarea id="message-input" value={draft} rows={3} placeholder={activeConversation && isGroupConversation(activeConversation) ? '输入 @ 点名参与 Agent' : '输入消息，Enter 发送，Shift + Enter 换行'} onChange={(event) => { setDraft(event.target.value) }} onKeyDown={(event) => { if (event.key === 'Enter' && mentionSuggestions.length > 0) { event.preventDefault(); const first = mentionSuggestions[0]; if (first) insertMention(first.name); return } if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); sendCurrentDraft() } }} />
                {mentionSuggestions.length > 0 ? <div className="mention-suggestions" role="listbox" aria-label="@Agent 建议">{mentionSuggestions.map((agent) => <button type="button" role="option" aria-selected="false" key={agent.id} onMouseDown={(event) => { event.preventDefault(); insertMention(agent.name) }}><Bot aria-hidden="true" size={14} /><span>{agent.name}</span><small>{agent.agent_type}</small></button>)}</div> : null}
              </div>
              <div className="composer-toolbar">
                <div className="agent-select"><Bot aria-hidden="true" size={14} /><span>{activeConversation && isGroupConversation(activeConversation) ? `${String(activeConversation.participants.length)} 位参与者` : activeConversation?.agent_name ?? 'Agent'}</span></div>
                <div className="composer-actions">
                  {cancellableExecution ? <button className="secondary-button" type="button" disabled={cancelMutation.isPending} onClick={() => { cancelMutation.mutate(cancellableExecution.executionId); }}><CircleStop aria-hidden="true" size={16} />取消</button> : null}
                  <button className="send-button" type="submit" aria-label="发送消息" title="发送消息" disabled={draft.trim() === '' || sendMutation.isPending}><Send aria-hidden="true" size={17} /></button>
                </div>
              </div>
            </form>
          </section>
        )}
      </main>
    </div>
  )
}

function MessageRow({ message, agentName }: { message: Message; agentName?: string }) {
  return (
    <article className={`message-row message-row--${message.role}`}>
      <header><strong>{message.role === 'user' ? '你' : message.role === 'agent' ? agentName ?? 'Agent' : '系统'}</strong><time dateTime={message.created_at}>{new Date(message.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</time></header>
      <MarkdownContent content={message.content} />
    </article>
  )
}
