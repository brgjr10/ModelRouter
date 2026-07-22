export interface Provider {
  id: string
  name: string
  base_url: string
  api_key: string
  is_enabled: boolean
  cached_models: string[]
}

export interface ModelCapacity {
  model_id: string
  provider_id: string
  provider: string
  context_window: number | null
  rpm: number | null
  tpm: number | null
  tpd: number | null
}

export interface SelectionRecord {
  model_id: string
  endpoint_name: string
  task_type: string
  reasoning_depth: string
  score: number
  timestamp: string
}

export interface DecisionTrace extends SelectionRecord {
  task: Record<string, any>
  top_candidates: Array<{ model_id: string; endpoint_name: string; score: number }>
}

export interface StatusResponse {
  active_providers: number
  registered_models: number
  recent_selections: DecisionTrace[]
}

export interface EndpointSummary {
  id: string
  name: string
  base_url: string
  is_managed: boolean
  is_enabled: boolean
  model_count: number
}

const BASE = ''

export async function getProviders(): Promise<Provider[]> {
  const r = await fetch(`${BASE}/api/providers`)
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function getEndpoints(): Promise<EndpointSummary[]> {
  const r = await fetch(`${BASE}/api/endpoints`)
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function setEndpointEnabled(id: string, enabled: boolean): Promise<EndpointSummary> {
  const r = await fetch(`${BASE}/api/providers/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ is_enabled: enabled }),
  })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function createProvider(p: Omit<Provider, 'id'>): Promise<Provider> {
  const r = await fetch(`${BASE}/api/providers`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(p),
  })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function updateProvider(id: string, p: Partial<Provider>): Promise<Provider> {
  const r = await fetch(`${BASE}/api/providers/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(p),
  })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function deleteProvider(id: string): Promise<void> {
  const r = await fetch(`${BASE}/api/providers/${id}`, { method: 'DELETE' })
  if (!r.ok) throw new Error(await r.text())
}

export async function getModels(): Promise<ModelCapacity[]> {
  const r = await fetch(`${BASE}/api/providers/models`)
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function getStatus(): Promise<StatusResponse> {
  const r = await fetch(`${BASE}/api/status`)
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export function getDecisionsLive(): EventSource {
  return new EventSource('/api/decisions/live')
}

export async function getWeights(): Promise<any> {
  const r = await fetch(`${BASE}/api/weights`)
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export async function saveWeights(weights: any): Promise<any> {
  const r = await fetch(`${BASE}/api/weights`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(weights),
  })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export function fmtTokens(n: number | null): string {
  if (n === null || n === undefined) return '—'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${Math.round(n / 1_000)}K`
  return String(n)
}
