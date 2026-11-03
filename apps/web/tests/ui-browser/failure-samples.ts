import { createHash } from "node:crypto";
import type { Page, TestInfo } from "@playwright/test";

type Slot = {
  ordinal: number;
  status: "captured" | "unavailable";
  reason?: string;
  nativeCaptureTimeoutMs?: number;
  bytes?: number;
  sha256?: string;
  attachment?: "attached" | "unavailable";
};

const TOTAL_BUDGET_MS = 6500;
const CAPTURE_BUDGET_MS = 4500;
const PREPARATION_TIMEOUT_MS = 400;
const CAPTURE_TIMEOUT_MS = 700;
const ATTACHMENT_TIMEOUT_MS = 300;
const remaining = (deadline: number) => Math.max(0, Math.floor(deadline - performance.now()));

// Only evaluations/attachments use this race. Screenshots always await their
// native timeout: a detached operation must never schedule a later screenshot.
async function bounded<T>(operation: () => Promise<T>, deadline: number, maximum: number) {
  const timeout = Math.min(remaining(deadline), maximum);
  if (timeout <= 0) return { status: "unavailable" as const };
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const task = operation();
    return await Promise.race([
      task.then(value => ({ status: "available" as const, value }), () => ({ status: "unavailable" as const })),
      new Promise<{ status: "unavailable" }>(resolve => { timer = setTimeout(() => resolve({ status: "unavailable" }), timeout); }),
    ]);
  } catch {
    return { status: "unavailable" as const };
  } finally { clearTimeout(timer); }
}

export async function observeFailureSamples(page: Page, testInfo: TestInfo, name: string, fullPage: boolean) {
  const started = performance.now();
  const captureDeadline = started + CAPTURE_BUDGET_MS;
  const deadline = started + TOTAL_BUDGET_MS;
  const safeName = name.replace(/[^a-zA-Z0-9_.-]/g, "_").slice(0, 80) || "screenshot";
  const options = { fullPage, animations: "disabled" as const, caret: "hide" as const, scale: "css" as const };
  const slots: Slot[] = Array.from({ length: 4 }, (_, index) => ({ ordinal: index + 1, status: "unavailable" }));
  const captured: Array<{ slot: Slot; image: Buffer }> = [];
  let stopped: string | undefined;

  for (const slot of slots) {
    stopped ??= page.isClosed() ? "page-closed" : remaining(captureDeadline) <= 0 ? "sampling-deadline" : undefined;
    if (stopped) { slot.reason = stopped; continue; }
    const preparationTimeout = Math.min(PREPARATION_TIMEOUT_MS, remaining(captureDeadline));
    const prepared = await bounded(() => page.evaluate(timeout => new Promise<boolean>(resolve => {
      let done = false;
      let firstFrame = 0;
      let secondFrame = 0;
      const finish = (ready: boolean) => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        cancelAnimationFrame(firstFrame);
        cancelAnimationFrame(secondFrame);
        resolve(ready);
      };
      const timer = setTimeout(() => finish(false), timeout);
      document.fonts.ready.then(() => {
        if (done) return;
        firstFrame = requestAnimationFrame(() => {
          if (!done) secondFrame = requestAnimationFrame(() => finish(true));
        });
      }, () => finish(false));
    }), preparationTimeout), captureDeadline, preparationTimeout);
    if (prepared.status !== "available" || prepared.value !== true) {
      slot.reason = stopped = "preparation-unavailable";
      continue;
    }
    if (page.isClosed() || remaining(captureDeadline) <= 0) {
      slot.reason = stopped = page.isClosed() ? "page-closed" : "sampling-deadline";
      continue;
    }
    const timeout = Math.min(CAPTURE_TIMEOUT_MS, remaining(captureDeadline));
    if (timeout <= 0) { slot.reason = stopped = "sampling-deadline"; continue; }
    slot.nativeCaptureTimeoutMs = timeout;
    try {
      const image = await page.screenshot({ ...options, timeout });
      slot.status = "captured";
      slot.bytes = image.length;
      slot.sha256 = createHash("sha256").update(image).digest("hex");
      captured.push({ slot, image });
    } catch {
      slot.reason = stopped = "capture-unavailable";
    }
  }

  // Reserve part of the total budget for every obtained PNG and the manifest.
  // Ordinals remain observations even when their bytes repeat or never match.
  for (const { slot, image } of captured) {
    const attached = await bounded(() => testInfo.attach(`${safeName}-failure-sample-${String(slot.ordinal).padStart(2, "0")}.png`, {
      body: image, contentType: "image/png",
    }), deadline, ATTACHMENT_TIMEOUT_MS);
    slot.attachment = attached.status === "available" ? "attached" : "unavailable";
  }
  const manifest = {
    schema: "ui-failure-samples-v1", name: safeName, observationOnly: true, cause: "UNDETERMINED",
    timingCaveat: "failure-only-observations-perturb-later-capture-timing", requestedSlots: 4,
    budgetMs: TOTAL_BUDGET_MS, captureBudgetMs: CAPTURE_BUDGET_MS,
    preparationTimeoutMs: PREPARATION_TIMEOUT_MS, captureTimeoutMs: CAPTURE_TIMEOUT_MS,
    attachmentTimeoutMs: ATTACHMENT_TIMEOUT_MS, captureOptions: options,
    slots, completeness: slots.every(slot => slot.status === "captured" && slot.attachment === "attached") ? "complete" : "incomplete",
    elapsedMs: Math.max(0, performance.now() - started),
    manifestAttachment: "pending-at-serialization",
  };
  const attached = await bounded(() => testInfo.attach(`${safeName}-failure-samples`, {
    body: Buffer.from(JSON.stringify(manifest)), contentType: "application/json",
  }), deadline, ATTACHMENT_TIMEOUT_MS);
  return { ...manifest, manifestAttachment: attached.status === "available" ? "attached" : "unavailable" };
}
