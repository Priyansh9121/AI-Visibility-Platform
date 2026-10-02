/**
 * Next calls register() once when the server starts. It is picked up from
 * src/instrumentation.ts with no config flag (stable in Next 15).
 */
export async function register(): Promise<void> {
  if (process.env.NEXT_RUNTIME === 'nodejs') {
    const { installProxyLogRedaction } = await import('./lib/proxyLogRedaction');
    installProxyLogRedaction();
  }
}
