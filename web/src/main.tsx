import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { applyTheme, getStoredMode, getStoredTheme } from './lib/theme'

// Apply the stored appearance before first paint to avoid a theme flash.
applyTheme(getStoredTheme(), getStoredMode())

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
