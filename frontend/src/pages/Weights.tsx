import React, { useEffect, useState } from 'react'
import { getWeights, saveWeights } from '../api'

const DIMS = [
  { key: 'task_match', label: 'Task Match' },
  { key: 'reasoning', label: 'Reasoning' },
  { key: 'context_fit', label: 'Context Fit' },
  { key: 'tool_use', label: 'Tool Use' },
  { key: 'latency', label: 'Latency' },
  { key: 'cost', label: 'Cost' },
]

const s: Record<string, React.CSSProperties> = {
  h1: { fontSize: 20, fontWeight: 700, marginBottom: 20, color: '#e2e8f0' },
  card: { background: '#1a1d2e', border: '1px solid #2d3148', borderRadius: 10, padding: 20, marginBottom: 16 },
  row: { display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 },
  label: { width: 140, color: '#94a3b8', fontSize: 13 },
  input: { flex: 1, padding: '6px 10px', background: '#0f1117', border: '1px solid #2d3148', borderRadius: 5, color: '#e2e8f0', fontSize: 13 },
  hint: { color: '#64748b', fontSize: 12, marginTop: 4 },
  saveBtn: { padding: '8px 18px', background: '#6366f1', color: '#fff', border: 'none', borderRadius: 6, fontSize: 13, fontWeight: 600, cursor: 'pointer', marginTop: 8 },
  ok: { color: '#4ade80', fontSize: 12, marginLeft: 10 },
  err: { color: '#f87171', fontSize: 12, marginLeft: 10 },
}

export default function Weights() {
  const [weights, setWeights] = useState<Record<string, number>>({})
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')

  useEffect(() => {
    getWeights().then(w => setWeights(w.global || {})).catch(() => setWeights({}))
  }, [])

  const set = (key: string) => (e: React.ChangeEvent<HTMLInputElement>) => {
    setWeights(w => ({ ...w, [key]: parseFloat(e.target.value) || 0 }))
  }

  const save = async () => {
    setSaving(true); setMsg('')
    try {
      await saveWeights({ global: weights })
      setMsg('Saved')
    } catch (ex: unknown) { setMsg(String(ex)) }
    finally { setSaving(false) }
  }

  return (
    <div>
      <h1 style={s.h1}>Routing Weights</h1>
      <p style={{ color: '#64748b', fontSize: 13, marginBottom: 16 }}>
        Adjust how much each factor influences model selection. Values are normalized at runtime.
      </p>
      <div style={s.card}>
        {DIMS.map(d => (
          <div key={d.key} style={s.row}>
            <div style={s.label}>{d.label}</div>
            <input
              style={s.input}
              type="number"
              step="0.05"
              min="0"
              max="1"
              value={weights[d.key] ?? 0}
              onChange={set(d.key)}
            />
            <div style={{ width: 80, textAlign: 'right', color: '#64748b', fontSize: 12 }}>
              {((weights[d.key] ?? 0) * 100).toFixed(0)}%
            </div>
          </div>
        ))}
        <button style={s.saveBtn} onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save weights'}</button>
        {msg && <span style={msg === 'Saved' ? s.ok : s.err}>{msg}</span>}
      </div>
    </div>
  )
}
