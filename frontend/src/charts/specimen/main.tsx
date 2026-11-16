// The chart-kit specimen, served by the development server at /specimen.html. The
// production build has one entry, index.html, so none of this ships.
import '@fontsource-variable/inter'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { I18nextProvider } from 'react-i18next'
import '../../index.css'
import { specimenI18n } from './i18n'
import { SpecimenPage } from './SpecimenPage'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <I18nextProvider i18n={specimenI18n()}>
      <SpecimenPage />
    </I18nextProvider>
  </StrictMode>,
)
