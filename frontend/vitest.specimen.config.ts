import { playwright } from '@vitest/browser-playwright'
import { defineConfig } from 'vitest/config'

// Screenshots of the chart-kit specimen in Chromium and WebKit, at a desktop and a
// phone width. Each capture is saved under specimen-shots/ for review; the run fails
// if a width scrolls the page sideways.
export default defineConfig({
  // Optimised up front, so Vite does not reload the page part way through a run.
  optimizeDeps: { include: ['react/jsx-dev-runtime'] },
  test: {
    include: ['src/**/*.shots.tsx'],
    setupFiles: ['./src/test/setup.ts', './src/test/styles.ts'],
    browser: {
      enabled: true,
      headless: true,
      viewport: { width: 1440, height: 900 },
      provider: playwright(),
      instances: [{ browser: 'chromium' }, { browser: 'webkit' }],
    },
  },
})
