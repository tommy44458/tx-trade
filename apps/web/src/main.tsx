import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './i18n/index.ts'
import { initializeUiTheme } from './uiTheme.ts'
import App from './App.tsx'
import './MobileLayout.css'

initializeUiTheme()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
