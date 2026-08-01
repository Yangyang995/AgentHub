import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  FileText,
  Search,
  Trash2,
  Upload,
  X,
  Loader2,
  FileCode,
  FileSpreadsheet,
  FileType,
} from 'lucide-react'
import { useRef, useState } from 'react'
import {
  deleteKnowledgeFile,
  fuzzySearchKnowledge,
  listKnowledgeFiles,
  searchKnowledge,
  uploadKnowledgeFiles,
  type KnowledgeFile,
  type KnowledgeSearchResult,
} from '../api/knowledge'

interface KnowledgePanelProps {
  projectId: string
  onClose?: () => void
  onToast?: (type: string, message: string) => void
}

/** 文件类型图标映射 */
function fileIcon(fileType: string) {
  switch (fileType) {
    case 'pdf': return <FileText size={16} />
    case 'xlsx': case 'xls': case 'csv': return <FileSpreadsheet size={16} />
    case 'py': case 'ts': case 'tsx': case 'js': case 'jsx':
    case 'rs': case 'go': case 'java': return <FileCode size={16} />
    default: return <FileType size={16} />
  }
}

export function KnowledgePanel({ projectId, onClose, onToast }: KnowledgePanelProps) {
  const queryClient = useQueryClient()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<KnowledgeSearchResult[] | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<{ fileId: string; fileName: string } | null>(null)
  const [isSearching, setIsSearching] = useState(false)

  const { data: files, isLoading } = useQuery({
    queryKey: ['knowledge-files', projectId],
    queryFn: () => listKnowledgeFiles(projectId),
    staleTime: 0,
    refetchInterval: false,
  })

  const uploadMutation = useMutation({
    mutationFn: (files: File[]) => uploadKnowledgeFiles(projectId, files),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['knowledge-files', projectId] })
      queryClient.refetchQueries({ queryKey: ['knowledge-files', projectId] })
      const created = data.filter((r: { chunks_created: number; chunks_skipped: number }) => r.chunks_created > 0)
      const skipped = data.filter((r: { chunks_created: number; chunks_skipped: number }) => r.chunks_created === 0 && r.chunks_skipped > 0)
      if (created.length === 0 && skipped.length > 0) {
        onToast?.('info', skipped.length + ' 个文件已存在，已跳过')
      } else if (created.length > 0) {
        const msg = created.length + ' 个文件上传成功'
        onToast?.('success', skipped.length > 0 ? msg + '，' + skipped.length + ' 个已跳过' : msg)
      }
    },
    onError: (err: Error) => {
      onToast?.('error', err.message)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (fileId: string) => deleteKnowledgeFile(projectId, fileId),
    onSuccess: (_data, fileId) => {
      queryClient.invalidateQueries({ queryKey: ['knowledge-files', projectId] })
      queryClient.refetchQueries({ queryKey: ['knowledge-files', projectId] })
      setSearchResults((prev) => prev ? prev.filter((r) => r.file_id !== fileId) : null)
      onToast?.('success', '文件已删除')
    },
  })

  const handleUpload = () => {
    fileInputRef.current?.click()
  }

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files
    if (selected && selected.length > 0) {
      uploadMutation.mutate(Array.from(selected))
      e.target.value = ''
    }
  }

  const handleSearch = async () => {
    if (!searchQuery.trim()) return
    setIsSearching(true)
    try {
      const results = await fuzzySearchKnowledge(projectId, searchQuery.trim())
      setSearchResults(results)
    } catch {
      setSearchResults([])
    } finally {
      setIsSearching(false)
    }
  }

  const handleDelete = (fileId: string, fileName: string) => {
    setDeleteTarget({ fileId, fileName })
  }

  const confirmDelete = () => {
    if (deleteTarget) {
      deleteMutation.mutate(deleteTarget.fileId)
      setDeleteTarget(null)
    }
  }

  const cancelDelete = () => {
    setDeleteTarget(null)
  }

  return (
    <div className="knowledge-panel">
      <div className="panel-header">
        <h3>知识库</h3>
        <button onClick={onClose} className="icon-btn" title="关闭">
          <X size={18} />
        </button>
      </div>

      {/* 搜索栏 */}
      <div className="search-bar">
        <input
          type="text"
          placeholder="模糊搜索文件名或内容..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
        />
        <button onClick={handleSearch} disabled={isSearching} className="icon-btn">
          {isSearching ? <Loader2 size={16} className="spinning" /> : <Search size={16} />}
        </button>
      </div>

      {/* 搜索结果 */}
      {searchResults !== null && (
        <div className="search-results">
          <div className="results-header">
            <span>{searchResults.length} 条结果</span>
            <button onClick={() => setSearchResults(null)} className="text-btn">
              清除
            </button>
          </div>
          {searchResults.length === 0 ? (
            <p className="empty-text">未找到匹配文件</p>
          ) : (
            <ul className="results-list">
              {searchResults.map((r) => (
                <li key={r.chunk_id} className="result-item result-item--compact">
                  {fileIcon(r.file_type)}
                  <span className="result-file-name">{r.file_name}</span>
                  <button
                    onClick={() => handleDelete(r.file_id, r.file_name)}
                    className="icon-btn danger"
                    title="删除"
                  >
                    <Trash2 size={14} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* 上传按钮 */}
      <div className="upload-area">
        <input
          ref={fileInputRef}
          type="file"
          multiple
          onChange={handleFileChange}
          style={{ display: 'none' }}
          accept=".pdf,.docx,.xlsx,.xls,.csv,.html,.htm,.md,.txt,.log,.json,.yaml,.yml,.toml,.py,.ts,.tsx,.js,.jsx,.rs,.go,.java,.css,.scss,.less"
        />
        <button
          onClick={handleUpload}
          disabled={uploadMutation.isPending}
          className="primary-btn"
        >
          {uploadMutation.isPending ? (
            <Loader2 size={16} className="spinning" />
          ) : (
            <Upload size={16} />
          )}
          上传文件
        </button>
      </div>



      {/* 文件列表 */}
      <div className="files-section">
        <div className="files-scroll">
        <h4>已索引文件</h4>
        {isLoading ? (
          <p className="loading-text">加载中...</p>
        ) : files && files.length > 0 ? (
          <ul className="files-list">
            {files.map((f: KnowledgeFile) => (
              <li key={f.file_id} className="file-item">
                {fileIcon(f.file_type)}
                <span className="file-name" title={f.file_name}>
                  {f.file_name}
                </span>
                <span className="chunk-count">{f.chunk_count} 块</span>
                <button
                  onClick={() => handleDelete(f.file_id, f.file_name)}
                  className="icon-btn danger"
                  title="删除"
                >
                  <Trash2 size={14} />
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="empty-text">尚无已索引文件</p>
        )}
        </div>
      </div>

      {/* 删除确认弹窗 */}
      {deleteTarget ? (
        <div className="modal-overlay" onClick={cancelDelete}>
          <div className="modal-dialog modal-dialog--danger" onClick={(e) => e.stopPropagation()}>
            <div className="modal-icon">
              <AlertTriangle size={32} />
            </div>
            <h4 className="modal-title">确认删除</h4>
            <p className="modal-body">
              删除 <strong>{deleteTarget.fileName}</strong> 将同时移除所有分块和向量数据，此操作不可撤销。
            </p>
            <div className="modal-actions">
              <button className="secondary-button" onClick={cancelDelete} disabled={deleteMutation.isPending}>
                取消
              </button>
              <button className="danger-button" onClick={confirmDelete} disabled={deleteMutation.isPending}>
                {deleteMutation.isPending ? '删除中...' : '确认删除'}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  )
}