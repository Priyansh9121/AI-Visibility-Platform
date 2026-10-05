/**
 * Every outside party the code sends data to or loads from — grep-derived,
 * 2026-09-29.
 *
 * Built by grepping every `http(s)://` host in the four source trees and every
 * runtime dependency in the manifests (the sweep is in the build log's
 * 2026-09-29 privacy entry), then classifying each one: a PROVIDER the privacy
 * page must name, or NOT a provider — a library, a comment, a placeholder, a
 * link. `providers.test.ts` re-runs the same grep on every run, and anything in
 * neither list fails it. So a new provider cannot arrive unnamed: it has to be
 * classified here, and a provider here has to appear on the page.
 *
 * Hosts reached with no literal in our source are covered another way: the
 * Anthropic and Stripe SDKs by their dependency, and the two hosts by the
 * founder's word.
 *
 * jsDelivr left on 2026-09-29. It served the viewer for the API's docs page,
 * and staging and production no longer serve that page (`main.py`), so the
 * running service loads nothing from it.
 */

export interface OutboundProvider {
  /** How the privacy page names it — the test looks for exactly this text. */
  pageName: string;
  /** `http(s)://` hosts in the source that belong to it. */
  hosts?: readonly string[];
  /** Runtime dependencies (Python or npm) that are its client library. */
  dependencies?: readonly string[];
  /** No literal host and no SDK: a line that must still exist in a file. */
  evidence?: { file: string; pattern: RegExp };
  /** Configuration, not code: nothing in the repository can show it. */
  deployment?: string;
}

export const PROVIDERS: readonly OutboundProvider[] = [
  {
    pageName: 'Render',
    deployment:
      'Runs the web app and the API (their *.onrender.com hosts are in the build log); Redis ' +
      'is Render Key Value, confirmed by the founder on 2026-09-29.',
  },
  {
    pageName: 'Supabase',
    deployment:
      'The Postgres behind DATABASE_URL, on the founder\'s word in the 2026-09-29 brief. The ' +
      'repository names it only as a candidate host.',
  },
  { pageName: 'Stripe', dependencies: ['stripe'] },
  {
    pageName: 'Google sign-in',
    hosts: ['accounts.google.com', 'oauth2.googleapis.com', 'www.googleapis.com'],
  },
  { pageName: 'Anthropic', dependencies: ['anthropic'] },
  { pageName: 'OpenAI', hosts: ['api.openai.com'] },
  { pageName: 'Google Gemini', hosts: ['generativelanguage.googleapis.com'] },
  { pageName: 'Perplexity', hosts: ['api.perplexity.ai'] },
  { pageName: 'SerpApi', hosts: ['serpapi.com'] },
  { pageName: 'Resend', hosts: ['api.resend.com'] },
  { pageName: 'Google Fonts', hosts: ['fonts.googleapis.com', 'fonts.gstatic.com'] },
];

/** Hosts that appear in the source but carry nobody's data anywhere. */
export const NOT_PROVIDER_HOSTS: readonly string[] = [
  'localhost', // development defaults, and comments about them
  'a.com', // a comment showing the CORS setting's format
  'b.com', // the same comment
  'www.oaic.gov.au', // a link on the privacy page itself
];

/** Runtime dependencies that are libraries, not services. */
export const NOT_PROVIDER_DEPENDENCIES: readonly string[] = [
  // apps/api
  'fastapi',
  'uvicorn',
  'pydantic',
  'pydantic-settings',
  'email-validator',
  'sqlalchemy',
  'alembic',
  'asyncpg',
  'argon2-cffi',
  'redis',
  'python-ulid',
  'structlog',
  'playwright', // drives Chromium to visit the client's own site
  'tldextract', // its online suffix-list fetch is switched off (crawl.py)
  'httpx',
  'idna', // encodes a client's hostname in process (services/intake.py); contacts nothing
  'pyjwt',
  // apps/workers
  'celery',
  'avp-api',
  // apps/web
  '@avp/design-system',
  '@avp/shared-types',
  'lucide-react',
  'next',
  'next-themes',
  'react',
  'react-dom',
];
