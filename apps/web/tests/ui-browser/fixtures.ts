import { test as base, expect, type Route } from "@playwright/test";
import { FIXED_TIME } from "./routes";

type NetworkAudit = { apiRequests: string[]; commandAttempts: string[]; externalRequests: string[]; pageErrors: string[] };

function guardedTest(offline: boolean) { return base.extend<{ networkAudit: NetworkAudit }>({
  networkAudit: [async ({ context, page, baseURL }, use, testInfo) => {
    if (!baseURL) throw new Error("The fixture browser suite requires a local dev-server baseURL.");
    const baseAddress = new URL(baseURL), allowed = baseAddress.origin;
    if (!["127.0.0.1", "localhost", "[::1]"].includes(baseAddress.hostname)) throw new Error("Browser checks may only use the local development server.");
    const audit: NetworkAudit = { apiRequests: [], commandAttempts: [], externalRequests: [], pageErrors: [] };
    const guard = async (route: Route) => {
      const request = route.request(), url = new URL(request.url());
      if (url.pathname === "/api" || url.pathname.startsWith("/api/")) {
        audit.apiRequests.push(`${request.method()} ${url.href}`);
        // The existing recommendation preview is a POST read. It is mocked here
        // and never reaches the API; all order/configuration commands stay blocked.
        const readOnly = request.method() === "GET" || request.method() === "POST" && url.pathname === "/api/v1/recommendations/preview";
        if (offline && readOnly) await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ reason: "ui_browser_offline", message: "Controlled unavailable read response" }) });
        else {
          if (offline) audit.commandAttempts.push(`${request.method()} ${url.href}`);
          await route.abort("blockedbyclient");
        }
      } else if (["http:", "https:"].includes(url.protocol) && url.origin !== allowed) {
        audit.externalRequests.push(`${request.method()} ${url.href}`);
        await route.abort("blockedbyclient");
      } else await route.continue();
    };
    const onError = (error: Error) => audit.pageErrors.push(error.message);
    await context.route("**/*", guard);
    page.on("pageerror", onError);
    // Freeze dates without stopping requestAnimationFrame or canvas layout work.
    await page.clock.setFixedTime(new Date(FIXED_TIME));
    await use(audit);
    await testInfo.attach("fixture-network-audit", { body: Buffer.from(JSON.stringify(audit, null, 2)), contentType: "application/json" });
    if (offline) {
      expect.soft(audit.apiRequests.length, "An actual route must try its read contract before showing an offline state.").toBeGreaterThan(0);
      expect.soft(audit.commandAttempts, "Offline views must not submit commands.").toEqual([]);
    } else expect.soft(audit.apiRequests, "Fixture pages must make zero API requests; attempted requests were blocked.").toEqual([]);
    expect.soft(audit.externalRequests, "Fixture checks must not contact external services.").toEqual([]);
    expect.soft(audit.pageErrors, "The browser must not throw uncaught errors.").toEqual([]);
    page.off("pageerror", onError);
    await context.unroute("**/*", guard);
  }, { auto: true }],
}); }

export const test = guardedTest(false);
export const offlineTest = guardedTest(true);

export { expect };
