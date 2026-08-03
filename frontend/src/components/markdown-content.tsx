import React, { useCallback, useRef, useState } from 'react'
import { Check, Copy } from 'lucide-react'
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

/** 代码块复制按钮，悬浮在 pre 右上角 */
function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout>>(undefined)
  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      if (timerRef.current) clearTimeout(timerRef.current)
      timerRef.current = setTimeout(() => setCopied(false), 1500)
    }).catch(() => {})
  }, [text])
  return (
    <button className="code-block-copy-btn" type="button" aria-label="复制代码" title="复制代码" onClick={handleCopy}>
      {copied ? <Check size={14} /> : <Copy size={14} />}
    </button>
  )
}

/** 自定义 pre 渲染器，包裹 CopyButton */
function PreWithCopy({ children, ...rest }: React.ComponentPropsWithoutRef<'pre'>) {
  const rawText: string = (() => {
    if (children === null || children === undefined) return ''
    const codeElement = children as { props?: { children?: string | string[] } }
    const codeProps = codeElement?.props
    if (!codeProps) return ''
    if (typeof codeProps.children === 'string') return codeProps.children
    if (Array.isArray(codeProps.children)) return codeProps.children.join('')
    return ''
  })()
  return (
    <div className="code-block-wrapper">
      <CopyButton text={rawText} />
      <pre {...rest}>{children}</pre>
    </div>
  )
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
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{ pre: PreWithCopy }}
      >
        {displayContent}
      </ReactMarkdown>
    </div>
  )
}
