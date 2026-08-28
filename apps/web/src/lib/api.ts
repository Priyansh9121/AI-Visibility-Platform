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
  InviteSeatResponse,
  Me,
  ProblemDetail,
  Report,
  Scan,
  SeatList,
  ShareLink,
  SignUpRequest,
  UserRole,
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

  /**
   * Ask for a password-reset link — Epic 9.13.
   *
   * **Always resolves.** The endpoint answers 200 with the same body whether or
   * not that address has an account, so there is nothing here to branch on and
   * the caller must not invent a difference. The response is deliberately not
   * typed as anything the UI reads beyond "it came back".
   */
  requestPasswordReset: (email: string) =>
    request<{ status: string; detail: string }>('/auth/reset-password/request', {
      method: 'POST',
      body: JSON.stringify({ email }),
    }),

  /**
   * Redeem a reset link and set the new password — Epic 9.13.
   *
   * Rejects with a 400 for a token that is unknown, expired, already used, or
   * belongs to an inactive account — one refusal for all four, so the screen
   * has exactly one thing to say.
   */
  confirmPasswordReset: (token: string, newPassword: string) =>
    request<{ status: string; detail: string }>('/auth/reset-password/confirm', {
      method: 'POST',
      body: JSON.stringify({ token, newPassword }),
    }),

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
   * Who holds a seat, and which invitations are still live — Epic 9.14.
   *
   * `me()` already carries `seats: {used, limit}` and always has. That is the
   * COUNT, which is what the shell renders; this is the ROSTER, which is what a
   * management screen needs. Owner or admin only — a member gets a 403.
   */
  seats: (agencyId: string) =>
    request<SeatList>(`/agencies/${agencyId}/seats`),

  /**
   * Invite an address to a seat — Epic 9.14.
   *
   * The seat limit is enforced server-side inside the insert's transaction, so
   * a full agency rejects with `409 /problems/seat-limit-reached` carrying
   * `seatsUsed` and `seatLimit`. Greying out the form on `me().seats` is a
   * courtesy; this refusal is the actual limit.
   *
   * Re-inviting an address that already holds a pending seat is allowed and
   * consumes no second seat — the response says which happened in
   * `seatConsumed`, and the screen must not report a seat as newly taken when
   * the count did not move.
   *
   * The response deliberately carries NO token and no invite URL. The link goes
   * in one email; a response body is not that email.
   */
  inviteSeat: (agencyId: string, email: string, role: UserRole = 'member') =>
    request<InviteSeatResponse>(`/agencies/${agencyId}/invitations`, {
      method: 'POST',
      body: JSON.stringify({ email, role }),
    }),

  /**
   * Release a seat — Epic 9.14.
   *
   * Ends the person's sessions as well as their seat, on every device, at once.
   * `204`, no body. Refused with a `409` for your own seat or the last owner's,
   * and a `403` when an admin aims at an owner.
   */
  removeSeat: (userId: string) =>
    request<void>(`/users/${userId}`, { method: 'DELETE' }),

  /**
   * Redeem a seat invitation and sign in — Epic 9.14.
   *
   * Unauthenticated: the holder has no account yet, which is what the link is
   * for. Rejects with a 400 for a token that is unknown, expired, already
   * used, revoked, or points at a seat that has since been removed — one
   * refusal for all five, so the screen has exactly one thing to say.
   */
  acceptInvitation: (token: string, fullName: string, password: string) =>
    request<Me>('/auth/invitations/accept', {
      method: 'POST',
      body: JSON.stringify({ token, fullName, password }),
    }),

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
