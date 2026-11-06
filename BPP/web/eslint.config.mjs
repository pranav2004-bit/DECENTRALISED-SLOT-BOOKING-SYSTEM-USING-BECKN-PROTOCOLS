import { defineConfig, globalIgnores } from 'eslint/config';
import nextVitals from 'eslint-config-next/core-web-vitals';
import nextTs from 'eslint-config-next/typescript';

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    rules: {
      // Arrived via a transitive eslint-plugin-react-hooks bump
      // (package-lock.json), not a deliberate adoption — part of the React
      // Compiler-readiness ruleset; this app doesn't use the Compiler. Flags
      // the "reset/load state when a dependency changes" pattern React's own
      // docs endorse (resources/page.tsx's loadRecentSlots/loadDoctors,
      // account-context.tsx's setLoading) — real, intentional, working code.
      'react-hooks/set-state-in-effect': 'off',
    },
  },
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    '.next/**',
    'out/**',
    'build/**',
    'next-env.d.ts',
    // scripts/dev.js's own NEXT_DIST_DIR — a per-brand build cache (next.config.ts),
    // same compiled-output status as .next/** above, just never covered by the
    // default ignores since those predate the multi-brand local dev setup.
    '.next-*/**',
    // A plain CommonJS Node CLI script (scripts/dev.js), not part of the Next.js
    // app bundle this config is meant to lint — require() is correct there.
    'scripts/**',
  ]),
]);

export default eslintConfig;
