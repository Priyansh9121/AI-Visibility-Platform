/**
 * Typed API client.
 *
 * Request and response shapes come from `@avp/shared-types`, which is generated
 * from the FastAPI OpenAPI schema. A backend field rename therefore becomes a
 * compile error here rather than a runtime surprise in the browser.
 */

import type {
  ClientDetail,
  CompetitorInput,
  CompetitorSet,
  CreateClientRequest,
  Dashboard,
  Me,
  ProblemDetail,
  Report,
  ScanDetail,
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
   * **This request does not return until the scan has finished.** The endpoint
   * runs the whole pipeline inline and commits once at the end, which Epic 9.2
   * measured at 361.3s. It is not a job submission, so there is no id to poll
   * for while it runs; see DashboardView's note.
   */
  runScan: (clientId: string) =>
    request<ScanDetail>(`/clients/${clientId}/scans`, {
      method: 'POST',
      body: JSON.stringify({}),
    }),

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
