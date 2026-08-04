import { useQuery } from "@tanstack/react-query"
import { CheckCircle, ExternalLink, Loader2, XCircle, X } from "lucide-react"
import { getDeploymentStatus } from "../api/phase10"

interface Props { projectId: string; deploymentId: string; onClose: () => void }

function sl(s: string): string {
  const m: Record<string, string> = { pending: "等待中", preparing: "准备中", uploading: "上传中", building: "构建中", completed: "已完成", failed: "失败", cancelled: "已取消" }
  return m[s] ?? s
}

export function DeploymentPanel({ projectId, deploymentId, onClose }: Props) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["deployment-status", projectId, deploymentId],
    queryFn: () => getDeploymentStatus(projectId, deploymentId),
    refetchInterval: (q) => {
      const s = q.state.data?.status
      return s === "completed" || s === "failed" || s === "cancelled" ? false : 3000
    },
    staleTime: 500,
  })

  const done = data?.status === "completed" || data?.status === "failed" || data?.status === "cancelled"

  function openUrl(url: string) {
    const u = /^https?:\/\//i.test(url) ? url : "https://" + url
    window.open(u, "_blank", "noopener,noreferrer")
  }

  return (
    <>
      <div className="panel-backdrop" onClick={onClose} />
      <aside className="right-panel">
        <div className="panel-header"><h3>部署到 Vercel</h3><div className="panel-header-actions"><button className="icon-button" title="关闭" onClick={onClose}><X size={16} /></button></div></div>
        <div className="deployment-body">
          {isLoading ? <div className="deployment-placeholder"><Loader2 size={24} className="spinner" /><span>加载中...</span></div>
          : isError ? <div className="deployment-placeholder deployment-placeholder--error"><span>无法获取部署状态</span></div>
          : data ? <div className="deployment-detail">
              <div className="deployment-status-row">
                {data.status === "completed" ? <CheckCircle size={16} className="status-icon--success" />
                : data.status === "failed" ? <XCircle size={16} className="status-icon--danger" />
                : <Loader2 size={16} className="spinner" />}
                <span className="deployment-status-label">{sl(data.status)}</span>
              </div>
              {data.error_code ? <div className="deployment-error"><span>错误: {data.error_code}</span></div> : null}
              {data.result_url ? (
                <button className="deployment-url" onClick={() => openUrl(data.result_url!)}>
                  <ExternalLink size={14} /><span>{/^https?:\/\//i.test(data.result_url) ? data.result_url : "https://" + data.result_url}</span>
                </button>
              ) : null}
              {!done ? <div className="deployment-progress"><div className="progress-bar"><div className="progress-bar__fill progress-bar__fill--indeterminate" /></div></div> : null}
            </div>
          : null}
        </div>
      </aside>
    </>
  )
}
