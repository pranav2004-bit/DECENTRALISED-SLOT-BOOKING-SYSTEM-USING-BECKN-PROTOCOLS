import { defineConfig, globalIgnores } from 'eslint/config';
import nextVitals from 'eslint-config-next/core-web-vitals';
import nextTs from 'eslint-config-next/typescript';

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    '.next/**',
    'out/**',
    'build/**',
    'next-env.d.ts',
    // livetracker3.md §5.2: e2e/ is a real, separate Playwright toolchain (own config, own
    // test runner via `npm run test:e2e`), not part of the Next.js app this config lints —
    // its `.seed-data.json`/`playwright-report/`/`test-results/` artifacts and `test`/`expect`
    // globals aren't meaningful to eslint-config-next's rule set.
    'e2e/**',
  ]),
]);

export default eslintConfig;
