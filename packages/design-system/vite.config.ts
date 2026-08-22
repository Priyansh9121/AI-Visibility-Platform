import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

/**
 * The style guide runs as its own tiny Vite app inside the design-system
 * package rather than as a route in apps/web.
 *
 * Two reasons. Epic 0's acceptance criterion needs a rendered style guide
 * before Epic 1 scaffolds Next.js, and keeping the guide here means the design
 * system stays portable — it can be reviewed, and its components exercised,
 * without booting the product.
 */
export default defineConfig({
  plugins: [react()],
  root: '.',
  server: { port: 4100, open: false },
  build: { outDir: 'dist-styleguide' },
});
