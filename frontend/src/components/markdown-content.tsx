import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

interface MarkdownContentProps {
  content: string
  /** 隐藏代码块（仅展示非代码块的文本内容） */
  hideCodeBlocks?: boolean
  /** 检测到第一个代码块开头即截断，不渲染代码块及之后的内容 */
  truncateAtCodeBlock?: boolean
}

function stripCodeBlocks(md: string): string {
  // 同时处理完整代码块（```...```）和未闭合代码块（```... 流式中途）
  return md.replace(/```[\s\S]*?(```|$)/g, '')
}

function truncateBeforeCodeBlock(md: string): string {
  const idx = md.indexOf('```')
  return idx === -1 ? md : md.slice(0, idx)
}

export function MarkdownContent({ content, hideCodeBlocks, truncateAtCodeBlock }: MarkdownContentProps) {
  let displayContent: string
  if (truncateAtCodeBlock === true) {
    displayContent = truncateBeforeCodeBlock(content)
  } else if (hideCodeBlocks === true) {
    displayContent = stripCodeBlocks(content)
  } else {
    displayContent = content
  }
  return (
    <div className="markdown-content">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{displayContent}</ReactMarkdown>
    </div>
  )
}
