import { playwright } from '@vitest/browser-playwright'
import { defineConfig } from 'vitest/config'

// The UI suite in WebKit, the engine of the desktop app's web view on macOS
// (WKWebView) and Linux (WebKitGTK). These read source or theme files with Node and
// check code and data rather than rendering, so they run with the jsdom suite only.
export const NODE_ONLY = [
  'src/charts/geometry.test.ts',
  'src/i18n/keys.test.ts',
  'src/i18n/literals.test.ts',
  'src/theme/dark.test.ts',
  'src/theme/figures.test.ts',
  'src/theme/tokens.test.ts',
]

export default defineConfig({
  test: {
    include: ['src/**/*.test.{ts,tsx}'],
    exclude: NODE_ONLY,
    setupFiles: ['./src/test/setup.ts', './src/test/styles.ts'],
    clearMocks: true,
    restoreMocks: true,
    unstubEnvs: true,
    unstubGlobals: true,
    browser: {
      enabled: true,
      headless: true,
      // Desktop width: below 1180px the layout moves navigation into the drawer.
      viewport: { width: 1280, height: 900 },
      provider: playwright(),
      instances: [{ browser: 'webkit' }],
    },
  },
})
