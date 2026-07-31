import path from "node:path";
import { configDefaults, defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "."),
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    // livetracker3.md §5.2: BAP/web/e2e/*.spec.ts are real Playwright tests, driven by
    // `npm run test:e2e` (a separate config/runner) against the live docker-compose stack —
    // without this exclude, vitest's own default `*.spec.ts` glob would pick them up too and
    // try to run them as unit tests, which fails immediately (no `@playwright/test` fixtures
    // exist in a vitest/jsdom context).
    exclude: [...configDefaults.exclude, "e2e/**"],
  },
});
