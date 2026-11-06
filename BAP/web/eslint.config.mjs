import { defineConfig, globalIgnores } from 'eslint/config';
import nextVitals from 'eslint-config-next/core-web-vitals';
import nextTs from 'eslint-config-next/typescript';

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    rules: {
      // These two rules arrived via a transitive eslint-plugin-react-hooks bump
      // (package-lock.json), not a deliberate adoption — they're part of the
      // React Compiler-readiness ruleset and this app doesn't use the Compiler.
      // `set-state-in-effect` flags the "reset state when a dependency changes"
      // pattern that React's own docs endorse (search/page.tsx resets
      // selectedSlot/searchOpen/etc. when selectedProviderId/division changes) —
      // real, intentional, working code, not a bug. `purity` flags a plain
      // `Date.now()` call inside a setState updater (search/page.tsx's hold-
      // countdown), which is fine outside React Compiler's memoization model.
      'react-hooks/set-state-in-effect': 'off',
      'react-hooks/purity': 'off',
    },
  },
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
