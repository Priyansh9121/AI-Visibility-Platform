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
 * A third kind, since 2026-10-22 (hardening A4): a service the code CAN reach but
 * ships switched off, because the privacy page does not name it yet
 * (`SWITCHED_OFF_PROVIDERS`, below). It is one narrow category and not a way round
 * the rule: see that list.
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

/**
 * An outside service that is in the code and OFF by default until `/privacy` names it.
 *
 * The founder's decision 3.130 (1), 2026-10-11: a new outside service ships switched off, and
 * its privacy line goes to the lawyer (launch checklist 2.4). Until then a host the privacy
 * page does not name would fail `providers.test.ts`, so this is the one place such a host may
 * wait, on three conditions the test holds:
 *
 *   - its host is in the source (the same grep as every other host: an entry the code no longer
 *     shows fails), and in THOSE FILES and no others. The API setting below switches off a call
 *     the API makes; a call from the browser, or from anywhere else, is not controlled by it, and
 *     would otherwise pass here while the privacy page says nothing of it (A4 fix round, the
 *     security review's L3);
 *   - the setting that switches it on exists in `apps/api/src/avp_api/config.py` and defaults to
 *     False, read from the source text. The moment someone makes it True, the test fails until
 *     the privacy page names the service and the entry has moved to `PROVIDERS`;
 *   - the host is named in checklist 2.4's list, where its privacy line waits for the lawyer.
 *
 * Everything else stays as strict as it was: a host that is in neither list still fails.
 */
export interface SwitchedOffProvider {
  /** How the privacy page must name it before the setting is turned on. */
  pageName: string;
  /** `http(s)://` hosts in the source that belong to it. */
  hosts: readonly string[];
  /**
   * The source files its hosts may appear in, relative to the repository root, and only those:
   * the files that make the call the setting below switches off.
   */
  files: readonly string[];
  /** The `Settings` field in `config.py` that switches it on. It must default to False. */
  setting: string;
  /** The same setting as the environment variable an operator sets. */
  variable: string;
}

export const SWITCHED_OFF_PROVIDERS: readonly SwitchedOffProvider[] = [
  {
    // The breached-password check (A4, D10): the first five characters of a password's SHA-1
    // hash go to this host when a password is set. Off until /privacy names it.
    pageName: 'Pwned Passwords',
    hosts: ['api.pwnedpasswords.com'],
    files: ['apps/api/src/avp_api/services/breached_passwords.py'],
    setting: 'breached_password_check_enabled',
    variable: 'BREACHED_PASSWORD_CHECK_ENABLED',
  },
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
