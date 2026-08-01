/** 知识库和记忆 API 客户端——Phase 8。 */

const BASE = '/api/v1'

export interface KnowledgeFile {
  file_id: string
  file_name: string
  file_type: string
  chunk_count: number
  ingested_at: string
}

export interface KnowledgeSearchResult {
  chunk_id: string
  file_name: string
  file_type: string
  file_id: string
  chunk_index: number
  content: string
  score: number
}

export interface KnowledgeSearchResponse {
  results: KnowledgeSearchResult[]
  expanded_queries: string[]
  used_expansion: boolean
  total: number
}

export interface IngestResult {
  file_name: string
  file_id: string
  chunks_created: number
  chunks_skipped: number
  warnings: string[]
}

export interface ConversationSummary {
  id: string
  round_start: number
  round_end: number
  summary: string
  is_full_merge: boolean
  created_at: string
}

export interface UserPreference {
  id: string
  category: string
  key: string
  value: string
  importance: number
  is_active: boolean
  conflict_flag: boolean
  previous_version_id: string | null
  created_at: string
  updated_at: string
}

// ── 知识库 API ──────────────────────────────────────────────────────────

export async function uploadKnowledgeFiles(
  projectId: string,
  files: File[],
): Promise<IngestResult[]> {
  const formData = new FormData()
  for (const f of files) {
    formData.append('files', f)
  }
  const res = await fetch(`${BASE}/projects/${projectId}/knowledge/upload`, {
    method: 'POST',
    body: formData,
  })
  if (!res.ok) throw new Error(`上传失败: ${res.status}`)
  return res.json()
}

export async function listKnowledgeFiles(
  projectId: string,
): Promise<KnowledgeFile[]> {
  const res = await fetch(
    `${BASE}/projects/${projectId}/knowledge/files`,
  )
  if (!res.ok) throw new Error(`获取文件列表失败: ${res.status}`)
  return res.json()
}

export async function deleteKnowledgeFile(
  projectId: string,
  fileId: string,
): Promise<{ deleted_chunks: number; message: string }> {
  const res = await fetch(
    `${BASE}/projects/${projectId}/knowledge/files/${encodeURIComponent(fileId)}`,
    { method: 'DELETE' },
  )
  if (!res.ok) throw new Error(`删除失败: ${res.status}`)
  return res.json()
}

export async function searchKnowledge(
  projectId: string,
  q: string,
  topK = 10,
): Promise<KnowledgeSearchResponse> {
  const params = new URLSearchParams({ q, top_k: String(topK) })
  const res = await fetch(
    `${BASE}/projects/${projectId}/knowledge/search?${params}`,
  )
  if (!res.ok) throw new Error(`检索失败: ${res.status}`)
  return res.json()
}

export async function fuzzySearchKnowledge(
  projectId: string,
  q: string,
  topK = 20,
): Promise<KnowledgeSearchResult[]> {
  const params = new URLSearchParams({ q, top_k: String(topK) })
  const res = await fetch(
    `${BASE}/projects/${projectId}/knowledge/search/quick?${params}`,
  )
  if (!res.ok) throw new Error(`搜索失败: ${res.status}`)
  return res.json()
}

// ── 记忆 API ────────────────────────────────────────────────────────────

export async function listSummaries(
  projectId: string,
  conversationId: string,
): Promise<ConversationSummary[]> {
  const res = await fetch(
    `${BASE}/projects/${projectId}/conversations/${conversationId}/summaries`,
  )
  if (!res.ok) throw new Error(`获取摘要失败: ${res.status}`)
  return res.json()
}

export async function triggerSummarize(
  projectId: string,
  conversationId: string,
): Promise<ConversationSummary> {
  const res = await fetch(
    `${BASE}/projects/${projectId}/conversations/${conversationId}/summarize`,
    { method: 'POST' },
  )
  if (!res.ok) throw new Error(`摘要生成失败: ${res.status}`)
  return res.json()
}

export async function listPreferences(
  projectId: string,
  category?: string,
): Promise<UserPreference[]> {
  const params = category
    ? `?${new URLSearchParams({ category })}`
    : ''
  const res = await fetch(
    `${BASE}/projects/${projectId}/preferences${params}`,
  )
  if (!res.ok) throw new Error(`获取偏好失败: ${res.status}`)
  return res.json()
}

export async function deletePreference(
  projectId: string,
  preferenceId: string,
): Promise<void> {
  const res = await fetch(
    `${BASE}/projects/${projectId}/preferences/${preferenceId}`,
    { method: 'DELETE' },
  )
  if (!res.ok) throw new Error(`删除偏好失败: ${res.status}`)
}