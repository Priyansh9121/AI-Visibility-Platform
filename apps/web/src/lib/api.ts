/**
 * Typed API client.
 *
 * Request and response shapes come from `@avp/shared-types`, which is generated
 * from the FastAPI OpenAPI schema. A backend field rename therefore becomes a
 * compile error here rather than a runtime surprise in the browser.
 */

import type {
  Client,
  ClientDetail,
  CompetitorInput,
  CompetitorSet,
  CreateClientRequest,
  Dashboard,
  Me,
  ProblemDetail,
  Report,
  Scan,
  ShareLink,
  SignUpRequest,
  ValidationProblemDetail,
} from '@avp/shared-types';
import { isProblemDetail } from '@avp/shared-types';

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000/api/v1';

/** An RFC 9457 problem returned by the API, carried as a throwable. */
export class ApiProblem extends Error {
  readonly problem: ProblemDetail;

  constructor(problem: ProblemDetail) {
    super(problem.detail || problem.title);
    this.name = 'ApiProblem';
    this.problem = problem;
  }

  get type(): string {
    return this.problem.type;
  }

  get status(): number {
    return this.problem.status;
  }

  /** Field-level messages, when this is a validation problem. */
  fieldErrors(): Record<string, string> {
    const errors = (this.problem as ValidationProblemDetail).errors;
    if (!Array.isArray(errors)) return {};
    return Object.fromEntries(errors.map((e) => [e.field, e.message]));
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    // Session auth is an httpOnly cookie, so every call must send credentials.
    // Without this the browser silently omits it and every request is a 401.
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(init.headers ?? {}),
    },
  });

  if (response.status === 204) return undefined as T;

  const body: unknown = await response.json().catch(() => null);

  if (!response.ok) {
    if (isProblemDetail(body)) throw new ApiProblem(body);
    throw new ApiProblem({
      type: '/problems/unknown',
      title: 'Request failed',
      status: response.status,
      detail: `The server returned ${response.status}.`,
      instance: path,
    });
  }

  return body as T;
}

export const api = {
  me: () => request<Me>('/auth/me'),

  /**
   * Create an agency and its first user, and sign them in — Epic 9.12.
   *
   * `POST /auth/sign-up` has existed since Epic 1.3 and nothing in the browser
   * had ever called it: the landing page invited strangers in through a door
   * that only opened from the inside.
   *
   * Returns `MeOut` and sets the session cookie, so there is no second login
   * step — api-contracts.md calls that out as deliberate ("requiring a fresh
   * login right after choosing a password is friction with no security value").
   *
   * Errors: `409 email-already-registered`, `422 validation-failed`.
   */
  signUp: (payload: SignUpRequest) =>
    request<Me>('/auth/sign-up', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  logIn: (email: string, password: string) =>
    request<Me>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }),

  logOut: () => request<void>('/auth/logout', { method: 'POST' }),

  /** The agency dashboard — identity, seat usage and the recent scans. */
  dashboard: () => request<Dashboard>('/dashboard'),

  /**
   * Start a scan for a client — the dashboard's "re-run".
   *
   * Returns as soon as the scan is QUEUED (`202`), carrying identity and status
   * only — no prompt set and no results, which do not exist yet. Epic 9.5 made
   * the endpoint asynchronous; it previously blocked for the whole ~303s run.
   *
   * Completion is observed with `GET /scans/{scanId}`, or by re-reading the
   * dashboard. This client does not poll — building the poller is a follow-up.
   */
  runScan: (clientId: string) =>
    request<Scan>(`/clients/${clientId}/scans`, {
      method: 'POST',
      body: JSON.stringify({}),
    }),

  /**
   * Every client this agency has added — Epic 9.13's Clients screen.
   *
   * The endpoint has existed since Epic 2 and nothing in the browser called it,
   * the same way `sign-up` sat unused until 9.12. Cursor-paginated; the screen
   * reads the first page and says so rather than pretending it is the whole set.
   */
  clients: () => request<{ data: Client[]; nextCursor: string | null }>('/clients'),

  createClient: (payload: CreateClientRequest) =>
    request<ClientDetail>('/clients', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /**
   * The narrative report for a scan — Epic 7.
   *
   * One call rather than four. The report needs the score, the competitor set,
   * the audit and aggregates over every engine result; assembling that in the
   * browser would mean paging the raw results endpoint and re-deriving citation
   * and mention counts on each render.
   */
  report: (scanId: string) => request<Report>(`/scans/${scanId}/report`),

  /**
   * Mint (or fetch) the public share link for a scan's report — Epic 9.8.
   *
   * Idempotent: asking twice returns the same URL rather than a second live
   * link to the same report, because there is no revocation and every extra
   * token would be a URL nobody is tracking.
   */
  shareLink: (scanId: string) =>
    request<ShareLink>(`/scans/${scanId}/share`, { method: 'POST' }),

  /**
   * A report by share token — the UNAUTHENTICATED read, Epic 9.8.
   *
   * Called from /share/{token} by someone with no account and no cookie. Goes
   * through the same `request` helper as everything else: it sends credentials
   * that will not exist, which the endpoint ignores. Any bad token is a 404,
   * deliberately indistinguishable from any other bad token.
   */
  publicReport: (token: string) => request<Report>(`/reports/${token}`),

  /**
   * Replace a client's competitor set by hand — Epic 3.
   *
   * A PUT, not a PATCH, because the endpoint replaces the set wholesale: an
   * operator correcting a bad detection is asserting "these are the rivals",
   * and every competitor supplied is marked `isManualOverride` so a later
   * re-detection cannot silently undo the correction. Callers must send the
   * full intended set, not a delta.
   */
  replaceCompetitors: (clientId: string, competitors: CompetitorInput[]) =>
    request<CompetitorSet>(`/clients/${clientId}/competitors`, {
      method: 'PUT',
      body: JSON.stringify({ competitors }),
    }),
};
