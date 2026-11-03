import { existsSync } from "node:fs";
import { open, readFile, realpath, stat, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { basename } from "node:path";
import { arch, cpus, platform, release } from "node:os";
import { expect, type CDPSession, type Page, type TestInfo } from "@playwright/test";
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

// Observations only: these do not identify initialized Skia functions. Every
// request is optional, bounded, and uses this test's existing browser/page.
const unavailable = (reason: string) => ({ status: "unavailable", reason });
const textLimit = (value: unknown) => typeof value === "string" ? value.replace(/\p{Cc}/gu, " ").slice(0, 256) : null;
const switchNames = ["disable-skia-runtime-opts", "disable-accelerated-2d-canvas", "disable-gpu", "disable-gpu-compositing", "disable-software-rasterizer", "enable-automation", "headless", "use-angle", "use-gl", "enable-features", "disable-features"] as const;
function renderingSwitches(args: string[]) {
  const values: Record<string, unknown> = {};
  for (const name of switchNames) {
    const matches = args.filter(arg => arg === `--${name}` || arg.startsWith(`--${name}=`));
    values[name] = { present: matches.length > 0 };
    // Never retain arbitrary switch values (feature lists can contain URLs).
    if (["headless", "use-angle", "use-gl"].includes(name)) {
      const mode = matches[0]?.split("=")[1];
      values[name] = { present: matches.length > 0, mode: mode === undefined ? null : ["new", "old", "swiftshader", "swiftshader-webgl", "vulkan", "gl", "egl", "gles", "angle", "disabled", "default"].includes(mode) ? mode : "unavailable-not-allowlisted" };
    }
  }
  return values;
}

async function optional<T>(operation: () => Promise<T>, milliseconds = 1500): Promise<T | ReturnType<typeof unavailable>> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([operation(), new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error("diagnostic-timeout")), milliseconds); })]);
  } catch {
    // Do not reflect browser, filesystem or protocol error strings.
    return unavailable("unsupported-inaccessible-or-timed-out");
  } finally {
    clearTimeout(timer);
  }
}

async function boundedRead(path: string, limit: number) {
  const handle = await open(path, "r");
  try {
    const buffer = Buffer.alloc(limit + 1);
    const { bytesRead } = await handle.read(buffer, 0, buffer.length, 0);
    if (bytesRead > limit) throw new Error("diagnostic-size-limit");
    return buffer.subarray(0, bytesRead).toString("utf8");
  } finally { await handle.close(); }
}

const binaryHashes = new Map<string, string>();
function fileIdentity(value: Awaited<ReturnType<typeof binaryStat>>) {
  return [value.dev, value.ino, value.size, value.mtimeNs, value.ctimeNs].join(":");
}
const binaryStat = (path: string) => stat(path, { bigint: true });
async function processIdentity(pid: number) {
  const prefix = `/proc/${pid}`;
  const status = await boundedRead(`${prefix}/status`, 65536);
  const uid = status.match(/^Uid:\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)/m);
  if (!uid || !process.getuid || uid.slice(1).some(value => Number(value) !== process.getuid!())) throw new Error("diagnostic-uid");
  const processStat = await boundedRead(`${prefix}/stat`, 65536);
  const end = processStat.lastIndexOf(")");
  if (!processStat.startsWith(`${pid} (`) || end < 0) throw new Error("diagnostic-process-identity");
  const startTicks = processStat.slice(end + 2).split(" ")[19];
  if (!/^\d+$/.test(startTicks ?? "")) throw new Error("diagnostic-start-identity");
  const executable = await realpath(`${prefix}/exe`);
  // Accept only the actual executed Playwright Chromium install; a familiar
  // process name by itself does not authorize reading another executable.
  if (!/\/(?:chromium|chromium_headless_shell)-\d+\//.test(executable) || !["chrome", "chrome-headless-shell", "headless_shell", "chromium"].includes(basename(executable))) throw new Error("diagnostic-executable");
  const info = await binaryStat(`${prefix}/exe`);
  if (!info.isFile()) throw new Error("diagnostic-executable-file");
  return { pid, uid: Number(uid[1]), startTicks, executable, file: fileIdentity(info), size: info.size };
}

