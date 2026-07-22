import React, { useEffect, useState } from 'react'
import { getDecisionsLive } from '../api'

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
  card: { background: '#1a1d2e', border: '1px solid #2d3148', borderRadius: 10, marginBottom: 12, overflow: 'hidden' },
  row: { display: 'grid', gridTemplateColumns: '140px 1fr 80px 80px', gap: 12, padding: '10px 16px', borderBottom: '1px solid #232840', alignItems: 'center' },
  label: { color: '#64748b', fontSize: 12, fontWeight: 500 },
  value: { color: '#e2e8f0', fontSize: 13, fontWeight: 600 },
  mono: { fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace', fontSize: 12, color: '#a5b4fc' },
  taskGrid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 8, padding: '10px 16px' },
  taskItem: { background: '#0f1117', border: '1px solid #2d3148', borderRadius: 6, padding: '6px 10px' },
  taskLabel: { color: '#64748b', fontSize: 11 },
  taskValue: { color: '#e2e8f0', fontSize: 12, fontWeight: 600, marginTop: 2 },
  badge: { display: 'inline-block', padding: '2px 8px', borderRadius: 999, fontSize: 11, fontWeight: 600 },
  candidateTable: { width: '100%', borderCollapse: 'collapse' as const, fontSize: 12, marginTop: 4 },
  th: { textAlign: 'left' as const, padding: '4px 8px', color: '#64748b', fontWeight: 500 },
  td: { padding: '4px 8px', color: '#94a3b8', borderTop: '1px solid #1e2235' },
}

export default function Decisions() {
  const [rows, setRows] = useState<Decision[]>([])
  const [expanded, setExpanded] = useState<string | null>(null)

  useEffect(() => {
    const es = new EventSource('/api/decisions/live')
    es.onmessage = (e) => {
      try {
        const d: Decision = JSON.parse(e.data)
        setRows(prev => [d, ...prev].slice(0, 100))
      } catch {}
    }
    return () => es.close()
  }, [])

  return (
    <div>
      <h1 style={s.h1}>Router Decisions</h1>
      <p style={{ color: '#64748b', fontSize: 13, marginBottom: 16 }}>
        Live trace of model selections. Each row shows the classified task and the top-3 candidates with scores.
      </p>
      {rows.length === 0 && <p style={{ color: '#475569' }}>No decisions yet. Send a request through the router to see traces.</p>}
      {rows.map((d, idx) => {
        const key = `${d.timestamp}-${idx}`
        const isOpen = expanded === key
        return (
          <div key={key} style={s.card}>
            <div style={s.row} onClick={() => setExpanded(isOpen ? null : key)}>
              <div>
                <div style={s.label}>Selected</div>
                <div style={{ ...s.value, color: '#4ade80' }}>{d.model_id}</div>
                <div style={{ fontSize: 11, color: '#64748b' }}>{d.endpoint_name}</div>
              </div>
              <div>
                <div style={s.label}>Task</div>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 4 }}>
                  <span style={{ ...s.badge, background: '#2d3148', color: '#e2e8f0' }}>{d.task_type}</span>
                  <span style={{ ...s.badge, background: '#1e293b', color: '#94a3b8' }}>{d.reasoning_depth}</span>
                </div>
                {isOpen && (
                  <div style={{ marginTop: 10 }}>
                    <div style={s.label}>Full classification</div>
                    <div style={s.taskGrid}>
                      {Object.entries(d.task || {}).filter(([k]) => k !== 'task_type' && k !== 'reasoning_depth').map(([k, v]) => (
                        <div key={k} style={s.taskItem}>
                          <div style={s.taskLabel}>{k}</div>
                          <div style={s.taskValue}>{String(v)}</div>
                        </div>
                      ))}
                    </div>
                    {d.top_candidates && d.top_candidates.length > 0 && (
                      <div style={{ marginTop: 12 }}>
                        <div style={s.label}>Top candidates</div>
                        <table style={s.candidateTable}>
                          <thead>
                            <tr>
                              <th style={s.th}>Rank</th>
                              <th style={s.th}>Model</th>
                              <th style={s.th}>Endpoint</th>
                              <th style={{ ...s.th, textAlign: 'right' as const }}>Score</th>
                            </tr>
                          </thead>
                          <tbody>
                            {d.top_candidates.map((c, ci) => (
                              <tr key={ci}>
                                <td style={s.td}>{ci + 1}</td>
                                <td style={s.td}><code style={s.mono}>{c.model_id}</code></td>
                                <td style={s.td}>{c.endpoint_name}</td>
                                <td style={{ ...s.td, textAlign: 'right' as const, color: ci === 0 ? '#4ade80' : '#94a3b8' }}>{c.score.toFixed(4)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                )}
              </div>
              <div style={{ textAlign: 'right' as const }}>
                <div style={s.label}>Score</div>
                <div style={{ ...s.value, color: d.score >= 0 ? '#4ade80' : '#f87171' }}>{d.score.toFixed(4)}</div>
                <div style={{ fontSize: 10, color: '#475569', marginTop: 2 }}>{new Date(d.timestamp).toLocaleTimeString()}</div>
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}
