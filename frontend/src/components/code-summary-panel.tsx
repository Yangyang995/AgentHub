import { useState } from 'react'
import { Check, ChevronDown, ChevronRight, Copy, FileCode } from 'lucide-react'
import type { CodeSummaryFile } from '../api/chat'

interface CodeSummaryPanelProps {
  agentName: string
  files: CodeSummaryFile[]
  executionId: string
}

function parseDiffLineNumbers(diff: string): { added: number[]; deleted: number[] } {
  const added: number[] = []
  const deleted: number[] = []
  let oldLine = 0
  let newLine = 0
  for (const line of diff.split('\n')) {
    const hunk = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line)
    if (hunk) {
      oldLine = parseInt(hunk[1] ?? '0', 10)
      newLine = parseInt(hunk[2] ?? '0', 10)
      continue
    }
    if (line.startsWith('+') && !line.startsWith('+++')) {
      added.push(newLine)
      newLine++
    } else if (line.startsWith('-') && !line.startsWith('---')) {
      deleted.push(oldLine)
      oldLine++
    } else if (line.startsWith(' ')) {
      oldLine++
      newLine++
    }
  }
  return { added, deleted }
}

function HighlightedCode({
  content,
  addedLines,
  deletedLines,
}: {
  content: string
  addedLines: number[]
  deletedLines: number[]
}) {
  const lines = content.split('\n')
  const addedSet = new Set(addedLines)
  const deletedSet = new Set(deletedLines)

  return (
    <div className="highlighted-code">
      <div className="highlighted-code__lines">
        {lines.map((line, index) => {
          const lineNum = index + 1
          const isAdded = addedSet.has(lineNum)
          const isDeleted = deletedSet.has(lineNum)
          let className = 'highlighted-code__line'
          if (isAdded) className += ' highlighted-code__line--added'
          if (isDeleted) className += ' highlighted-code__line--deleted'
          return (
            <div key={index} className={className}>
              <span className="highlighted-code__gutter">{lineNum}</span>
              <span className="highlighted-code__text" data-noselect={isDeleted ? 'true' : undefined}>
                {line}
              </span>
            </div>
          )
        })}
      </div>
      {deletedLines.length > 0 ? (
        <div className="highlighted-code__legend">
          <span className="legend-item legend-item--added">绿色 = 新增/修改</span>
          <span className="legend-item legend-item--deleted">红色 = 已删除（不参与复制）</span>
        </div>
      ) : null}
    </div>
  )
}

export function CodeSummaryPanel({ agentName, files, executionId }: CodeSummaryPanelProps) {
  const [expanded, setExpanded] = useState(false)
  const [expandedFiles, setExpandedFiles] = useState<Set<number>>(new Set())
  const [copiedIndex, setCopiedIndex] = useState<number | null>(null)

  if (files.length === 0) return null

  function togglePanel() {
    setExpanded((prev) => !prev)
  }

  function toggleFile(index: number) {
    setExpandedFiles((prev) => {
      const next = new Set(prev)
      if (next.has(index)) {
        next.delete(index)
      } else {
        next.add(index)
      }
      return next
    })
  }

  function expandAll() {
    setExpandedFiles(new Set(files.map((_, i) => i)))
  }

  function collapseAll() {
    setExpandedFiles(new Set())
  }

  async function copyCode(index: number) {
    const file = files[index]
    if (!file) return
    const { deleted } = parseDiffLineNumbers(file.diff)
    const deletedSet = new Set(deleted)
    const filtered = file.content.split('\n').filter((_, i) => !deletedSet.has(i + 1)).join('\n')
    try {
      await navigator.clipboard.writeText(filtered)
    } catch {
      // clipboard API not available, silently ignore
    }
    setCopiedIndex(index)
    setTimeout(() => { setCopiedIndex(null) }, 2000)
  }

  const newCount = files.filter((f) => f.is_new_file).length
  const modifiedCount = files.filter((f) => f.is_modified).length

  return (
    <div className="code-summary-panel">
      <button
        type="button"
        className="code-summary-panel__toggle"
        onClick={togglePanel}
        aria-expanded={expanded}
      >
        {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        <span className="code-summary-panel__title">
          {agentName} 生成了 {files.length} 个文件
        </span>
        <span className="code-summary-panel__stats">
          {newCount > 0 ? <span className="tag tag--new">{newCount} 新建</span> : null}
          {modifiedCount > 0 ? <span className="tag tag--modified">{modifiedCount} 修改</span> : null}
        </span>
      </button>

      {expanded ? (
        <div className="code-summary-panel__body">
          <div className="code-summary-panel__toolbar">
            <button type="button" className="secondary-button" onClick={expandAll}>
              全部展开
            </button>
            <button type="button" className="secondary-button" onClick={collapseAll}>
              全部折叠
            </button>
          </div>

          <ul className="code-summary-panel__file-list">
            {files.map((file, index) => {
              const isFileExpanded = expandedFiles.has(index)
              const { added, deleted } = parseDiffLineNumbers(file.diff)
              return (
                <li key={`${executionId}-${String(index)}`} className="code-summary-file">
                  <button
                    type="button"
                    className="code-summary-file__header"
                    onClick={() => { toggleFile(index) }}
                  >
                    {isFileExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    <FileCode size={14} className="code-summary-file__icon" />
                    <span className="code-summary-file__path">{file.path}</span>
                    {file.language !== '' ? (
                      <span className="code-summary-file__lang">{file.language}</span>
                    ) : null}
                    {file.is_new_file ? (
                      <span className="tag tag--new">新建</span>
                    ) : file.is_modified ? (
                      <span className="tag tag--modified">已修改</span>
                    ) : (
                      <span className="tag tag--unchanged">未变更</span>
                    )}
                    <button
                      type="button"
                      className="code-summary-file__copy"
                      title="复制代码（不含删除行）"
                      onClick={(e) => { e.stopPropagation(); void copyCode(index) }}
                    >
                      {copiedIndex === index ? (
                        <>
                          <Check size={13} />
                          已复制
                        </>
                      ) : (
                        <>
                          <Copy size={13} />
                          复制
                        </>
                      )}
                    </button>
                  </button>

                  {isFileExpanded ? (
                    <div className="code-summary-file__code">
                      {file.is_modified && (added.length > 0 || deleted.length > 0) ? (
                        <HighlightedCode
                          content={file.content}
                          addedLines={added}
                          deletedLines={deleted}
                        />
                      ) : (
                        <pre className="code-summary-file__content">
                          <code>{file.content}</code>
                        </pre>
                      )}
                    </div>
                  ) : null}
                </li>
              )
            })}
          </ul>
        </div>
      ) : null}
    </div>
  )
}