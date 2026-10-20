import React from 'react'
import ReactDOM from 'react-dom/client'
import './fonts.css'
import './tokens.css'
import App from './App'
import ErrorBoundary from './components/ErrorBoundary'
import { api } from './api'
import { installErrorReports } from './lib/reportErrors'

installErrorReports()

// Settings → Display → Scale, put back at every start.
api.display.scale().then((r) => window.gamecore?.setUiScale?.(r.scale)).catch(() => {})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    {/* Last net. Anything that escapes a surface boundary lands here rather
       than on a white screen the user cannot recover from without a mouse. */}
    <ErrorBoundary fallback={
      <div style={{
        width: '100%', height: '100%', display: 'flex', alignItems: 'center',
        justifyContent: 'center', flexDirection: 'column', gap: 10,
        fontFamily: 'var(--gc-font-ui)', color: '#fff', textAlign: 'center', padding: 40,
      }}>
        <div style={{ fontSize: 18, fontWeight: 700 }}>GameCore could not start the interface</div>
        <div style={{ fontSize: 15, color: 'var(--gc-ink-2)' }}>
          Hold L1 + R1 for 2 seconds to force the default theme, then restart.
        </div>
      </div>
    }>
      <App />
    </ErrorBoundary>
  </React.StrictMode>
)
