import { test, expect } from "./fixtures";
import type { Page } from "@playwright/test";
import { assertPageStructure, capturePair, openDemo } from "./helpers";
import { demoRoutes } from "./routes";

async function assertReferenceContent(page: Page, screenId: string, mobile: boolean) {
  if (screenId === "06-run") await expect(page.getByRole("list", {name: "최근 처리 기록 목록"}).getByRole("listitem")).toHaveCount(3);
  if (screenId === "08-history") {
    await expect(page.getByRole("heading", {name: "선택한 실행 상세", exact: true})).toBeVisible();
    await expect(page.locator(".unavailable-history-row")).toContainText("손익 미확인");
  }
  if (screenId === "09-backtest" && mobile) {
    for (const input of await page.locator('input[type="datetime-local"]').all()) {
      expect((await input.boundingBox())!.width).toBeGreaterThan(220);
    }
  }
  if (screenId === "10-report") {
    await expect(page.getByRole("button", {name: "조건 다시 설정", exact: true})).toBeVisible();
    await expect(page.getByRole("button", {name: "검증 상세 보기", exact: true})).toBeVisible();
    if (mobile) {
      for (const value of await page.locator(".report-heading + .metric-strip strong").all()) expect(await value.evaluate(e=>e.scrollWidth<=e.clientWidth)).toBe(true);
      if (page.viewportSize()!.width >= 390) expect((await page.locator(".evaluation-curve").boundingBox())!.y).toBeLessThan(page.viewportSize()!.height-100);
    }
  }
  if (screenId === "12-diagnostics" && mobile) {
    const banner=(await page.locator(".diagnostic-banner").boundingBox())!;
    const badge=(await page.locator(".diagnostic-banner > .badge").boundingBox())!;
    expect(badge.x).toBeGreaterThanOrEqual(banner.x);
    expect(badge.x+badge.width).toBeLessThanOrEqual(banner.x+banner.width);
  }
  if (screenId === "04-recommend") await expect(page.locator(".chart:visible")).toHaveCount(1);
  if (screenId === "03-account" && mobile) {
    for (const value of await page.locator(".account-summary-values strong,.account-position-values dd").all()) await expect(value).toHaveCSS("white-space", "nowrap");
  }
}

for (const route of demoRoutes) {
  test(`${route.screenId}: shared fixture route`, async ({ page }, testInfo) => {
    await openDemo(page, route.query);
    await assertPageStructure(page, route.title);
    await assertReferenceContent(page, route.screenId, testInfo.project.use.isMobile === true);
    await capturePair(page, testInfo, route.screenId);
  });
}

for (const width of [320, 360]) {
  for (const route of demoRoutes) {
    test(`narrow ${width}: ${route.screenId}`, async ({ page }, testInfo) => {
      test.skip(testInfo.project.use.isMobile !== true, "Narrow-width coverage belongs to the mobile project.");
      await page.setViewportSize({ width, height: 844 });
      await openDemo(page, route.query);
      await assertPageStructure(page, route.title);
      await assertReferenceContent(page, route.screenId, true);
      await capturePair(page, testInfo, `narrow-${width}-${route.screenId}`);
    });
  }
}
