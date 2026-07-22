import React, { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { createProvider } from '../api'

const s: Record<string, React.CSSProperties> = {
  wrap: { maxWidth: 480, margin: '60px auto', background: '#1a1d2e', borderRadius: 12, padding: 32, border: '1px solid #2d3148' },
  h1: { fontSize: 22, fontWeight: 700, marginBottom: 8, color: '#e2e8f0' },
  sub: { color: '#64748b', fontSize: 14, marginBottom: 28 },
  label: { display: 'block', fontSize: 13, color: '#94a3b8', marginBottom: 4, marginTop: 16 },
  input: { width: '100%', padding: '8px 12px', background: '#0f1117', border: '1px solid #2d3148', borderRadius: 6, color: '#e2e8f0', fontSize: 14 },
  btn: { marginTop: 24, width: '100%', padding: '10px 0', background: '#6366f1', color: '#fff', border: 'none', borderRadius: 6, fontSize: 15, fontWeight: 600, cursor: 'pointer' },
  err: { marginTop: 12, color: '#f87171', fontSize: 13 },
}

export default function SetupWizard({ onComplete }: { onComplete: () => void }) {
  const nav = useNavigate()
  const [form, setForm] = useState({ name: '', base_url: '', api_key: '' })
  const [err, setErr] = useState('')
  const [saving, setSaving] = useState(false)

  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm(f => ({ ...f, [k]: e.target.value }))

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!form.name || !form.base_url) { setErr('Name and Base URL are required.'); return }
    setSaving(true); setErr('')
    try {
      await createProvider({ ...form, is_enabled: true, cached_models: [] })
      onComplete()
      nav('/providers')
    } catch (ex: unknown) {
      setErr(String(ex))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div style={s.wrap}>
      <h1 style={s.h1}>Welcome to ModelRouter</h1>
      <p style={s.sub}>Add your first provider to get started. You can add more from the Providers page.</p>
      <form onSubmit={submit}>
        <label style={s.label}>Display name</label>
        <input style={s.input} value={form.name} onChange={set('name')} placeholder="e.g. My Ollama" />
        <label style={s.label}>Base URL</label>
        <input style={s.input} value={form.base_url} onChange={set('base_url')} placeholder="http://localhost:11434" />
        <label style={s.label}>API key <span style={{ color: '#475569' }}>(optional)</span></label>
        <input style={s.input} value={form.api_key} onChange={set('api_key')} type="password" placeholder="sk-..." />
        {err && <p style={s.err}>{err}</p>}
        <button style={s.btn} type="submit" disabled={saving}>{saving ? 'Saving…' : 'Add provider & continue'}</button>
      </form>
    </div>
  )
}
