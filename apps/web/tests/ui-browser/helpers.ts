import { existsSync } from "node:fs";
import { writeFile } from "node:fs/promises";
import { expect, type Page, type TestInfo } from "@playwright/test";
import { FIXED_TIME, demoUrl } from "./routes";

export async function settleFrames(page: Page) {
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
}

async function openApp(page: Page, query: string, demo: boolean) {
  await page.goto(demo ? demoUrl(query) : `/?${query}`);
  await expect(page.locator(".trading-app")).toBeVisible();
  await expect(page.locator("#page-content")).toBeVisible();
  await page.evaluate(async () => {
    await document.fonts.load('16px "Pretendard"', "한글 화면 확인");
    await document.fonts.ready;
  });
  await expect.poll(() => page.evaluate(() => Array.from(document.fonts).some(face => face.family.replace(/["']/g, "") === "Pretendard" && face.status === "loaded")), { message: "The bundled Korean font must load before visual comparison." }).toBe(true);
  await page.waitForLoadState("networkidle");
  await page.addStyleTag({ content: "*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}" });
  await settleFrames(page);
  if (demo) await expect.poll(() => page.locator(".chart:visible").evaluateAll(charts => charts.every(chart => Array.from(chart.querySelectorAll("canvas")).some(canvas => canvas.width > 0 && canvas.height > 0))), { message: "Visible chart canvases must finish layout." }).toBe(true);
  await page.mouse.move(0, 0);
}

export const openDemo = (page: Page, query: string) => openApp(page, query, true);
export const openOffline = (page: Page, query: string) => openApp(page, query, false);

export async function assertPageStructure(page: Page, title: string, demo = true) {
  await expect(page.locator("h1")).toHaveCount(1);
  await expect(page.getByRole("heading", { level: 1 })).toContainText(title);
  await expect(page.locator(".header-time")).toContainText(demo ? "예시 데이터" : "서버 응답으로 상태 확인");
  await expect(page.locator("body")).not.toContainText("\uFFFD");
  expect(await page.evaluate(() => Date.now())).toBe(Date.parse(FIXED_TIME));
  const layout = await page.evaluate(() => ({
    width: document.documentElement.clientWidth,
    scroll: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth),
  }));
  expect(layout.scroll, `Body overflow: ${layout.scroll}px exceeds viewport ${layout.width}px.`).toBeLessThanOrEqual(layout.width);
}

type ScreenshotOptions = NonNullable<Parameters<Page['screenshot']>[0]>;

// Candidate images must satisfy the same consecutive-pixel stability premise as
// Playwright's strict screenshot assertion. This writes output artifacts only.
export async function captureStableImage(page: Page, options: ScreenshotOptions) {
  const maxAttempts = 8;
  let previous: Buffer | undefined;
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    await page.evaluate(async () => { await document.fonts.ready; });
    await settleFrames(page);
    const current = await page.screenshot(options);
    if (previous?.equals(current)) return { image: current, attempts: attempt };
    previous = current;
  }
  throw new Error('Screenshot pixels did not stabilize in ' + maxAttempts +
    ' consecutive captures (' + (options.fullPage ? 'full page' : 'viewport') +
    '). Investigate font, canvas, focus, or layout changes; no candidate was accepted.');
}

export async function capturePair(page: Page, testInfo: TestInfo, stem: string) {
  await page.evaluate(() => window.scrollTo(0, 0));
  await settleFrames(page);
  for (const fullPage of [false, true]) {
    const name = `${stem}-${fullPage ? "full" : "viewport"}.png`;
    const options = { fullPage, animations: "disabled" as const, caret: "hide" as const, scale: "css" as const };
    const candidatePath = testInfo.outputPath(`baseline-${name}`);
    const stable = await captureStableImage(page, options);
    await writeFile(candidatePath, stable.image);
    await testInfo.attach(name, { path: candidatePath, contentType: "image/png" });
    const captureOnly = process.env.UI_CAPTURE_BASELINE === "1";
    await testInfo.attach(`${name}-manifest`, { body: Buffer.from(JSON.stringify({
      project: testInfo.project.name, name, candidate: candidatePath,
      baseline: testInfo.snapshotPath(name), viewport: page.viewportSize(), fullPage,
      mode: captureOnly ? "capture-only-needs-review" : "strict-comparison",
      candidateStability: { consecutiveExactImages: 2, attempts: stable.attempts, maxAttempts: 8 },
    }, null, 2)), contentType: "application/json" });
    if (!captureOnly) {
      expect(testInfo.config.updateSnapshots, "Baseline updates must remain disabled.").toBe("none");
      if (!existsSync(testInfo.snapshotPath(name))) throw new Error(`Reviewed screenshot baseline is missing: ${testInfo.snapshotPath(name)}. Capture candidates with UI_CAPTURE_BASELINE=1; review and commit them explicitly.`);
      await expect(page).toHaveScreenshot(name, { ...options, maxDiffPixels: 0, threshold: 0 });
    }
  }
}

export async function storageSnapshot(page: Page) {
  return page.evaluate(() => Object.fromEntries(Object.keys(localStorage).sort().map(key => [key, localStorage.getItem(key)])));
}

export async function seedStorageSentinels(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem("ui-fixture-preserve", "unchanged");
    localStorage.setItem("spot-paper-pending-v2", '{"fixtureMustIgnore":"preserve"}');
    localStorage.setItem("phase4-uncertain-intent-v1", '{"fixtureMustIgnore":"preserve"}');
  });
}
