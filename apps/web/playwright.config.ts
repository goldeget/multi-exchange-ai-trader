import {defineConfig} from '@playwright/test';

export default defineConfig({
  testDir: './tests/ui-browser',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30_000,
  forbidOnly: !!process.env.CI,
  reporter: [['list'], ['html', {open: 'never'}]],
  outputDir: 'test-results',
  snapshotPathTemplate: '{testDir}/baselines/{projectName}/{arg}{ext}',
  // Capture is explicit and separate. Ordinary CI must never accept new pixels.
  updateSnapshots: 'none',
  expect: {toHaveScreenshot: {animations: 'disabled', maxDiffPixels: 0}},
  use: {
    baseURL: 'http://127.0.0.1:4174',
    browserName: 'chromium',
    // Use Chromium's web-test CPU path across different hosted runner CPUs.
    // Reviewed pixels and zero-difference assertions remain unchanged.
    launchOptions: {args: ['--disable-skia-runtime-opts', '--disable-accelerated-2d-canvas']},
    locale: 'ko-KR',
    timezoneId: 'Asia/Seoul',
    deviceScaleFactor: 1,
    colorScheme: 'dark',
    reducedMotion: 'reduce',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {name: 'pc', use: {viewport: {width: 1440, height: 1024}}},
    {name: 'mobile', use: {viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true}},
  ],
  webServer: {
    command: 'node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 4174 --strictPort',
    url: 'http://127.0.0.1:4174',
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
