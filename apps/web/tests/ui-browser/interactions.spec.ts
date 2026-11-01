import { test, expect } from "./fixtures";
import { assertPageStructure, capturePair, openDemo, seedStorageSentinels, storageSnapshot } from "./helpers";
import { demoUrl, FIXTURE_REPORT_ID, FIXTURE_RUN_ID } from "./routes";

test("spot review traps focus, Escape dismisses, and focus returns to its trigger", async ({ page }, testInfo) => {
  await openDemo(page, "view=paper");
  const trigger = page.getByRole("button", { name: "가상 주문 검토", exact: true });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "현물 가상 주문 검토" });
  await expect(dialog).toBeVisible();
  const confirm = dialog.getByRole("button", { name: "예시 검토 확인", exact: true });
  const close = dialog.getByRole("button", { name: "닫기", exact: true });
  await expect(dialog.getByRole("heading", { name: "현물 가상 주문 검토", exact: true })).toBeFocused();
  await page.keyboard.press("Shift+Tab"); await expect(close).toBeFocused();
  await page.keyboard.press("Tab"); await expect(confirm).toBeFocused();
  await page.keyboard.press("Tab"); await expect(close).toBeFocused();
  await page.keyboard.press("Tab"); await expect(confirm).toBeFocused();
  await capturePair(page, testInfo, "interaction-spot-review-dialog");
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
});

