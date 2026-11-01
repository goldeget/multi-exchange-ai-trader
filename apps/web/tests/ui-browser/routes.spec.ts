import { test, expect } from "./fixtures";
import type { Page } from "@playwright/test";
import { assertPageStructure, capturePair, openDemo } from "./helpers";
import { demoRoutes } from "./routes";

async function assertReferenceContent(page: Page, screenId: string, mobile: boolean) {
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
