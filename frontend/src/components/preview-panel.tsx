import { useCallback, useEffect, useRef, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { ChevronLeft, ChevronRight, ExternalLink, Loader2, Rocket, Square, X } from "lucide-react"
import { getPreviewStatus, stopPreview } from "../api/phase10"

interface Props { projectId: string; previewId: string; onClose: () => void; onToast?: (t: string, m: string) => void; onDeploy?: () => void }

function sl(s: string): string {
  const m: Record<string, string> = { starting: "启动中", running: "运行中", stopped: "已停止", error: "错误" }
  return m[s] ?? s
}

export function PreviewPanel({ projectId, previewId, onClose, onToast, onDeploy }: Props) {
  const qc = useQueryClient()
  const pr = useRef<HTMLElement>(null)
  const [w, setW] = useState(400)
  const [drag, setDrag] = useState(false)

  const { data, isLoading } = useQuery({
    queryKey: ["preview-status", projectId, previewId],
    queryFn: () => getPreviewStatus(projectId, previewId),
    retry: 0,
    refetchInterval: (q) => {
      if (q.state.error) return 2000
      const s = q.state.data?.status
      return s === "stopped" || s === "error" ? false : 2000
    },
    staleTime: 500,
  })

  const stop = async () => {
    try { await stopPreview(projectId, previewId); qc.invalidateQueries({ queryKey: ["preview-status", projectId, previewId] }); onToast?.("success", "已停止") }
    catch (e) { onToast?.("error", e instanceof Error ? e.message : "停止失败") }
  }

  const onMD = useCallback((e: React.MouseEvent) => { e.preventDefault(); setDrag(true) }, [])
  useEffect(() => {
    if (!drag) return
    const mx = Math.floor(window.innerWidth * 0.7)
    const mm = (e: MouseEvent) => setW(Math.min(Math.max(window.innerWidth - e.clientX, 340), mx))
    const mu = () => setDrag(false)
    document.addEventListener("mousemove", mm); document.addEventListener("mouseup", mu)
    document.body.style.userSelect = "none"; document.body.style.cursor = "ew-resize"
    return () => { document.removeEventListener("mousemove", mm); document.removeEventListener("mouseup", mu); document.body.style.userSelect = ""; document.body.style.cursor = "" }
  }, [drag])

  const running = data?.status === "running" && data.url
  return (
    <>
      <div className="panel-backdrop" onClick={onClose} />
      <aside ref={pr} className="right-panel" style={{ width: w }}>
        <div className={`panel-resize-handle${drag ? " panel-resize-handle--active" : ""}`} onMouseDown={onMD} title={"拖拽调整宽度"}>
            {w > 500 ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
          </div>
        <div className="panel-header">
          <h3>本地预览</h3>
          <div className="panel-header-actions">
            {running ? <a href={data!.url!} target="_blank" rel="noopener noreferrer" className="icon-button" title="打开"><ExternalLink size={16} /></a> : null}
            {onDeploy && running ? <button className="icon-button" title="部署到 Vercel" onClick={onDeploy}><Rocket size={16} /></button> : null}
            {data?.status === "running" ? <button className="icon-button" title="停止" onClick={stop}><Square size={16} /></button> : null}
            <button className="icon-button" title="关闭" onClick={onClose}><X size={16} /></button>
          </div>
        </div>
        <div className="preview-body">
          {isLoading ? <div className="preview-placeholder"><Loader2 size={24} className="spinner" /><span>加载中...</span></div>
          : data?.status === "error" ? <div className="preview-placeholder preview-placeholder--error"><span>Preview error{data.error ? ": " + data.error : ""}</span></div>
          : running ? <iframe src={data!.url!} title="Preview" sandbox="allow-scripts" className="preview-iframe" />
          : <div className="preview-placeholder"><Loader2 size={24} className="spinner" /><span>{sl(data?.status ?? "启动中")}...</span></div>}
        </div>
        {data ? <div className="preview-footer"><span className={`status-badge status-badge--${data.status}`}>{sl(data.status)}</span>{data.port ? <span className="preview-port">:{data.port}</span> : null}</div> : null}
      </aside>
    </>
  )
}