async function executableHash(identity: Awaited<ReturnType<typeof processIdentity>>) {
  const key = `${identity.executable}:${identity.file}`;
  const cached = binaryHashes.get(key);
  if (cached) return { sha256: cached, cache: "verified-file-identity" };
  if (identity.size > 512n * 1024n * 1024n) throw new Error("diagnostic-binary-size-limit");
  const handle = await open(`/proc/${identity.pid}/exe`, "r");
  try {
    if (fileIdentity(await handle.stat({ bigint: true })) !== identity.file) throw new Error("diagnostic-file-changed");
    const hash = createHash("sha256");
    const buffer = Buffer.alloc(1024 * 1024);
    const deadline = performance.now() + 1500;
    let position = 0;
    while (BigInt(position) < identity.size) {
      if (performance.now() > deadline) throw new Error("diagnostic-hash-time-limit");
      const { bytesRead } = await handle.read(buffer, 0, Math.min(buffer.length, Number(identity.size) - position), position);
      if (!bytesRead) throw new Error("diagnostic-short-binary");
      hash.update(buffer.subarray(0, bytesRead));
      position += bytesRead;
    }
    if (fileIdentity(await handle.stat({ bigint: true })) !== identity.file) throw new Error("diagnostic-file-changed");
    const sha256 = hash.digest("hex");
    // Only publish/cache after the caller rechecks PID/start/file identity.
    return { sha256, cache: "fresh", key };
  } finally { await handle.close(); }
}

async function ownedProcesses(session: CDPSession, budgetMs = 2500) {
  const deadline = performance.now() + budgetMs;
  const response = await optional(() => session.send("SystemInfo.getProcessInfo"), Math.min(400, budgetMs));
  if (!("processInfo" in response)) return response;
  const owned = response.processInfo.filter(item => ["browser", "renderer", "GPU"].includes(item.type));
  const selected = owned.slice(0, 12);
  const browserPid = selected.find(item => item.type === "browser")?.id;
  const browserIdentity = platform() === "linux" && Number.isSafeInteger(browserPid) && browserPid! > 0
    ? await optional(() => processIdentity(browserPid!), Math.max(1, Math.min(400, deadline - performance.now()))) : unavailable("linux-owned-browser-pid-required");
  const identities = [];
  for (const item of selected) {
    if (performance.now() >= deadline) { identities.push({ type: item.type, pid: item.id, identity: unavailable("process-collection-time-budget") }); continue; }
    if (!Number.isSafeInteger(item.id) || item.id <= 0) { identities.push({ type: item.type, identity: unavailable("invalid-cdp-pid") }); continue; }
    if (!("executable" in browserIdentity)) { identities.push({ type: item.type, pid: item.id, identity: unavailable("validated-linux-browser-required") }); continue; }
    identities.push({ type: item.type, pid: item.id, identity: await optional(async () => {
      const before = await processIdentity(item.id);
      if (before.executable !== browserIdentity.executable || before.file !== browserIdentity.file) throw new Error("diagnostic-executable-mismatch");
      const flags = renderingSwitches((await boundedRead(`/proc/${item.id}/cmdline`, 262144)).split("\0"));
      const binary = await executableHash(before);
      const after = await processIdentity(item.id);
      if (before.startTicks !== after.startTicks || before.uid !== after.uid || before.executable !== after.executable || before.file !== after.file) throw new Error("diagnostic-process-changed");
      if ("key" in binary && binary.key) {
        if (binaryHashes.size >= 16) binaryHashes.clear();
        binaryHashes.set(binary.key, binary.sha256);
      }
      return { uid: after.uid, startTicks: after.startTicks,
        executable: { basename: basename(after.executable), canonicalPath: "withheld-path-privacy", fileIdentity: after.file, sha256: binary.sha256, hashSource: binary.cache }, flags };
    }, Math.max(1, Math.min(2000, deadline - performance.now()))) });
  }
  return { source: "owned-browser-CDP", pageToRendererPid: unavailable("CDP-does-not-establish-target-to-OS-pid"), truncated: owned.length > selected.length, identities };
}

