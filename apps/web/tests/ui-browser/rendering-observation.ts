// These observations never identify initialized rendering functions. CDP argv
// has exact token boundaries; /proc may contain an unescaped process title.
const switchNames = ["disable-skia-runtime-opts", "disable-accelerated-2d-canvas", "disable-gpu", "disable-gpu-compositing", "disable-software-rasterizer", "enable-automation", "headless", "use-angle", "use-gl", "enable-features", "disable-features"] as const;
const modes = ["new", "old", "swiftshader", "swiftshader-webgl", "vulkan", "gl", "egl", "gles", "angle", "disabled", "default"] as const;
const maxCodeUnits = 262144;
type SwitchName = typeof switchNames[number];
type Mode = typeof modes[number] | "unavailable-not-allowlisted" | "conflicting-or-unknown" | null;
type Reason = "malformed-input" | "input-limit" | "inaccessible-input";
type SwitchObservation = { present: boolean | null; mode?: Mode };
type ProcSwitchObservation = { present: null; exactNulFieldMatch: boolean | null; textualCandidate: boolean | null; mode?: null };
const isMode = (name: SwitchName) => name === "headless" || name === "use-angle" || name === "use-gl";

function unknownArgv(reason: Reason) {
  return {
    source: "cdp-argv" as const, status: "unavailable" as const, reason,
    switches: Object.fromEntries(switchNames.map(name => [name, isMode(name) ? { present: null, mode: null } : { present: null }])) as Record<SwitchName, SwitchObservation>,
  };
}

export function observeRenderingArgv(input: unknown) {
  // Inspect only own data fields: diagnostic input must not invoke accessors or
  // coerce values. Proxy inspection failures become fixed, private metadata.
  try {
    if (!Array.isArray(input)) return unknownArgv("malformed-input");
    const length: unknown = Object.getOwnPropertyDescriptor(input, "length")?.value;
    if (typeof length !== "number" || !Number.isSafeInteger(length) || length < 0) return unknownArgv("malformed-input");
    if (length > 256) return unknownArgv("input-limit");
    const args: string[] = [];
    let total = 0;
    for (let index = 0; index < length; index += 1) {
      const field = Object.getOwnPropertyDescriptor(input, String(index));
      if (!field || !("value" in field) || typeof field.value !== "string") return unknownArgv("malformed-input");
      total += field.value.length;
      if (field.value.length > maxCodeUnits || total > maxCodeUnits) return unknownArgv("input-limit");
      args.push(field.value);
    }
    const switches = Object.fromEntries(switchNames.map(name => {
      const flag = `--${name}`;
      const matches = args.filter(arg => arg === flag || arg.startsWith(`${flag}=`));
      const observation: SwitchObservation = { present: matches.length > 0 };
      if (isMode(name)) {
        // Keep the entire suffix and report conflicts without guessing which
        // duplicate Chromium would use. Arbitrary values are never returned.
        const values = new Set(matches.map(arg => arg === flag ? null : arg.slice(flag.length + 1)));
        const value = values.values().next().value;
        observation.mode = values.size > 1 ? "conflicting-or-unknown"
          : value == null ? null
          : (modes as readonly string[]).includes(value) ? value as Mode : "unavailable-not-allowlisted";
      }
      return [name, observation];
    })) as Record<SwitchName, SwitchObservation>;
    return { source: "cdp-argv" as const, status: "observed" as const, switches };
  } catch { return unknownArgv("inaccessible-input"); }
}

function unknownProc(reason: Reason) {
  return {
    source: "proc-cmdline" as const, status: "unavailable" as const, reason,
    representation: { codeUnitLength: null, nonemptyNulFieldCount: null, shape: "unavailable" as const },
    switches: Object.fromEntries(switchNames.map(name => [name, {
      present: null, exactNulFieldMatch: null, textualCandidate: null, ...(isMode(name) ? { mode: null } : {}),
    }])) as Record<SwitchName, ProcSwitchObservation>,
  };
}

export function observeProcCommandLine(input: unknown) {
  if (typeof input !== "string") return unknownProc("malformed-input");
  if (input.length > maxCodeUnits) return unknownProc("input-limit");
  const fields = input.split("\0");
  const nonemptyNulFieldCount = fields.reduce((count, field) => count + (field.length > 0 ? 1 : 0), 0);
  const shape = nonemptyNulFieldCount === 0 ? "empty" : nonemptyNulFieldCount === 1 ? "single-field" : "nul-fields";
  const switches = Object.fromEntries(switchNames.map(name => {
    const flag = `--${name}`;
    // A NUL field match and a textual candidate describe stored text only.
    // Neither establishes original argv, absence, or an effective mode.
    const exactNulFieldMatch = fields.some(field => field === flag || field.startsWith(`${flag}=`));
    const textualCandidate = new RegExp(`(?:^|[\\s\\0])${flag}(?=$|[=\\s\\0])`).test(input);
    return [name, { present: null, exactNulFieldMatch, textualCandidate, ...(isMode(name) ? { mode: null } : {}) }];
  })) as Record<SwitchName, ProcSwitchObservation>;
  return {
    source: "proc-cmdline" as const, status: "observed" as const,
    representation: { codeUnitLength: input.length, nonemptyNulFieldCount, shape }, switches,
  };
}
