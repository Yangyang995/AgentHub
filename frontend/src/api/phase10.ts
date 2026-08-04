const BASE = "/api/v1"

export interface ArtifactResponse {
  id: string; project_id: string; execution_id: string | null
  artifact_type: string; relative_path: string; content_hash: string
  size: number; metadata_json: Record<string, unknown> | null; created_at: string
}

export async function uploadArtifact(projectId: string, data: {
  artifact_type: string; relative_path: string; content_base64: string
  execution_id?: string; metadata?: Record<string, unknown>
}): Promise<ArtifactResponse> {
  const res = await fetch(`${BASE}/projects/${projectId}/artifacts`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error((await res.json().catch(() => ({ detail: "upload failed" }))).detail ?? "upload failed")
  return res.json()
}

export interface PreviewStartResponse { approval_id: string; preview_id: string; message: string }
export interface PreviewStatusResponse { preview_id: string; status: string; url: string | null; port: number | null; error: string | null }

export async function startPreview(projectId: string, artifactId: string, extraArtifactIds?: string[]): Promise<PreviewStartResponse> {
  const res = await fetch(`${BASE}/projects/${projectId}/artifacts/${artifactId}/preview`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: extraArtifactIds?.length ? JSON.stringify({ extra_artifact_ids: extraArtifactIds }) : undefined,
  })
  if (!res.ok) throw new Error((await res.json().catch(() => ({ detail: "preview failed" }))).detail ?? "preview failed")
  return res.json()
}

export async function getPreviewStatus(projectId: string, previewId: string): Promise<PreviewStatusResponse> {
  const res = await fetch(`${BASE}/projects/${projectId}/previews/${previewId}`)
  if (!res.ok) throw new Error(`preview status failed: ${res.status}`)
  return res.json()
}

export async function stopPreview(projectId: string, previewId: string): Promise<{ status: string; preview_id: string }> {
  const res = await fetch(`${BASE}/projects/${projectId}/previews/${previewId}`, { method: "DELETE" })
  if (!res.ok) throw new Error(`stop preview failed: ${res.status}`)
  return res.json()
}

export interface DeploymentStartResponse { approval_id: string; deployment_id: string; message: string }
export interface DeploymentResponse {
  id: string; project_id: string; approval_id: string | null; artifact_id: string | null
  provider: string; status: string; result_url: string | null; error_code: string | null
  created_at: string; updated_at: string
}

export async function createDeployment(projectId: string, artifactId: string, extraArtifactIds?: string[]): Promise<DeploymentStartResponse> {
  const res = await fetch(`${BASE}/projects/${projectId}/artifacts/${artifactId}/deploy`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: extraArtifactIds?.length ? JSON.stringify({ extra_artifact_ids: extraArtifactIds }) : undefined,
  })
  if (!res.ok) throw new Error((await res.json().catch(() => ({ detail: "deploy failed" }))).detail ?? "deploy failed")
  return res.json()
}

export async function getDeploymentStatus(projectId: string, deploymentId: string): Promise<DeploymentResponse> {
  const res = await fetch(`${BASE}/projects/${projectId}/deployments/${deploymentId}`)
  if (!res.ok) throw new Error(`deploy status failed: ${res.status}`)
  return res.json()
}

export async function decideApproval(projectId: string, approvalId: string, decision: string, decidedBy?: string): Promise<{ id: string; status: string }> {
  const res = await fetch(`${BASE}/projects/${projectId}/approvals/${approvalId}/decide`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision, decided_by: decidedBy }),
  })
  if (!res.ok) throw new Error((await res.json().catch(() => ({ detail: "approval failed" }))).detail ?? "approval failed")
  return res.json()
}
