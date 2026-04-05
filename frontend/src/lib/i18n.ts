import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'

import ptBR from '@/locales/pt-BR.json'
import en from '@/locales/en.json'

function syncHtmlLang(lng: string) {
  document.documentElement.lang = lng
}

i18n
  .use(initReactI18next)
  .init({
    resources: {
      'pt-BR': { translation: ptBR },
      en: { translation: en },
    },
    lng: 'pt-BR',
    fallbackLng: 'pt-BR',
    interpolation: {
      escapeValue: false,
    },
  })

syncHtmlLang(i18n.language)
i18n.on('languageChanged', syncHtmlLang)

export default i18n
