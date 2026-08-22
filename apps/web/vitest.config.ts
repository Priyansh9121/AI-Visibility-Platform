import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

/**
 * apps/web had no test runner before Epic 7 — the package's `test` script was a
 * placeholder from Epic 2. The report is the first screen with logic worth
 * testing rather than markup worth looking at: the narrative is DERIVED, so the
 * derivation can be wrong, and a wrong derivation is a report that argues the
 * opposite of what the data says.
 *
 * vitest rather than a second runner, because the design system already uses it
 * and the two suites share the same renderToStaticMarkup approach.
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    include: ['src/**/*.test.{ts,tsx}'],
  },
});