async function pagePaintState(page: Page) {
  return optional(() => page.evaluate(() => {
    const label = (element: Element | null) => element ? { tag: element.tagName, inDialog: !!element.closest(".dialog"), canvas: element instanceof HTMLCanvasElement } : null;
    const bounded = (value: string) => value.slice(0, 128);
    return {
      devicePixelRatio, viewport: { width: innerWidth, height: innerHeight }, scroll: { x: scrollX, y: scrollY },
      visualViewport: visualViewport ? { width: visualViewport.width, height: visualViewport.height, scale: visualViewport.scale, offsetLeft: visualViewport.offsetLeft, offsetTop: visualViewport.offsetTop } : null,
      documentFocused: document.hasFocus(), activeElement: label(document.activeElement), fontStatus: document.fonts.status, fontsTruncated: document.fonts.size > 16,
      fonts: Array.from(document.fonts).slice(0, 16).map(font => ({ family: bounded(font.family), status: font.status, weight: bounded(font.weight), style: bounded(font.style) })),
      elements: Array.from(document.querySelectorAll(".chart,.chart canvas,.dialog,.dialog h2,.dialog button")).slice(0, 50).map(element => {
        const rect = element.getBoundingClientRect();
        const css = getComputedStyle(element);
        return { ...label(element), rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
          scroll: { x: element.scrollLeft, y: element.scrollTop }, focused: element === document.activeElement,
          focusVisible: element.matches(":focus-visible"), hovered: element.matches(":hover"),
          css: Object.fromEntries(["width", "height", "line-height", "transform", "translate", "border-top-left-radius", "border-top-right-radius", "border-bottom-left-radius", "border-bottom-right-radius", "border-top-width", "border-right-width", "border-bottom-width", "border-left-width", "border-top-color", "border-right-color", "border-bottom-color", "border-left-color", "background-color", "filter", "opacity", "font-family", "font-size", "font-weight"].map(key => [key, bounded(css.getPropertyValue(key))])),
          bitmap: element instanceof HTMLCanvasElement ? { width: element.width, height: element.height } : null };
      }),
    };
  }));
}

