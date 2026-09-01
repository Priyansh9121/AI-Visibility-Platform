/**
 * Typed API client.
 *
 * Request and response shapes come from `@avp/shared-types`, which is generated
 * from the FastAPI OpenAPI schema. A backend field rename therefore becomes a
 * compile error here rather than a runtime surprise in the browser.
 */

import type {
  BillingStatus,
  CheckoutSession,
  PortalSession,
  Client,
  ClientHistory,
  TechnicalAudit,
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

/**
 * A binary GET — Epic 9.14's PDF export.
 *
 * Separate from `request` rather than a flag on it, because almost nothing it
 * does applies here: there is no JSON body to send, no `Content-Type` to set,
 * and no `response.json()` to parse. What IS shared is the part that matters —
 * `credentials: 'include'`, without which the session cookie is silently
 * omitted and every call is a 401.
 *
 * A failure still arrives as `application/problem+json`, so the error path
 * parses one and throws the same `ApiProblem` every other call throws. A
 * caller should not have to handle two error vocabularies depending on what
 * the success case returns.
 */
async function requestBlob(path: string): Promise<Blob> {
  const response = await fetch(`${API_BASE}${path}`, { credentials: 'include' });

  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    if (isProblemDetail(body)) throw new ApiProblem(body);
    throw new ApiProblem({
      type: '/problems/unknown',
      title: 'Request failed',
      status: response.status,
      detail: `The server returned ${response.status}.`,
      instance: path,
    });
  }

  return response.blob();
}

/**
 * Hand a blob to the browser as a download, using the server's filename.
 *
 * A plain `<a href>` to the API would be simpler and is wrong here: the API is
 * a different origin in every environment this runs in, so the download would
 * depend on the session cookie surviving a cross-origin top-level navigation —
 * which `SameSite` governs and which changes with deployment topology. Fetching
 * with credentials and clicking a blob URL works the same everywhere.
 *
 * The object URL is revoked afterwards. Left alive it pins the whole file in
 * memory until the tab closes, and a report is not small.
 */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
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
   * Change your own password while signed in — Epic 9.14.
   *
   * The current password is re-verified server-side: a session proves somebody
   * got in once, not that they are still the account holder.
   *
   * **Every OTHER session is revoked; this one survives on a fresh cookie.**
   * That is deliberately not the reset flow's answer — see the endpoint's own
   * docstring. It means the caller stays signed in and every other device is
   * signed out, which is what someone changing a password usually intends.
   *
   * Rejects with `401 invalid-credentials` for a wrong current password (there
   * is no "no such user" branch — the caller is authenticated) and `422` for a
   * new password that fails the sign-up rules or is the current one.
   */
  changePassword: (currentPassword: string, newPassword: string) =>
    request<Me>('/auth/change-password', {
      method: 'POST',
      body: JSON.stringify({ currentPassword, newPassword }),
    }),

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

  client: (clientId: string) => request<Client>(`/clients/${clientId}`),

  /**
   * A client's whole scan history, shaped for a trend — Epic 9.20.
   *
   * One call rather than one report per scan, for the reason
   * `services/client_history.py` sets out and measured: the report path is
   * ~2.7x the work per scan and most of what it returns (narrative, audit,
   * fixes) is discarded by a trend line, and its cited-domain lists are
   * truncated for display — so a Sources trend built from them would silently
   * be a trend over whatever survived a display cap.
   *
   * Oldest first. The clients LIST is newest-first, deliberately: a list wants
   * the newest thing at the top, a trend wants the earliest at the left.
   */
  clientHistory: (clientId: string) =>
    request<ClientHistory>(`/clients/${clientId}/history`),

  /**
   * A scan's technical audit — Epic 9.22, wiring an endpoint that has existed
   * since Epic 6 and which nothing in the browser had ever called directly.
   *
   * The report already shows this data folded into its fix beat. This is the
   * same audit read on its own, so a client's Technical screen can draw the
   * four weighted components the sub-score is actually made of.
   */
  audit: (scanId: string) => request<TechnicalAudit>(`/scans/${scanId}/audit`),

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
   * The report as a PDF — Epic 9.14.
   *
   * The SAME document the report screen shows, rendered server-side from the
   * same `build_report` payload. It is not a second report and must never be
   * described to a user as an "export" of something different.
   *
   * Degrades identically: an unscored scan downloads a PDF that says "Not
   * scored", exactly as the screen does — never a zero, and never a failure.
   */
  reportPdf: (scanId: string) => requestBlob(`/scans/${scanId}/report.pdf`),

  /**
   * The same PDF, by share token — Epic 9.14, unauthenticated.
   *
   * A stranger holding the link can download the file as well as read the
   * page. It carries strictly the facts `publicReport` already serves them, so
   * withholding it would protect nothing while making the send path worse.
   */
  publicReportPdf: (token: string) => requestBlob(`/reports/${token}.pdf`),

  /**
   * This agency's subscription, as recorded — Epic 9.15.
   *
   * Reads the columns the Stripe webhook maintains. **It does not ask Stripe**,
   * so it is as fast as any other read here and does not fail when a payment
   * processor is slow.
   *
   * Owner or admin only; a member gets a 403, which the settings screen
   * degrades the same way it degrades a forbidden seat roster.
   *
   * Every field is null/false for an agency that has not subscribed. That is
   * not an error state and must not be rendered as one — nothing in the
   * product is gated on it.
   */
  billing: (agencyId: string) =>
    request<BillingStatus>(`/agencies/${agencyId}/billing`),

  /**
   * Create a Stripe Checkout Session and get the URL to navigate to — 9.15.
   *
   * The caller's next move is `window.location.assign(url)`. There is no Stripe
   * JavaScript library in this app and this shape is why one is not needed: the
   * browser goes to a page Stripe hosts and comes back.
   *
   * **Calling this does not subscribe anybody.** It returns a URL. The
   * subscription starts when Stripe tells the API it did, over the webhook —
   * which is also why closing the tab mid-payment does not lose it.
   *
   * Rejects with `503 billing-not-configured` when a Stripe setting is unset,
   * carrying a detail that names the exact environment variable.
   */
  startCheckout: (agencyId: string) =>
    request<CheckoutSession>(`/agencies/${agencyId}/billing/checkout`, {
      method: 'POST',
    }),

  /**
   * Open Stripe's hosted billing portal — Epic 9.15.
   *
   * Update a card, cancel, download an invoice. Same shape as `startCheckout`:
   * an endpoint returns a URL and the browser navigates to it. Building any of
   * those screens here would mean handling card details, dunning states and
   * invoice PDFs — a product in itself, and one Stripe already ships.
   *
   * Requires an existing Stripe customer. An agency that has never subscribed
   * gets `503`, because the portal for one would be an empty page with no card
   * and no invoices — a worse answer than the button not being there, which is
   * why Settings only offers it once a subscription exists.
   */
  openBillingPortal: (agencyId: string) =>
    request<PortalSession>(`/agencies/${agencyId}/billing/portal`, {
      method: 'POST',
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
