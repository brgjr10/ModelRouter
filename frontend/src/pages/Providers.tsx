import React, { useEffect, useState } from 'react'
import {
  Provider,
  ModelCapacity,
  EndpointSummary,
  getProviders,
  getEndpoints,
  getModels,
  getStatus,
  updateProvider,
  createProvider,
  deleteProvider,
  fmtTokens,
} from '../api'

type ModelRow = ModelCapacity & { endpoint_name: string }

const s: Record<string, React.CSSProperties> = {
  h1: { fontSize: 20, fontWeight: 700, marginBottom: 20, color: '#e2e8f0' },
  card: { background: '#1a1d2e', border: '1px solid #2d3148', borderRadius: 10, marginBottom: 16, overflow: 'hidden' },
  groupHead: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 18px', background: '#151827', borderBottom: '1px solid #232840' },
  groupName: { fontWeight: 600, fontSize: 15, color: '#e2e8f0' },
  groupSub: { fontSize: 11, color: '#64748b', marginTop: 2 },
  actions: { display: 'flex', gap: 8, alignItems: 'center' },
  btn: { padding: '5px 12px', border: '1px solid #2d3148', borderRadius: 5, background: 'transparent', color: '#94a3b8', fontSize: 12, cursor: 'pointer' },
  table: { width: '100%', borderCollapse: 'collapse' as const, fontSize: 13 },
  th: { textAlign: 'left' as const, padding: '10px 14px', color: '#64748b', fontWeight: 500 },
  td: { padding: '9px 14px', borderTop: '1px solid #1e2235', color: '#cbd5e1' },
  warn: { color: '#fbbf24', marginLeft: 4 },
  modelToggle: { background: 'transparent', border: '1px solid #2d3148', color: '#94a3b8', padding: '3px 8px', borderRadius: 999, fontSize: 11, cursor: 'pointer' },
  addBtn: { marginBottom: 12, padding: '6px 12px', border: '1px solid #2d3148', borderRadius: 5, background: '#14532d', color: '#4ade80' },
  modalOverlay: { position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 },
  modalContent: { background: '#1a1d2e', padding: 24, borderRadius: 8, width: 400, color: '#e2e8f0' },
  modalInput: { width: '100%', padding: '6px', marginBottom: 12, border: '1px solid #2d3148', borderRadius: 4, background: '#1a1d2e', color: '#e2e8f0' },
  modalButton: { padding: '6px 12px', border: '1px solid #2d3148', borderRadius: 5, background: '#4ade80', color: '#0f1117' },
  modalClose: { marginTop: 8, padding: '4px 8px', border: '1px solid #2d3148', borderRadius: 5, background: 'transparent', color: '#94a3b8' },
}