async function captureDiagnostics(page: Page, testInfo: TestInfo, name: string) {
  const sessions: CDPSession[] = [];
  const deadline = performance.now() + 4000;
  const collect = <T>(operation: () => Promise<T>) => performance.now() >= deadline
    ? Promise.resolve(unavailable("capture-collection-time-budget"))
    : optional(operation, Math.max(1, Math.min(400, deadline - performance.now())));
  let closing = false;
  let browserSession: CDPSession | undefined;
  const acquire = (operation: () => Promise<CDPSession>) => collect(async () => {
    const session = await operation();
    if (closing) { await optional(() => session.detach()); throw new Error("diagnostic-session-late"); }
    sessions.push(session);
    return session;
  });
  const stamp = () => ({ wallTime: new Date().toISOString(), monotonicMs: performance.now() });
  const data: Record<string, unknown> = {
    schema: "u12-capture-renderer-v1", name, testId: testInfo.testId.slice(0, 256), project: testInfo.project.name.slice(0, 128),
    workerIndex: testInfo.workerIndex, parallelIndex: testInfo.parallelIndex, nodePid: process.pid, started: stamp(),
    cause: "UNDETERMINED", completeness: "diagnostic-incomplete-target-to-pid-unresolved", timingCaveat: "read-only-instrumentation-can-perturb-timing",
    job: Object.fromEntries(["GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB", "GITHUB_SHA", "ImageOS", "ImageVersion"].map(key => {
      const value = process.env[key];
      return [key, value && /^[a-zA-Z0-9_.-]{1,128}$/.test(value) ? value : unavailable("absent-or-not-allowlisted")];
    })),
    candidateSha256: null, baselineSha256: null, outcome: "capture-or-assertion-incomplete",
    browserVersion: unavailable("not-collected"), browserFlags: unavailable("not-collected"), gpu: unavailable("not-collected"),
    processesBefore: unavailable("not-collected"), target: unavailable("not-collected"), before: unavailable("not-collected"),
    system: unavailable("not-collected"), packageLockSha256: unavailable("not-collected"),
  };
  try {
    const browser = page.context().browser();
    data.system = { platform: platform(), architecture: arch(), kernel: textLimit(release()), node: process.version, cpuModels: [...new Set(cpus().map(cpu => cpu.model))].slice(0, 4).map(textLimit), cpuFeatures: unavailable("not-collected") };
    data.packageLockSha256 = await collect(async () => createHash("sha256").update(await readFile(new URL("../../package-lock.json", import.meta.url))).digest("hex"));
    const session = browser ? await acquire(() => browser.newBrowserCDPSession()) : unavailable("no-owned-browser");
    if ("send" in session) {
      browserSession = session;
      const version = await collect(() => session.send("Browser.getVersion"));
      data.browserVersion = "product" in version ? { product: textLimit(version.product), revision: textLimit(version.revision), protocolVersion: textLimit(version.protocolVersion), jsVersion: textLimit(version.jsVersion) } : version;
      const command = await collect(() => session.send("Browser.getBrowserCommandLine"));
      data.browserFlags = "arguments" in command ? renderingSwitches(command.arguments) : command;
      const info = await collect(() => session.send("SystemInfo.getInfo"));
      data.gpu = "gpu" in info ? { devices: info.gpu.devices.slice(0, 4).map(device => ({ vendorId: device.vendorId, deviceId: device.deviceId, vendorString: textLimit(device.vendorString), deviceString: textLimit(device.deviceString), driverVendor: textLimit(device.driverVendor), driverVersion: textLimit(device.driverVersion) })),
        auxiliary: Object.fromEntries(["glRenderer", "glVendor", "glVersion", "displayType", "skiaBackendType", "passthroughCmdDecoder", "sandboxed", "inProcessGpu"].map(key => { const value: unknown = info.gpu.auxAttributes?.[key]; return [key, typeof value === "boolean" || typeof value === "number" ? value : textLimit(value) ?? unavailable("not-returned")]; })) } : info;
      data.processesBefore = performance.now() < deadline ? await ownedProcesses(session, Math.min(2500, deadline - performance.now())) : unavailable("capture-collection-time-budget");
    } else data.browser = session;
    const targetSession = await acquire(() => page.context().newCDPSession(page));
    if ("send" in targetSession) {
      const target = await collect(() => targetSession.send("Target.getTargetInfo"));
      data.target = "targetInfo" in target ? { targetId: textLimit(target.targetInfo.targetId), browserContextId: textLimit(target.targetInfo.browserContextId) ?? unavailable("default-or-not-returned"), pageToRendererPid: unavailable("not-established") } : target;
    } else data.target = targetSession;
    data.before = { at: stamp(), state: await pagePaintState(page) };
  } catch { data.collection = unavailable("unexpected-diagnostic-failure"); }
  return {
    data,
    async afterCapture() { data.after = { at: stamp(), state: await pagePaintState(page) }; },
    async finish() {
      try {
        if (!data.after) data.after = { at: stamp(), state: await pagePaintState(page) };
        data.processesAfter = browserSession ? await ownedProcesses(browserSession, 1000) : unavailable("owned-browser-session-unavailable");
      } catch { data.after = unavailable("unexpected-diagnostic-failure"); }
      finally {
        closing = true;
        data.detach = [];
        for (const session of sessions) (data.detach as unknown[]).push(await optional(async () => { await session.detach(); return { status: "detached" }; }));
        data.finished = stamp();
        try {
          const body = JSON.stringify(data, null, 2);
          await testInfo.attach(`${name}-renderer-diagnostics`, { body: Buffer.from(Buffer.byteLength(body) <= 160 * 1024 ? body : JSON.stringify({ schema: data.schema, name, status: "unavailable", reason: "diagnostic-size-limit" })), contentType: "application/json" });
        } catch { /* Optional telemetry must never replace the original failure. */ }
      }
    },
  };
}

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
    // Failure telemetry is attached in finally, including stabilization and
    // strict assertion failures. Existing capture waits/order remain intact.
    const diagnostic = await captureDiagnostics(page, testInfo, name).catch(() => undefined);
    try {
      const options = { fullPage, animations: "disabled" as const, caret: "hide" as const, scale: "css" as const };
      const candidatePath = testInfo.outputPath(`baseline-${name}`);
      const stable = await captureStableImage(page, options);
      if (diagnostic) await diagnostic.afterCapture().catch(() => { diagnostic.data.after = unavailable("post-capture-state-failed"); });
      await writeFile(candidatePath, stable.image);
      await testInfo.attach(name, { path: candidatePath, contentType: "image/png" });
      const captureOnly = process.env.UI_CAPTURE_BASELINE === "1";
      const baselinePath = testInfo.snapshotPath(name);
      const candidateSha256 = createHash("sha256").update(stable.image).digest("hex");
      if (diagnostic) diagnostic.data.candidateSha256 = candidateSha256;
      let baselineSha256: string | undefined;
      if (!captureOnly) {
        expect(testInfo.config.updateSnapshots, "Baseline updates must remain disabled.").toBe("none");
        expect(testInfo.project.ignoreSnapshots, "Snapshot comparisons must not be skipped.").toBe(false);
        if (!existsSync(baselinePath)) throw new Error(`Reviewed screenshot baseline is missing: ${baselinePath}. Capture candidates with UI_CAPTURE_BASELINE=1; review and commit them explicitly.`);
        baselineSha256 = createHash("sha256").update(await readFile(baselinePath)).digest("hex");
        if (diagnostic) diagnostic.data.baselineSha256 = baselineSha256;
      }
      await testInfo.attach(`${name}-manifest`, { body: Buffer.from(JSON.stringify({
        project: testInfo.project.name, name, candidate: candidatePath,
        baseline: baselinePath, viewport: page.viewportSize(), fullPage,
        mode: captureOnly ? "capture-only-needs-review" : "strict-comparison",
        comparisonStrategy: captureOnly ? "review-candidate-only" : "stable-png-buffer-pixels-and-sha256",
        candidateSha256, baselineSha256: baselineSha256 ?? null,
        candidateStability: { consecutiveExactImages: 2, attempts: stable.attempts, maxAttempts: 8 },
        geometry: await page.evaluate(() => ({
          devicePixelRatio: window.devicePixelRatio,
          viewport: { width: window.innerWidth, height: window.innerHeight },
          elements: Array.from(document.querySelectorAll('.chart,.chart canvas,.dialog,.dialog h2,.dialog button')).slice(0, 50).map(element => {
            const rect = element.getBoundingClientRect();
            const style = getComputedStyle(element);
            return {
              tag: element.tagName, className: element.className,
              rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
              css: { width: style.width, height: style.height, lineHeight: style.lineHeight, transform: style.transform, translate: style.translate },
              bitmap: element instanceof HTMLCanvasElement ? { width: element.width, height: element.height } : null,
            };
          }),
        })),
      }, null, 2)), contentType: "application/json" });
      if (!captureOnly) {
        // Compare the exact saved stable PNG; extra page captures would change the
        // viewport/full-page paint sequence between capture and strict modes.
        expect(stable.image).toMatchSnapshot(name, { maxDiffPixels: 0, threshold: 0 });
        expect(candidateSha256, "The saved PNG bytes must exactly match the reviewed baseline.").toBe(baselineSha256);
      }
      if (diagnostic) diagnostic.data.outcome = captureOnly ? "capture-only-needs-review" : "strict-comparison-passed";
    } finally {
      if (diagnostic) await diagnostic.finish().catch(() => undefined);
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