test("spot fixture confirmation preserves stored pending records and balances", async ({ page }) => {
  await seedStorageSentinels(page); await openDemo(page, "view=paper");
  const before = await storageSnapshot(page);
  const balance = await page.locator("#page-content").innerText();
  await page.getByRole("button", { name: "가상 주문 검토", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "예시 검토 확인", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("status")).toContainText("실제 장부와 외부 서비스는 변경하지 않습니다");
  expect(await storageSnapshot(page)).toEqual(before);
  expect(balance).toContain("10,000.00");
  await expect(page.locator(".order-input")).toContainText("사용 가능 10,000.00 USDT");
});

test("recommendation fixture confirmation never creates an exchange or ledger intent", async ({ page }) => {
  await seedStorageSentinels(page); await openDemo(page, "view=recommend");
  const before = await storageSnapshot(page);
  await page.getByRole("checkbox", { name: "가격과 추천 시점 재확인" }).check();
  await page.getByRole("checkbox", { name: "위험 한도와 독립 장부 확인" }).check();
  await page.getByRole("checkbox", { name: "승인 대상 · 유효시간 확인" }).check();
  await page.getByRole("button", { name: /승인 효과 검토/ }).click();
  const dialog = page.getByRole("dialog", { name: "추천 승인 효과 확인" });
  await expect(dialog).toContainText("요청 접수는 실제 체결과 종료 확인을 뜻하지 않습니다");
  await dialog.getByRole("button", { name: "예시 승인 검토 확인" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("status")).toContainText("실제 주문이나 장부 변경은 없습니다");
  expect(await storageSnapshot(page)).toEqual(before);
});

for (const scenario of [
  { key: "create", prepare: "설정 검토" },
  { key: "backtest", prepare: "가상 검증 검토" },
]) {
  test(`research ${scenario.key} fixture confirmation preserves local storage`, async ({ page }) => {
    await seedStorageSentinels(page); await openDemo(page, `view=research&tab=${scenario.key}`);
    const before = await storageSnapshot(page);
    if (scenario.key === "create") await page.getByRole("checkbox", { name: "설정과 독립 장부를 확인했습니다." }).check();
    await page.getByRole("button", { name: scenario.prepare, exact: true }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toHaveCount(1);
    await dialog.getByRole("button", { name: "예시 검토 완료", exact: true }).click();
    await expect(dialog).toHaveCount(0);
    expect(await storageSnapshot(page)).toEqual(before);
  });
}

test("menu navigation preserves fixture mode through browser back and forward", async ({ page }) => {
  await openDemo(page, "view=overview");
  const mobile = page.viewportSize()!.width <= 768;
  const nav = page.getByRole("navigation", { name: mobile ? "모바일 메뉴" : "주요 메뉴" });
  await nav.getByRole("button", { name: mobile ? "시장" : "시장 · 차트", exact: true }).click();
  await assertPageStructure(page, "시장 · 차트");
  expect(new URL(page.url()).searchParams.get("demo")).toBe("1");
  await page.goBack(); await assertPageStructure(page, "종합 현황");
  await page.goForward(); await assertPageStructure(page, "시장 · 차트");
});

test("known run and report IDs survive direct URL loads", async ({ page }) => {
  await openDemo(page, `view=research&tab=run&run_id=${FIXTURE_RUN_ID}`);
  await assertPageStructure(page, "자동실행");
  await expect(page.locator(".run-banner")).toBeVisible();
  expect(new URL(page.url()).searchParams.get("run_id")).toBe(FIXTURE_RUN_ID);
  await openDemo(page, `view=research&tab=report&report_id=${FIXTURE_REPORT_ID}`);
  await assertPageStructure(page, "검증 결과");
  await expect(page.locator(".report-heading")).toBeVisible();
  expect(new URL(page.url()).searchParams.get("report_id")).toBe(FIXTURE_REPORT_ID);
});

test("history selection shows its fixed snapshot before opening that exact run", async ({ page }) => {
  await openDemo(page, "view=research&tab=history");
  await page.getByRole("button", {name: /저장된 실행 2/}).click();
  await expect(page.getByRole("heading", {name: "선택한 실행 상세", exact: true})).toBeVisible();
  await expect(page.locator(".definitions > div").filter({has: page.getByText("정리한 가상손익", {exact:true})}).locator("dd")).toHaveText("-12.50 USDT");
  expect(new URL(page.url()).searchParams.get("tab")).toBe("history");
  await page.getByRole("button", {name: "실행 상세 보기", exact: true}).click();
  await expect(page.locator(".run-banner")).toContainText("종료");
  expect(new URL(page.url()).searchParams.get("run_id")).toBe("reference-stopped");
});

test("report details explain stored segments and allow returning to configuration", async ({ page }) => {
  await openDemo(page, `view=research&tab=report&report_id=${FIXTURE_REPORT_ID}`);
  await page.getByRole("button", {name: "검증 상세 보기", exact: true}).click();
  const dialog=page.getByRole("dialog", {name: "검증 상세", exact: true});
  await expect(dialog).toContainText("마지막 미사용 구간");
  await expect(dialog).toContainText("KST");
  await expect(dialog).toContainText("적용 수수료율");
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await page.getByRole("button", {name: "조건 다시 설정", exact: true}).click();
  await assertPageStructure(page, "백테스트 설정");
});

test("unknown saved IDs never display another run or report as the requested result", async ({ page }) => {
  await openDemo(page, "view=research&tab=run&run_id=missing-run");
  await expect(page.locator(".run-banner")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "실행을 선택하세요", exact: true })).toBeVisible();
  await openDemo(page, "view=research&tab=report&report_id=missing-report");
  await expect(page.locator(".report-heading")).toHaveCount(0);
  await expect(page.getByText("검증 결과를 선택하세요.", { exact: true })).toBeVisible();
});

test("malformed identifiers and unknown route names stay within the fixture app", async ({ page }) => {
  for (const invalid of ["../private", "bad\u0000id", "a".repeat(129)]) {
    await openDemo(page, `view=research&tab=run&run_id=${encodeURIComponent(invalid)}`);
    await assertPageStructure(page, "자동실행");
    expect(await page.locator("#page-content").innerText()).not.toContain(invalid);
  }
  await page.goto(demoUrl("view=not-a-real-screen"));
  await expect(page.getByRole("heading", { level: 1 })).toContainText("종합 현황");
});
