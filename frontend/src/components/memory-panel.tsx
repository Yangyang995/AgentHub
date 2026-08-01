import { useQuery } from '@tanstack/react-query'
import { Brain, Pin, X } from 'lucide-react'
import { listSummaries, type ConversationSummary } from '../api/knowledge'

interface MemoryPanelProps {
  projectId: string
  conversationId: string
  onClose?: () => void
}

export function MemoryPanel({ projectId, conversationId, onClose }: MemoryPanelProps) {
  const { data: summaries, isLoading } = useQuery({
    queryKey: ['summaries', projectId, conversationId],
    queryFn: () => listSummaries(projectId, conversationId),
    enabled: !!conversationId,
  })

  const latestFullMerge = summaries
    ?.filter((s: ConversationSummary) => s.is_full_merge)
    .slice(-1)[0]

  return (
    <div className="memory-panel">
      <div className="panel-header">
        <h3>
          <Brain size={18} /> 会话记忆
        </h3>
        <button onClick={onClose} className="icon-btn" title="关闭">
          <X size={18} />
        </button>
      </div>

      {isLoading ? (
        <p className="loading-text">加载中...</p>
      ) : (
        <>
          {/* 最新摘要 */}
          {latestFullMerge ? (
            <div className="current-summary">
              <div className="summary-label">
                <Pin size={14} /> 上次会话摘要
              </div>
              <p className="summary-text">{latestFullMerge.summary}</p>
              <span className="summary-meta">
                轮次 {latestFullMerge.round_start}-{latestFullMerge.round_end}
                {' · '}
                {new Date(latestFullMerge.created_at).toLocaleDateString('zh-CN')}
              </span>
            </div>
          ) : (
            <p className="empty-text">暂无摘要——继续对话后自动生成</p>
          )}

          {/* 历史摘要列表 */}
          {summaries && summaries.length > 0 && (
            <div className="history-summaries">
              <h4>历史摘要 ({summaries.length})</h4>
              <div className="summary-list">
                {summaries.map((s: ConversationSummary) => (
                  <div
                    key={s.id}
                    className={`summary-card ${s.is_full_merge ? 'full-merge' : ''}`}
                  >
                    <div className="summary-card-header">
                      <span>轮次 {s.round_start}-{s.round_end}</span>
                      {s.is_full_merge && <span className="badge">校准</span>}
                    </div>
                    <p>{s.summary.slice(0, 150)}</p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}