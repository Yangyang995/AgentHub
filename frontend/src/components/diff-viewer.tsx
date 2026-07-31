import { useMemo } from 'react'

interface DiffLine {
  type: 'context' | 'addition' | 'deletion' | 'header'
  lineNumber: number | null
  content: string
}

interface DiffViewerProps {
  diffContent: string
  fileName?: string
  className?: string
  theme?: 'light' | 'dark'
}

/** 解析 unified diff 文本为结构化行列表 */
function parseDiff(diffContent: string): DiffLine[] {
  const lines = diffContent.split('\n')
  const result: DiffLine[] = []
  let leftLine: number | null = null
  let rightLine: number | null = null

  for (const line of lines) {
    // 匹配 @@ -a,b +c,d @@ 格式的行号范围
    const rangeMatch = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/.exec(line)
    if (rangeMatch) {
      leftLine = parseInt(rangeMatch[1] ?? '0', 10)
      rightLine = parseInt(rangeMatch[3] ?? '0', 10)
      result.push({ type: 'header', lineNumber: null, content: line })
      continue
    }
    // 跳过纯 diff 命令头
    if (
      line.startsWith('diff ') ||
      line.startsWith('--- ') ||
      line.startsWith('+++ ') ||
      line.startsWith('index ') ||
      line === ''
    ) {
      if (line.startsWith('--- ') || line.startsWith('+++ ')) {
        result.push({ type: 'header', lineNumber: null, content: line })
      }
      continue
    }
    if (line.startsWith('+')) {
      result.push({
        type: 'addition',
        lineNumber: rightLine,
        content: line.substring(1),
      })
      if (rightLine !== null) rightLine++
    } else if (line.startsWith('-')) {
      result.push({
        type: 'deletion',
        lineNumber: leftLine,
        content: line.substring(1),
      })
      if (leftLine !== null) leftLine++
    } else if (line.startsWith(' ') || line === '') {
      result.push({
        type: 'context',
        lineNumber: rightLine,
        content: line.startsWith(' ') ? line.substring(1) : line,
      })
      if (leftLine !== null) leftLine++
      if (rightLine !== null) rightLine++
    }
  }
  return result
}

export function DiffViewer({ diffContent, fileName, className, theme = 'light' }: DiffViewerProps) {
  const diffLines = useMemo(() => parseDiff(diffContent), [diffContent])

  if (diffLines.length === 0) {
    return (
      <div className={`diff-viewer diff-viewer--empty diff-viewer--${theme} ${className ?? ''}`}>
        <p>没有差异内容</p>
      </div>
    )
  }

  return (
    <div className={`diff-viewer diff-viewer--${theme} ${className ?? ''}`}>
      {fileName !== undefined ? (
        <header className="diff-viewer__header">
          <span className="diff-viewer__filename">{fileName}</span>
        </header>
      ) : null}
      <div className="diff-viewer__lines" role="table" aria-label="代码差异对比">
        {diffLines.map((line, index) => (
          <div
            key={index}
            role="row"
            className={`diff-line diff-line--${line.type}`}
          >
            {line.type === 'header' ? (
              <div className="diff-line__header">
                {line.content}
              </div>
            ) : (
              <>
                <span className="diff-line__number diff-line__number--old" role="cell" aria-hidden="true">
                  {line.type === 'deletion' || line.type === 'context'
                    ? (line.lineNumber ?? '')
                    : ''}
                </span>
                <span className="diff-line__number diff-line__number--new" role="cell" aria-hidden="true">
                  {line.type === 'addition' || line.type === 'context'
                    ? (line.lineNumber ?? '')
                    : ''}
                </span>
                <span className="diff-line__content" role="cell">
                  <span className="diff-line__prefix">
                    {line.type === 'addition' ? '+' : line.type === 'deletion' ? '-' : ' '}
                  </span>
                  {line.content}
                </span>
              </>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