export default function Providers() {
  const [endpoints, setEndpoints] = useState<EndpointSummary[]>([])
  const [providers, setProviders] = useState<Provider[]>([])
  const [models, setModels] = useState<ModelRow[]>([])
  const [lastTokens, setLastTokens] = useState<number>(0)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(false)
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})
  const [showAddModal, setShowAddModal] = useState(false)
  const [newProvider, setNewProvider] = useState({ name: '', base_url: '', api_key: '', is_enabled: true, cached_models: [] as string[] })

  const load = async () => {
    const [eps, ps, ms, st] = await Promise.all([getEndpoints(), getProviders(), getModels(), getStatus()])
    setEndpoints(eps)
    setProviders(ps)
    setModels(ms as ModelRow[])
    setLastTokens(0)
  }

  useEffect(() => { load() }, [])

  const findProviderForEndpoint = (ep: EndpointSummary): Provider | undefined => {
    return providers.find(p => p.base_url === ep.base_url)
  }

  const toggleModel = async (ep: EndpointSummary, modelId: string) => {
    setLoading(true)
    setErr('')
    try {
      let provider = findProviderForEndpoint(ep)
      if (!provider) {
        const created = await createProvider({
          name: ep.name || ep.base_url,
          base_url: ep.base_url,
          api_key: '',
          is_enabled: true,
          cached_models: [],
        })
        provider = created
      }
      const next = provider.cached_models.includes(modelId)
        ? provider.cached_models.filter(x => x !== modelId)
        : [...provider.cached_models, modelId]
      await updateProvider(provider.id, { cached_models: next })
      await load()
    } catch (ex: unknown) {
      setErr(String(ex))
    } finally {
      setLoading(false)
    }
  }

  const toggleEndpoint = async (ep: EndpointSummary) => {
    setLoading(true)
    setErr('')
    try {
      let provider = findProviderForEndpoint(ep)
      if (!provider) {
        const created = await createProvider({
          name: ep.name || ep.base_url,
          base_url: ep.base_url,
          api_key: '',
          is_enabled: true,
          cached_models: [],
        })
        provider = created
      }
      await updateProvider(provider.id, { is_enabled: !provider.is_enabled })
      await load()
    } catch (ex: unknown) {
      setErr(String(ex))
    } finally {
      setLoading(false)
    }
  }

  const delProvider = async (ep: EndpointSummary) => {
    const provider = findProviderForEndpoint(ep)
    if (!provider) return
    if (!confirm(`Remove "${ep.name}"? Models will be hidden but the upstream service will not be affected.`)) return
    setLoading(true)
    try {
      const { deleteProvider: deleteProviderFn } = await import('../api')
      await deleteProviderFn(provider.id)
      await load()
    } catch (ex: unknown) {
      setErr(String(ex))
    } finally {
      setLoading(false)
    }
  }

  const modelsForEndpoint = (epId: string) => models.filter(m => m.provider_id === epId)

  const grouped = endpoints.reduce<Record<string, EndpointSummary[]>>((acc, ep) => {
    const key = ep.name || ep.base_url || 'Unnamed'
    acc[key] = acc[key] || []
    acc[key].push(ep)
    return acc
  }, {})

  const handleAddProvider = async () => {
    setLoading(true)
    setErr('')
    try {
      const resp = await createProvider(newProvider)
      setProviders([...providers, resp])
      setShowAddModal(false)
      setNewProvider({ name: '', base_url: '', api_key: '', is_enabled: true, cached_models: [] })
      await load()
    } catch (ex) {
      setErr(String(ex))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      <h1 style={s.h1}>Providers</h1>
      <button style={s.addBtn} onClick={() => setShowAddModal(true)}>
        Add Provider
      </button>

      <div style={{ marginBottom: 16, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <button
          style={{ ...s.btn, background: Object.values(collapsed).every(Boolean) ? '#14532d' : undefined, color: Object.values(collapsed).every(Boolean) ? '#4ade80' : undefined }}
          onClick={() => setCollapsed(Object.fromEntries(Object.keys(grouped).map(k => [k, true])))}
        >Collapse all</button>
        <button
          style={{ ...s.btn, background: Object.values(collapsed).some(Boolean) ? '#14532d' : undefined, color: Object.values(collapsed).some(Boolean) ? '#4ade80' : undefined }}
          onClick={() => setCollapsed({})}
        >Expand all</button>
      </div>

      {loading && <p style={{ color: '#64748b', fontSize: 12, marginBottom: 12 }}>Saving…</p>}
      {err && <p style={{ color: '#f87171', fontSize: 12, marginBottom: 12 }}>{err}</p>}

      {showAddModal && (
        <div style={s.modalOverlay}>
          <div style={s.modalContent}>
            <h3 style={{ marginTop: 0 }}>Add New Provider</h3>
            <label style={{ color: '#94a3b8', marginBottom: 4, display: 'block' }}>
              Name: <input type="text" value={newProvider.name} onChange={(e) => setNewProvider({ ...newProvider, name: e.target.value })} placeholder="Provider Name" style={s.modalInput} />
            </label>
            <label style={{ color: '#94a3b8', marginBottom: 4, display: 'block' }}>
              Base URL: <input type="text" value={newProvider.base_url} onChange={(e) => setNewProvider({ ...newProvider, base_url: e.target.value })} placeholder="http://localhost:5050" style={s.modalInput} />
            </label>
            <label style={{ color: '#94a3b8', marginBottom: 4, display: 'block' }}>
              API Key: <input type="text" value={newProvider.api_key} onChange={(e) => setNewProvider({ ...newProvider, api_key: e.target.value })} placeholder="your_api_key_here" style={s.modalInput} />
            </label>
            <button type="submit" style={s.modalButton} onClick={handleAddProvider} disabled={loading}>
              {loading ? 'Creating…' : 'Create Provider'}
            </button>
            <button type="button" style={s.modalClose} onClick={() => setShowAddModal(false)}>Cancel</button>
          </div>
        </div>
      )}

      {endpoints.length === 0 && (
        <p style={{ color: '#475569' }}>No endpoints found. The router will auto-discover local services and show Odysseus DB entries here.</p>
      )}

      {Object.entries(grouped).map(([name, group]) => {
        const isCollapsed = !!collapsed[name]
        const merged = group.flatMap(ep => modelsForEndpoint(ep.id))
        const anyManaged = group.some(ep => findProviderForEndpoint(ep))
        return (
          <div key={name} style={{ ...s.card, marginBottom: 12 }}>
            <div style={{ ...s.groupHead, cursor: 'pointer' }} onClick={() => setCollapsed(c => ({ ...c, [name]: !isCollapsed }))}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <span style={{ color: '#64748b', fontSize: 12, width: 18 }}>{isCollapsed ? '▶' : '▼'}</span>
                <div>
                  <div style={s.groupName}>{name}</div>
                  <div style={s.groupSub}>{merged.length} models</div>
                </div>
              </div>
              <div style={s.actions} onClick={e => e.stopPropagation()}>
                {anyManaged && group.map(ep => {
                  const provider = findProviderForEndpoint(ep)
                  if (!provider) return null
                  return (
                    <button
                      key={ep.id}
                      style={{ ...s.btn, background: provider.is_enabled ? '#14532d' : '#1e293b', color: provider.is_enabled ? '#4ade80' : '#64748b' }}
                      onClick={async () => await toggleEndpoint(ep)}
                    >
                      {provider.is_enabled ? 'Enabled' : 'Disabled'}
                    </button>
                  )
                })}
                {!anyManaged && (
                  <button
                    style={{ ...s.btn, background: '#14532d', color: '#4ade80' }}
                    onClick={async () => { for (const ep of group) await toggleEndpoint(ep) }}
                  >
                    Enable all
                  </button>
                )}
                <button
                  style={{ ...s.btn, color: '#f87171', borderColor: '#7f1d1d' }}
                  onClick={async () => await delProvider(group[0])}
                >
                  Remove
                </button>
              </div>
            </div>

            {!isCollapsed && (
              merged.length > 0 ? (
                <table style={s.table}>
                  <thead>
                    <tr>
                      <th style={s.th}>Model</th>
                      <th style={{ ...s.th, width: 110, textAlign: 'right' as const }}>Toggle</th>
                    </tr>
                  </thead>
                  <tbody>
                    {merged.map(m => {
                      const provider = providers.find(p => p.id === m.provider_id)
                      const isActive = provider ? provider.cached_models.includes(m.model_id) : true
                      return (
                        <tr key={`${m.provider_id}-${m.model_id}`}>
                          <td style={s.td}><code style={{ color: '#a5b4fc' }}>{m.model_id}</code></td>
                          <td style={{ ...s.td, textAlign: 'right' as const }}>
                            <button
                              style={{ ...s.modelToggle, background: isActive ? '#14532d' : '#1e293b', color: isActive ? '#4ade80' : '#64748b' }}
                              onClick={async () => { const ep = group.find(e => e.id === m.provider_id); if (ep) await toggleModel(ep, m.model_id) }}
                            >
                              {isActive ? 'Active' : 'Idle'}
                            </button>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              ) : (
                <p style={{ padding: '10px 14px', color: '#475569', fontSize: 12 }}>
                  No models loaded yet — the router will discover them on next sync.
                </p>
              )
            )}
          </div>
        )
      })}
    </div>
  )
}