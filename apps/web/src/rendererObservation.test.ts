import { describe, expect, it } from "vitest";
import { observeProcCommandLine, observeRenderingArgv } from "../tests/ui-browser/rendering-observation";

describe("exact CDP argument observations", () => {
  it("recognizes complete argument tokens without promoting text inside values", () => {
    const result = observeRenderingArgv([
      "chromium", "--disable-skia-runtime-opts", "--use-gl=angle",
      "--url=https://example.invalid/ --disable-gpu", "--disable-gpu-compositing-extra",
    ]);
    expect(result.source).toBe("cdp-argv");
    expect(result.status).toBe("observed");
    expect(result.switches["disable-skia-runtime-opts"]).toEqual({ present: true });
    expect(result.switches["disable-gpu"]).toEqual({ present: false });
    expect(result.switches["disable-gpu-compositing"]).toEqual({ present: false });
    expect(result.switches["use-gl"]).toEqual({ present: true, mode: "angle" });
  });

  it("reports genuine empty argv as absent rather than unavailable", () => {
    const result = observeRenderingArgv([]);
    expect(result.status).toBe("observed");
    expect(Object.values(result.switches).every(value => value.present === false)).toBe(true);
    expect(result.switches.headless).toEqual({ present: false, mode: null });
  });

  it.each(["new", "old", "swiftshader", "swiftshader-webgl", "vulkan", "gl", "egl", "gles", "angle", "disabled", "default"])(
    "preserves the complete allowlisted mode %s", mode => {
      expect(observeRenderingArgv([`--use-angle=${mode}`]).switches["use-angle"])
        .toEqual({ present: true, mode });
    },
  );

  it.each(["angle=extra", "", "ANGLE", "not-allowlisted"])(
    "does not truncate or normalize an unallowlisted complete suffix %s", suffix => {
      expect(observeRenderingArgv([`--use-gl=${suffix}`]).switches["use-gl"])
        .toEqual({ present: true, mode: "unavailable-not-allowlisted" });
    },
  );

  it("keeps bare modes unknown and identical duplicates unambiguous", () => {
    expect(observeRenderingArgv(["--headless"]).switches.headless).toEqual({ present: true, mode: null });
    expect(observeRenderingArgv(["--headless", "--headless"]).switches.headless).toEqual({ present: true, mode: null });
    expect(observeRenderingArgv(["--use-gl=angle", "--use-gl=angle"]).switches["use-gl"])
      .toEqual({ present: true, mode: "angle" });
  });

  it.each([
    ["--use-gl=angle", "--use-gl=egl"],
    ["--use-gl=egl", "--use-gl=angle"],
    ["--use-gl", "--use-gl=angle"],
    ["--use-gl=angle", "--use-gl"],
    ["--use-gl=unlisted-one", "--use-gl=unlisted-two"],
  ])("does not guess effective precedence for conflicting modes %j", (...args) => {
    expect(observeRenderingArgv(args).switches["use-gl"])
      .toEqual({ present: true, mode: "conflicting-or-unknown" });
  });
});

describe("unverified proc command-line representation", () => {
  it("retains exact NUL field matches without asserting original argument presence", () => {
    const result = observeProcCommandLine("chromium\0--disable-skia-runtime-opts\0--use-gl=angle\0");
    expect(result.source).toBe("proc-cmdline");
    expect(result.representation).toEqual({ codeUnitLength: 52, nonemptyNulFieldCount: 3, shape: "nul-fields" });
    expect(result.switches["disable-skia-runtime-opts"])
      .toEqual({ present: null, exactNulFieldMatch: true, textualCandidate: true });
    expect(result.switches["disable-gpu"])
      .toEqual({ present: null, exactNulFieldMatch: false, textualCandidate: false });
    expect(result.switches["use-gl"])
      .toEqual({ present: null, exactNulFieldMatch: true, textualCandidate: true, mode: null });
  });

  it.each([
    "chromium --type=renderer --disable-skia-runtime-opts",
    "chromium --type=renderer --disable-skia-runtime-opts\0\0\0",
    "chromium\0--type=renderer --disable-skia-runtime-opts\0",
    "chromium --url=https://example.invalid/ --disable-skia-runtime-opts",
    "chromium --title='label --disable-skia-runtime-opts quoted'",
  ])("never reconstructs argv from flattened, padded, mixed or quoted text %j", raw => {
    const result = observeProcCommandLine(raw);
    expect(result.switches["disable-skia-runtime-opts"])
      .toEqual({ present: null, exactNulFieldMatch: false, textualCandidate: true });
    expect(Object.values(result.switches).every(value => value.present === null)).toBe(true);
    expect(result.switches["use-gl"].mode).toBeNull();
  });

  it("does not treat prefixes or embedded URL fragments as textual token boundaries", () => {
    const result = observeProcCommandLine("chromium\0https://example.invalid/--disable-gpu\0--disable-gpu-extra\0");
    expect(result.switches["disable-gpu"])
      .toEqual({ present: null, exactNulFieldMatch: false, textualCandidate: false });
  });

  it.each(["", "\0\0"])("keeps empty or padding-only proc text unknown %j", raw => {
    const result = observeProcCommandLine(raw);
    expect(result.status).toBe("observed");
    expect(result.representation.nonemptyNulFieldCount).toBe(0);
    expect(result.representation.shape).toBe("empty");
    expect(result.switches.headless)
      .toEqual({ present: null, exactNulFieldMatch: false, textualCandidate: false, mode: null });
  });
});

