import i18next, { type Resource } from 'i18next'
import { initReactI18next } from 'react-i18next'
import en from './en.json'

export const catalogue = en
export const LANGUAGE = 'en'

/** The app's one translator. Every visible string goes through it. */
export function createI18n(resources: Resource = { [LANGUAGE]: { translation: en } }) {
  const instance = i18next.createInstance()
  void instance.use(initReactI18next).init({
    lng: LANGUAGE,
    fallbackLng: LANGUAGE,
    resources,
    initAsync: false,
    interpolation: { escapeValue: false },
    returnNull: false,
  })
  return instance
}

export const i18n = createI18n()
