import React, { useEffect, useState } from 'react'
import { getStatus, StatusResponse } from '../api'

type Decision = {
  model_id: string
  endpoint_name: string
  task_type: string
  reasoning_depth: string
  score: number
  timestamp: string
  task: Record<string, any>
  top_candidates: Array<{ model_id: string; endpoint_name: string; score: number }>
}

const s: Record<string, React.CSSProperties> = {
  h1: { fontSize: 20, fontWeight: 700, marginBottom: 20, color: '#e2e8f0' },
  cards: { display: 'flex', gap: 16, marginBottom: 28 },
  card: { flex: 1, background: '#1a1d2e', border: '1px solid #2d3148', borderRadius: 10, padding: '16px 20px' },
  cardNum: { fontSize: 32, fontWeight: 700, color: '#818cf8' },
  cardLbl: { fontSize: 13, color: '#64748b', marginTop: 4 },
  table: { width: '100%', borderCollapse: 'collapse' as const, fontSize: 13 },
  th: { textAlign: 'left' as const, padding: '8px 12px', color: '#64748b', borderBottom: '1px solid #2d3148', fontWeight: 500 },
  td: { padding: '8px 12px', borderBottom: '1px solid #1e2235', color: '#cbd5e1' },
  badge: { display: 'inline-block', padding: '2px 8px', borderRadius: 4, fontSize: 11, fontWeight: 600, background: '#1e2a4a', color: '#818cf8' },
  expandBtn: { background: 'transparent', border: 'none', color: '#64748b', cursor: 'pointer', fontSize: 11, padding: 2 },
}

export default function Status() {
  const [data, setData] = useState<StatusResponse | null>(null)
  const [rows, setRows] = useState<Decision[]>([])
  const [expanded, setExpanded] = useState<string | null>(null)

  const load = async () => {
    const s = await getStatus()
    setData(s)
    if (s.recent_selections) {
      setRows(prev => {
        const map = new Map(prev.map(r => [r.timestamp, r]))
        for (const r of s.recent_selections) map.set(r.timestamp, r as Decision)
        return Array.from(map.values()).slice(0, 50)
      })
    }
  }

  useEffect(() => {
    load()
    const id = setInterval(load, 5000)
    return () => clearInterval(id)
  }, [])

  if (!data) return <p style={{ color: '#64748b' }}>Loading…</p>

  return (
    <div>
      <h1 style={s.h1}>Router Status</h1>
      <div style={s.cards}>
        <div style={s.card}><div style={s.cardNum}>{data.active_providers}</div><div style={s.cardLbl}>Active providers</div></div>
        <div style={s.card}><div style={s.cardNum}>{data.registered_models}</div><div style={s.cardLbl}>Registered models</div></div>
        <div style={s.card}><div style={s.cardNum}>{data.recent_selections.length}</div><div style={s.cardLbl}>Recent requests</div></div>
      </div>
      <table style={s.table}>
        <thead>
          <tr>
            <th style={s.th}>Model</th>
            <th style={s.th}>Endpoint</th>
            <th style={s.th}>Task</th>
            <th style={s.th}>Score</th>
            <th style={s.th}>Time</th>
            <th style={{ ...s.th, width: 40 }}></th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr><td style={{ ...s.td, color: '#475569' }} colSpan={6}>No requests yet.</td></tr>
          )}
          {rows.map((r, i) => {
            const key = r.timestamp + i
            const open = expanded === key
            return (
              <React.Fragment key={key}>
                <tr style={{ cursor: 'pointer' }} onClick={() => setExpanded(open ? null : key)}>
                  <td style={s.td}><code style={{ color: '#a5b4fc' }}>{r.model_id}</code></td>
                  <td style={s.td}>{r.endpoint_name}</td>
                  <td style={s.td}>
                    <span style={s.badge}>{r.task_type}</span>
                    {r.reasoning_depth && <span style={{ ...s.badge, background: '#1e293b', color: '#94a3b8', marginLeft: 4 }}>{r.reasoning_depth}</span>}
                  </td>
                  <td style={s.td}>{r.score.toFixed(4)}</td>
                  <td style={s.td}>{new Date(r.timestamp).toLocaleTimeString()}</td>
                  <td style={{ ...s.td, textAlign: 'center' }}><button style={s.expandBtn}>{open ? '▼' : '▶'}</button></td>
                </tr>
                {open && (
                  <tr>
                    <td colSpan={6} style={{ padding: 0, background: '#12141e' }}>
                      <div style={{ padding: '12px 16px', fontSize: 12, color: '#94a3b8' }}>
                        <div style={{ marginBottom: 8 }}>
                          <strong style={{ color: '#e2e8f0' }}>Classification:</strong>
                          <span style={{ marginLeft: 8 }}>{JSON.stringify(r.task)}</span>
                        </div>
                        {r.top_candidates && r.top_candidates.length > 0 && (
                          <div>
                            <strong style={{ color: '#e2e8f0' }}>Top candidates:</strong>
                            <table style={{ ...s.table, marginTop: 6, fontSize: 12 }}>
                              <thead>
                                <tr><th style={s.th}>#</th><th style={s.th}>Model</th><th style={s.th}>Endpoint</th><th style={{ ...s.th, textAlign: 'right' as const }}>Score</th></tr>
                              </thead>
                              <tbody>
                                {r.top_candidates.map((c, ci) => (
                                  <tr key={ci}>
                                    <td style={s.td}>{ci + 1}</td>
                                    <td style={s.td}><code style={{ color: '#a5b4fc' }}>{c.model_id}</code></td>
                                    <td style={s.td}>{c.endpoint_name}</td>
                                    <td style={{ ...s.td, textAlign: 'right' as const, color: ci === 0 ? '#4ade80' : '#94a3b8' }}>{c.score.toFixed(4)}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        )}
                      </div>
                    </td>
                  </tr>
                )}
              </React.Fragment>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
