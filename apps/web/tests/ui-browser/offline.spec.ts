import { offlineTest as test, expect } from "./fixtures";
import { assertPageStructure, capturePair, openOffline } from "./helpers";
import { demoRoutes } from "./routes";

for (const route of demoRoutes) {
  test(`actual offline: ${route.screenId} fails closed`, async ({ page }, testInfo) => {
    await openOffline(page, route.query);
    await assertPageStructure(page, route.title, false);
    await expect(page.locator('[data-source="FIXTURE"]')).toHaveCount(0);
    await expect.poll(() => page.locator('main').innerText(), { message: "Unavailable API reads must produce a visible failure state." }).toMatch(/확인하지 못|연결하지 못|연결 안 됨|미확인|실패|조회하지 못|조회 실패/);
    await expect(page.getByRole("dialog")).toHaveCount(0);
    const confirmation = page.getByRole("button", { name: /^(가상 주문 확정|설명한 승인 효과 실행|이대로 적용|예시 검토 확인|예시 승인 검토 확인)$/ });
    for (const button of await confirmation.all()) await expect(button).toBeDisabled();

    if (route.screenId === "01-overview") {
      const values = page.locator(".metric-strip strong");
      await expect(values).toHaveCount(3);
      await expect(values).toHaveText(["—", "—", "—"]);
      await expect(page.locator(".overview-market .price-bar strong")).toHaveText("— USDT");
      await expect(page.locator(".live-overview")).not.toContainText("10,000.00");
    } else if (route.screenId === "03-account") {
      await expect(page.locator(".account-summary-values")).toContainText("확인 불가");
      await expect(page.locator(".account-summary-values")).not.toContainText("0.00");
    } else if (route.screenId === "05-paper") {
      await expect(page.getByRole("button", { name: "가상 주문 검토", exact: true })).toBeDisabled();
      await expect(page.getByRole("button", { name: "새 현물 장부 만들기", exact: true })).toBeDisabled();
      await expect(page.locator(".order-input")).toContainText("사용 가능 — USDT");
      await expect(page.locator(".order-input")).not.toContainText("사용 가능 0");
    } else if (route.screenId === "07-create") {
      await expect(page.getByRole("button", { name: "설정 검토", exact: true })).toBeDisabled();
    } else if (route.screenId === "09-backtest") {
      await expect(page.getByRole("button", { name: "가상 검증 검토", exact: true })).toBeDisabled();
    } else if (route.screenId === "11-connections") {
      const limit = page.getByRole("textbox", { name: "월 내부 예산 한도 (USD)" });
      await expect(limit).toBeDisabled(); await expect(limit).toHaveValue("");
      await expect(page.getByRole("button", { name: "설정 저장", exact: true })).toBeDisabled();
      await expect(page.getByText("예산을 확인하지 못했습니다. 숫자를 추측하지 않습니다.", { exact: true })).toBeVisible();
      await expect(page.locator(".budget-progress")).toHaveCount(0);
    } else if (route.screenId === "12-diagnostics") {
      await expect(page.getByText("진단 정보를 확인하지 못했습니다.", { exact: true })).toBeVisible();
      await expect(page.locator(".diagnostic-banner")).toHaveCount(0);
    }
    await capturePair(page, testInfo, `offline-${route.screenId}`);
  });
}
