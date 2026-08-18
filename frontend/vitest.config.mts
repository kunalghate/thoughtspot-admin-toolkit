import { defineConfig, configDefaults } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

// ── Deterministic timezone + locale for the whole run ─────────────────────────
// Set here, at config module scope, because this file is evaluated in the Vitest
// main process BEFORE the worker pool is forked — the workers inherit this env
// at spawn and initialise ICU from it. `test.env` is too late for the locale:
// Node caches the default ICU locale at process start, so assigning LC_ALL from
// inside a running process changes nothing (measured). TZ is honoured at
// runtime, but is set in the same place so the two cannot drift apart.
//
//  - TZ: a NON-UTC zone on purpose. Under TZ=UTC (what CI runs) the naive
//    timestamps the backend returns parse to the same instant with or without
//    the `+ "Z"` in Topbar.helpers, so deleting it leaves the suite green while
//    a 3h50m-old sync renders "Synced just now" in green — the exact S23
//    fail-open this suite exists to catch.
//  - Locale: `toLocaleString()` reads the process locale, so a contributor on a
//    de/fr/es/pt machine saw `Syncing 1.500 / 3.000` and a false red.
process.env.TZ = "America/New_York";
process.env.LC_ALL = "en_US.UTF-8";
process.env.LANG = "en_US.UTF-8";
process.env.LANGUAGE = "en_US";

export default defineConfig({
  plugins: [react()],
  resolve: {
    // Mirror tsconfig paths: "@/*" -> "./*"
    alias: { "@": fileURLToPath(new URL("./", import.meta.url)) },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["**/*.test.{ts,tsx}"],
    exclude: [...configDefaults.exclude, "tests/e2e/**", "out/**"],
  },
});
