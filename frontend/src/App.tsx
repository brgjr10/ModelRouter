import React, { useEffect, useState } from 'react'
import { BrowserRouter, Routes, Route, NavLink, Navigate } from 'react-router-dom'
import { getProviders } from './api'
import SetupWizard from './pages/SetupWizard'
import Providers from './pages/Providers'
import Status from './pages/Status'
import Decisions from './pages/Decisions'
import Weights from './pages/Weights'

const styles: Record<string, React.CSSProperties> = {
  nav: {
    display: 'flex', alignItems: 'center', gap: 8,
    background: '#1a1d2e', borderBottom: '1px solid #2d3148',
    padding: '0 24px', height: 52,
  },
  logo: { fontWeight: 700, fontSize: 16, color: '#818cf8', marginRight: 16, letterSpacing: 1 },
  link: { color: '#94a3b8', textDecoration: 'none', fontSize: 14, padding: '4px 10px', borderRadius: 6 },
  activeLink: { color: '#e2e8f0', background: '#2d3148' },
  main: { padding: 24, maxWidth: 1100, margin: '0 auto' },
}

export default function App() {
  const [ready, setReady] = useState<boolean | null>(null)
  const [hasProviders, setHasProviders] = useState(true)

  useEffect(() => {
    getProviders()
      .then(ps => { setHasProviders(ps.some(p => p.is_enabled)); setReady(true) })
      .catch(() => setReady(true))
  }, [])

  if (ready === null) return <div style={{ padding: 32, color: '#64748b' }}>Loading…</div>

  return (
    <BrowserRouter>
      <nav style={styles.nav}>
        <span style={styles.logo}>⚡ ModelRouter</span>
        <NavLink to="/providers" style={({ isActive }) => ({ ...styles.link, ...(isActive ? styles.activeLink : {}) })}>Providers</NavLink>
        <NavLink to="/status" style={({ isActive }) => ({ ...styles.link, ...(isActive ? styles.activeLink : {}) })}>Status</NavLink>
        <NavLink to="/decisions" style={({ isActive }) => ({ ...styles.link, ...(isActive ? styles.activeLink : {}) })}>Decisions</NavLink>
        <NavLink to="/weights" style={({ isActive }) => ({ ...styles.link, ...(isActive ? styles.activeLink : {}) })}>Weights</NavLink>
      </nav>
      <main style={styles.main}>
        <Routes>
          <Route path="/" element={<Navigate to="/providers" replace />} />
          <Route path="/setup" element={<SetupWizard onComplete={() => setHasProviders(true)} />} />
          <Route path="/providers" element={<Providers />} />
          <Route path="/status" element={<Status />} />
          <Route path="/decisions" element={<Decisions />} />
          <Route path="/weights" element={<Weights />} />
          <Route path="*" element={<Navigate to="/providers" replace />} />
        </Routes>
      </main>
    </BrowserRouter>
  )
}
