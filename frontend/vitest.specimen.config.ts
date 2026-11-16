import { playwright } from '@vitest/browser-playwright'
import { defineConfig } from 'vitest/config'

// Screenshots of the chart-kit specimen in Chromium and WebKit, at a desktop and a
// phone width. Each capture is saved under specimen-shots/ for review; the run fails
// if a width scrolls the page sideways. The browser window is larger than any test
// frame, so captures are taken at full size (and at twice the pixel density) rather
// than scaled down to fit.
export default defineConfig({
  // Optimised up front, so Vite does not reload the page part way through a run.
  optimizeDeps: { include: ['react/jsx-dev-runtime'] },
  test: {
    include: ['src/**/*.shots.tsx'],
    // Thirty-seven captures per width at twice the pixel density; WebKit on Linux is slow at it.
    testTimeout: 120_000,
    setupFiles: ['./src/test/setup.ts', './src/test/styles.ts'],
    browser: {
      enabled: true,
      headless: true,
      viewport: { width: 1440, height: 900 },
      provider: playwright({
        contextOptions: { viewport: { width: 1600, height: 2000 }, deviceScaleFactor: 2 },
      }),
      instances: [{ browser: 'chromium' }, { browser: 'webkit' }],
    },
  },
})