describe("diagnostic input and privacy bounds", () => {
  it.each([undefined, null, 7, "--disable-gpu", {}, ["--disable-gpu", 7], Array(1)].map(input => ({ input })))(
    "rejects malformed argv without claiming absent flags %j", ({ input }) => {
      const result = observeRenderingArgv(input);
      expect(result.status).toBe("unavailable");
      expect(Object.values(result.switches).every(value => value.present === null)).toBe(true);
    },
  );

  it.each([undefined, null, 7, {}, ["--disable-gpu"]].map(input => ({ input })))("rejects non-string proc input %j", ({ input }) => {
    const result = observeProcCommandLine(input);
    expect(result.status).toBe("unavailable");
    expect(result.representation).toEqual({ codeUnitLength: null, nonemptyNulFieldCount: null, shape: "unavailable" });
    expect(result.switches["disable-gpu"])
      .toEqual({ present: null, exactNulFieldMatch: null, textualCandidate: null });
  });

  it("rejects array accessors without invoking a getter or reflecting its value", () => {
    let reads = 0;
    const input = Object.defineProperty(["placeholder"], "0", { get() { reads += 1; return "--disable-gpu"; } });
    const result = observeRenderingArgv(input);
    expect(reads).toBe(0);
    expect(result.status).toBe("unavailable");
    expect(result.switches["disable-gpu"].present).toBeNull();
  });

  it("returns fixed unavailable metadata when a proxy inspection throws", () => {
    const input = new Proxy([], { getOwnPropertyDescriptor() { throw new Error("synthetic-private-error-sentinel"); } });
    const result = observeRenderingArgv(input);
    expect(result.status).toBe("unavailable");
    expect(JSON.stringify(result)).not.toContain("synthetic-private-error-sentinel");
  });

  it("accepts 256 argv entries but rejects 257 without partial observations", () => {
    expect(observeRenderingArgv(Array(256).fill("")).status).toBe("observed");
    const result = observeRenderingArgv(["--disable-gpu", ...Array(256).fill("")]);
    expect(result.status).toBe("unavailable");
    expect(result.switches["disable-gpu"].present).toBeNull();
  });

  it("enforces per-string and total UTF16 limits before publishing any argv observations", () => {
    expect(observeRenderingArgv(["x".repeat(262144)]).status).toBe("observed");
    expect(observeRenderingArgv(["x".repeat(262145)]).status).toBe("unavailable");
    expect(observeRenderingArgv(["x".repeat(131072), "y".repeat(131072)]).status).toBe("observed");
    expect(observeRenderingArgv(["x".repeat(131073), "y".repeat(131072)]).status).toBe("unavailable");
    expect(observeRenderingArgv(["😀".repeat(131072)]).status).toBe("observed");
    expect(observeRenderingArgv(["😀".repeat(131073)]).status).toBe("unavailable");
  });

  it("enforces the raw proc UTF16 limit and bounds representation metadata", () => {
    expect(observeProcCommandLine("x".repeat(262144)).representation)
      .toEqual({ codeUnitLength: 262144, nonemptyNulFieldCount: 1, shape: "single-field" });
    const result = observeProcCommandLine("x".repeat(262145));
    expect(result.status).toBe("unavailable");
    expect(result.representation.codeUnitLength).toBeNull();
    expect(result.switches["disable-gpu"].textualCandidate).toBeNull();
  });

  it("never serializes unknown values, feature lists, paths, URLs or private sentinels", () => {
    const sentinel = "synthetic-private-sentinel";
    const argv = [`C:/${sentinel}/chromium`, `--enable-features=${sentinel}`, `--use-gl=${sentinel}`, `https://${sentinel}.invalid/`];
    const cdp = observeRenderingArgv(argv);
    const proc = observeProcCommandLine(argv.join("\0"));
    expect(JSON.stringify([cdp, proc])).not.toContain(sentinel);
    expect(cdp.switches["enable-features"]).toEqual({ present: true });
    expect(cdp.switches["use-gl"]).toEqual({ present: true, mode: "unavailable-not-allowlisted" });
    expect(Object.keys(cdp.switches)).toHaveLength(11);
    expect(Object.keys(proc.switches)).toHaveLength(11);
  });
});
