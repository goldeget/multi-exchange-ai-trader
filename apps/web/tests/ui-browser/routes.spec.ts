import { test } from "./fixtures";
import { assertPageStructure, capturePair, openDemo } from "./helpers";
import { demoRoutes } from "./routes";

for (const route of demoRoutes) {
  test(`${route.key}: shared fixture route`, async ({ page }, testInfo) => {
    await openDemo(page, route.query);
    await assertPageStructure(page, route.title);
    await capturePair(page, testInfo, route.key);
  });
}

for (const width of [320, 360]) {
  for (const route of demoRoutes) {
    test(`narrow ${width}: ${route.key}`, async ({ page }, testInfo) => {
      test.skip(testInfo.project.use.isMobile !== true, "Narrow-width coverage belongs to the mobile project.");
      await page.setViewportSize({ width, height: 844 });
      await openDemo(page, route.query);
      await assertPageStructure(page, route.title);
      await capturePair(page, testInfo, `narrow-${width}-${route.key}`);
    });
  }
}
