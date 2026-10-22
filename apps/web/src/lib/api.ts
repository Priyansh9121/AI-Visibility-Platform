/**
 * Typed API client.
 *
 * Request and response shapes come from `@avp/shared-types`, which is generated
 * from the FastAPI OpenAPI schema. A backend field rename therefore becomes a
 * compile error here rather than a runtime surprise in the browser.
 */

import type {
  ResetRequestResult,
  AcknowledgeAlert,
  AlertFeed,
  AnswerGaps,
  AutoRescan,
  ClientQuestions,
  CrawlerAccess,
  BillingPlan,
  BillingStatus,
  CheckoutSession,
  Client,
  ClientDetail,
  ClientHistory,
  CompetitorInput,
  CompetitorSet,
  CreateClientRequest,
  Dashboard,
  InviteSeatResponse,
  Me,
  OtherSites,
  ReplaceOtherSites,
  PortalSession,
  ProblemDetail,
  PromptRun,
  PromptRunHistory,
  ReplaceCompetitorsRequest,
  Report,
  RivalSuggestions,
  Scan,
  ScanEmails,
  SeatList,
  ShareLink,
  SignUpRequest,
  TechnicalAudit,
  UserRole,
  ValidationProblemDetail,
  GooglePending,
  GoogleTicket,
  CompleteGoogleSignUpRequest,
} from '@avp/shared-types';
import { isProblemDetail } from '@avp/shared-types';

// Same-origin: the web app's next.config rewrites /api/v1 to the API, so the
// session cookie is first-party. No environment variable chooses this.
const API_BASE = '/api/v1';

/**
 * Where "Sign in with Google" sends the browser — Epic 20.
 *
 * A NAVIGATION, not a request: the API answers with a redirect to Google,
 * and a `fetch` cannot follow a redirect into a sign-in page. So the button
 * is an anchor to this URL, the web app's own `/api/v1/auth/google/start`,
 * which the rewrite hands to the API. The callback also comes back through
 * the web app, so the cookie it sets lands on the web host. Exported for
 * exactly that anchor and nothing else.
 */
export const GOOGLE_SIGN_IN_URL = `${API_BASE}/auth/google/start`;

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
 * A binary GET or POST — Epic 9.14's PDF export. The share-token PDF is a POST with the token in the JSON body, so no token is ever in a URL.
 *
 * Separate from `request` rather than a flag on it, because almost nothing it
 * does applies here: there is no JSON response to parse, no `Content-Type` to set,
 * and no `response.json()` to parse. What IS shared is the part that matters —
 * `credentials: 'include'`, without which the session cookie is silently
 * omitted and every call is a 401.
 *
 * A failure still arrives as `application/problem+json`, so the error path
 * parses one and throws the same `ApiProblem` every other call throws. A
 * caller should not have to handle two error vocabularies depending on what
 * the success case returns.
 */
async function requestBlob(
  path: string,
  init: { method?: 'GET' | 'POST'; body?: unknown } = {},
): Promise<Blob> {
  const hasBody = init.body !== undefined;
  const response = await fetch(`${API_BASE}${path}`, {
    method: init.method ?? 'GET',
    credentials: 'include',
    ...(hasBody
      ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(init.body) }
      : {}),
  });

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

  const blob = await response.blob();
  // The server names the file. When the browser lets us read the header, return
  // a File carrying that name (a File is a Blob); otherwise the plain Blob, and
  // the caller supplies its own fallback name.
  const name = /filename="?([^";]+)"?/i.exec(response.headers?.get?.('Content-Disposition') ?? '')?.[1];
  return name ? new File([blob], name, { type: blob.type }) : blob;
}

/**
 * Hand a blob to the browser as a download, using the server's filename.
 *
 * Fetched with credentials and handed over as a blob URL rather than reached by
 * a plain `<a href>`: the PDF routes are POSTs (the shared one carries its
 * token in the body), and a fetch shows a failure as a sentence where a
 * navigation would leave the page. The API is same-origin now (`/api/v1`), so
 * the session cookie rides along either way.
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
   * Errors: `409 sign-up-not-possible` (a neutral sentence: the address may already have an
   * account, and this does not say), `422 validation-failed`.
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
   * Turn the emails sent when a scan you started finishes on or off — F1.
   *
   * Your own choice only: the request names no user. A JSON boolean, answered with the same
   * `{available, enabled}` that `me()` carries as `scanEmails`.
   */
  setScanEmails: (enabled: boolean) =>
    request<ScanEmails>('/auth/me/scan-emails', {
      method: 'PUT',
      body: JSON.stringify({ enabled }),
    }),

  /**
   * Ask for a password-reset link — Epic 9.13.
   *
   * **Always resolves.** The endpoint answers 200 with the same body whether or
   * not that address has an account, so the caller must not invent a
   * difference. The one thing it may read is `emailConfigured` (2026-09-29):
   * whether this service can send email at all — a fact about the deployment,
   * the same for every address — so the screen never says "check your email"
   * when nothing was sent.
   */
  requestPasswordReset: (email: string) =>
    request<ResetRequestResult>('/auth/reset-password/request', {
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

  /**
   * The agency dashboard — identity, seat usage and the recent scans.
   *
   * `limit` widens the recent-scans window (the endpoint caps it at 50). The
   * sidebar's client list reads its scores off this window — Epic 13 — and
   * asks for the widest one, so as few clients as possible fall outside it.
   */
  /**
   * Who a Google sign-in is for, before the agency is named — Epic 20.
   *
   * A POST so the ticket rides in the body, never a URL. Read-only: the
   * ticket is not spent by reading it, so the completion page survives a
   * reload. `400 invalid-google-ticket` for unknown, used
   * and expired alike.
   */
  googlePending: (ticket: string) =>
    request<GooglePending>('/auth/google/pending', {
      method: 'POST',
      body: JSON.stringify({ ticket }),
    }),

  /**
   * "Join" — the answer to "You've been invited to join {agency}. Join, or start your own
   * agency?" (3.126).
   *
   * Spends the ticket the peek named an agency for, activates the invited seat, and signs the
   * person in: the session cookie is set on this response. A POST, ticket in the body. `400
   * invalid-google-ticket` for a ticket that is unknown, used, expired, another browser's, or for
   * a seat or invitation that is no longer good.
   */
  joinWithGoogle: (ticket: string) =>
    request<Me>('/auth/google/join', {
      method: 'POST',
      body: JSON.stringify({ ticket }),
    }),

  /**
   * "Start your own agency" — the other answer (3.126).
   *
   * Spends the same kind of ticket, revokes the invitation and frees the address, and answers with
   * a NEW ticket for the agency-name step. Nobody is signed in. Same refusals as `joinWithGoogle`.
   */
  declineGoogleInvitation: (ticket: string) =>
    request<GoogleTicket>('/auth/google/decline', {
      method: 'POST',
      body: JSON.stringify({ ticket }),
    }),

  /**
   * Finish creating an agency from a verified Google identity — Epic 20.
   *
   * The Google half of `signUp`: same agency, same owner seat, signed in on
   * creation, no password. The ticket is consumed. `400 invalid-google-ticket`,
   * `409 email-already-registered`, `422`.
   */
  completeGoogleSignUp: (payload: CompleteGoogleSignUpRequest) =>
    request<Me>('/auth/google/complete', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  dashboard: (limit?: number) =>
    request<Dashboard>(limit == null ? '/dashboard' : `/dashboard?limit=${limit}`),

  /**
   * Hide the getting-started checklist for the whole agency — Epic 19.
   *
   * `204`, idempotent, any seat holder. Per agency rather than per browser
   * because the checklist describes the account, and a teammate opening the
   * same workspace should see the same answer.
   */
  dismissGettingStarted: () =>
    request<void>('/dashboard/getting-started/dismiss', { method: 'POST' }),

  /**
   * Start a scan for a client — the dashboard's "re-run".
   *
   * Returns as soon as the scan is QUEUED (`202`), carrying identity and status
   * only — no prompt set and no results, which do not exist yet. Epic 9.5 made
   * the endpoint asynchronous; it previously blocked for the whole ~303s run.
   *
   * Completion is observed with `GET /scans/{scanId}`, or by re-reading the
   * dashboard, which polls it (`lib/dashboard/polling.ts`).
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
   * Whether this client is rescanned every month, and why the last try started nothing — F2.
   *
   * `available` is false (everything else empty) unless the API has automatic rescans on and the
   * caller is an owner or admin: the screen draws nothing then.
   */
  autoRescan: (clientId: string) => request<AutoRescan>(`/clients/${clientId}/auto-rescan`),

  /** Switch "Rescan every month" on or off for one client (owner or admin). A JSON boolean. */
  setAutoRescan: (clientId: string, enabled: boolean) =>
    request<AutoRescan>(`/clients/${clientId}/auto-rescan`, {
      method: 'PUT',
      body: JSON.stringify({ enabled }),
    }),

  /**
   * Whether new questions are asked for at the client's next scan, and the set a rescan would
   * copy (null until there is one) — F6.
   */
  clientQuestions: (clientId: string) => request<ClientQuestions>(`/clients/${clientId}/questions`),

  /** Ask for new questions at the next scan, or take that back. A JSON boolean; 409 while a scan runs. */
  setNewQuestions: (clientId: string, newQuestionsNextScan: boolean) =>
    request<ClientQuestions>(`/clients/${clientId}/questions`, {
      method: 'PUT',
      body: JSON.stringify({ newQuestionsNextScan }),
    }),

  /**
   * The sites with a brand's name under another country's ending: what the agency confirmed and
   * dismissed for the client and its rivals, and what the latest finished scan suggests.
   */
  otherSites: (clientId: string) => request<OtherSites>(`/clients/${clientId}/other-sites`),

  /**
   * Replace all three lists at once (the client's sites, each rival's, the dismissed). A rival
   * left out of `rivals` has its sites cleared, so callers send every rival that holds one.
   * Answers with what a GET would; 409 while a scan of the client runs, 422 with one sentence.
   */
  replaceOtherSites: (clientId: string, body: ReplaceOtherSites) =>
    request<OtherSites>(`/clients/${clientId}/other-sites`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),

  /**
   * The probable rivals of the client's latest scan — F2: domains with the client's own
   * restricted ending (edu.au for rmit.edu.au) that the scan's answers named, offered as rivals to
   * add. Reads; writes nothing. Agency only: nothing in the report carries one. Added through
   * `replaceCompetitors`, the same writer the competitor editor uses.
   */
  rivalSuggestions: (clientId: string) =>
    request<RivalSuggestions>(`/clients/${clientId}/rival-suggestions`),

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
   * Prompts where a rival was named and this client was not — Epic B.
   *
   * `null` rather than a 404 when the client has never produced a scan
   * carrying a grid, which is why the return type is nullable: a client with
   * no scans is a normal state the screen has an empty view for, not a
   * missing resource.
   *
   * `scanId` picks one scan; omitted, the newest is used and the response
   * lists the rest, so the picker costs no second request.
   */
  /**
   * This client's alerts, newest first — Epic E.
   *
   * Carries `scansTotal` and `scansCompared` alongside the rows because an
   * empty feed is ambiguous on its own: most clients have one scan and can
   * therefore never produce an alert. "No alerts" and "nothing was comparable"
   * are different answers and the screen says which it is.
   */
  clientAlerts: (clientId: string) =>
    request<AlertFeed>(`/clients/${clientId}/alerts`),

  /**
   * Mark an alert as seen. Idempotent — a double click does not move the
   * timestamp, and there is deliberately no un-acknowledge.
   */
  acknowledgeAlert: (alertId: string) =>
    request<AcknowledgeAlert>(`/alerts/${alertId}/acknowledge`, { method: 'POST' }),

  answerGaps: (clientId: string, scanId?: string) =>
    request<AnswerGaps | null>(
      `/clients/${clientId}/answer-gaps${scanId ? `?scanId=${encodeURIComponent(scanId)}` : ''}`,
    ),

  /**
   * What this client's robots.txt asks each AI crawler to do — Epic F.
   *
   * A READ of what a scan's technical audit already parsed. It does not fetch
   * robots.txt, which is why the same client can answer differently for
   * different scans and why an old scan keeps saying what was true then.
   *
   * Returns null when no scan has ever recorded a policy — every scan
   * predating the feature, which is a normal state with an empty view, not a
   * missing resource.
   */
  aiCrawlerAccess: (clientId: string, scanId?: string) =>
    request<CrawlerAccess | null>(
      `/clients/${clientId}/ai-crawler-access${scanId ? `?scanId=${encodeURIComponent(scanId)}` : ''}`,
    ),

  /**
   * A scan's technical audit — Epic 9.22, wiring an endpoint that has existed
   * since Epic 6 and which nothing in the browser had ever called directly.
   *
   * The report already shows this data folded into its fix beat. This is the
   * same audit read on its own, so a client's Technical screen can draw the
   * four weighted components the sub-score is actually made of.
   */
  audit: (scanId: string) => request<TechnicalAudit>(`/scans/${scanId}/audit`),

  /**
   * A client's ad-hoc prompt history — Epic 9.24.
   *
   * Newest first, unlike `clientHistory`: this is a LIST of things an operator
   * did, and the most recent one is the one they are looking for. A trend wants
   * its earliest point at the left; a log wants its latest entry at the top.
   *
   * Carries what the throttle has left, so the screen can say so before a run
   * is attempted rather than discovering it in a 429.
   */
  promptRuns: (clientId: string) =>
    request<PromptRunHistory>(`/clients/${clientId}/prompt-runs`),

  /**
   * Run one prompt against every engine, now.
   *
   * Synchronous, and slow on purpose — three concurrent engine calls, ~23s
   * median. The caller must show that it is working; there is no job to poll
   * because there is no job. See routers/prompt_runs.py.
   */
  runPrompt: (clientId: string, prompt: string) =>
    request<PromptRun>(`/clients/${clientId}/prompt-runs`, {
      method: 'POST',
      body: JSON.stringify({ prompt }),
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
   * Mint (or fetch) the public share link for a scan's report — Epic 9.8.
   *
   * Idempotent while a link is live: asking again returns the same URL and the
   * same `expiresAt` rather than a second live link to the same report, because
   * every extra token would be a URL nobody is tracking, and a press does not
   * move a live link's end (checklist 3.28). Once the link has been withdrawn
   * or has ended, the next call makes a NEW token with a fresh 30 days. A failed
   * scan is refused with a 409 `/problems/failed-scan-not-shareable`, even when a
   * link is live.
   */
  shareLink: (scanId: string) =>
    request<ShareLink>(`/scans/${scanId}/share`, { method: 'POST' }),

  /**
   * Take the public link down — Epic 9.21. `204`, no body, idempotent. The next
   * `shareLink` call mints a NEW token; the withdrawn one never works again.
   */
  withdrawShareLink: (scanId: string) =>
    request<void>(`/scans/${scanId}/share`, { method: 'DELETE' }),

  /**
   * A report by share token — the UNAUTHENTICATED read, Epic 9.8.
   *
   * Called from /share#{token} by someone with no account and no cookie. Goes
   * through the same `request` helper as everything else: it sends credentials
   * that will not exist, which the endpoint ignores. Any bad token is a 404,
   * deliberately indistinguishable from any other bad token.
   */
  publicReport: (token: string) =>
    request<Report>('/reports/shared', { method: 'POST', body: JSON.stringify({ token }) }),

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
   * The link goes in one email, so with email set up the response carries no
   * token and no invite URL. Without it (2026-09-29) `acceptUrl` carries the
   * link, this once, to the owner or admin who made it — otherwise nobody
   * could ever deliver it. It is never shown again; re-inviting mints a new one.
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
   * "Keep these people": close the "Check who has access" notice — A6 part 4 (3.123).
   *
   * Shown in Settings > Team to an owner or admin whose Google sign-in replaced their password,
   * as `accessReview` on `seats()`. `204`, idempotent, and only ever the caller's own: the request
   * names nobody and carries no body. Removing someone from the list is `removeSeat`, as from the
   * roster; this is the other answer.
   */
  keepAccessReview: () =>
    request<void>('/auth/me/access-review/keep', { method: 'POST' }),

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
   * The report's figures as one CSV — F3, 2026-10-06. Signed in only, and off unless the API's
   * `REPORT_CSV_ENABLED` is on: the report says so itself (`csvExportAvailable`), and the page
   * draws the button from that and nothing else.
   *
   * There is no share-token form of this and there will not be one. It is a GET by scan id, so
   * the same rule as the PDF applies to a refusal: it arrives as a problem and is thrown as one
   * (a 409 `/problems/scan-not-exportable` carries one sentence for the person at the screen).
   */
  reportCsv: (scanId: string) => requestBlob(`/scans/${scanId}/report.csv`),

  /**
   * The same PDF, by share token — Epic 9.14, unauthenticated.
   *
   * A stranger holding the link can download the file as well as read the
   * page. It carries strictly the facts `publicReport` already serves them, so
   * withholding it would protect nothing while making the send path worse.
   */
  publicReportPdf: (token: string) =>
    requestBlob('/reports/shared/pdf', { method: 'POST', body: { token } }),

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
   * What the plan offers the public: the length of its free trial — 2026-10-04.
   *
   * `GET /billing/plan`: no session, no agency, nothing secret, never cached. The
   * public pricing card and the landing page read it before anybody has signed up,
   * so the length of the trial is stated in ONE place (the API's `STRIPE_TRIAL_DAYS`)
   * and the pages carry no copy of it. `trialDays` is 0 when there is no trial. Read
   * through `loadTrialDays` in `lib/billing/trial`, which treats a failure as "no trial".
   */
  billingPlan: () => request<BillingPlan>('/billing/plan'),

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
   * The client's current competitor list: its next list (what a save or a detection run last
   * wrote, and what the next scan will use), else the set of its most recent scan. It is what a
   * save replaces, which on an older scan is not the list that scan shows. Null when the client
   * has neither: the API answers 404 for that. Its `version` is what a save names to say which
   * list it read (`replaceCompetitors`).
   */
  competitorSet: async (clientId: string): Promise<CompetitorSet | null> => {
    try {
      return await request<CompetitorSet>(`/clients/${clientId}/competitors`);
    } catch (err) {
      if (err instanceof ApiProblem && err.status === 404) return null;
      throw err;
    }
  },

  /**
   * Replace a client's competitor list by hand — Epic 3. The list is saved for the client's LATER
   * scans: the API writes its next list, and a scan that has started keeps the list it started
   * with (launch checklist 3.78, 3.79).
   *
   * A PUT, not a PATCH, because the endpoint replaces the list wholesale: an
   * operator correcting a bad detection is asserting "these are the rivals",
   * and every competitor supplied is marked `isManualOverride` so a later
   * re-detection cannot silently undo the correction. Callers must send the
   * full intended list, not a delta.
   *
   * `version` is the `version` of the list the caller READ (from `competitorSet`, or from any
   * answer that returned a list), or `null` when it read none because the client has none (launch
   * checklist 3.113). It is required, so a writer cannot forget it: a save made from a list that
   * somebody has since changed would strike what it never saw. When the list is no longer the one
   * named, the API writes nothing and answers 409 `/problems/competitor-list-changed`, whose
   * `current` member is the list as it stands (`lib/competitorListChange`).
   */
  replaceCompetitors: (
    clientId: string,
    competitors: CompetitorInput[],
    version: string | null,
  ) => {
    const body: ReplaceCompetitorsRequest = { competitors, version };
    return request<CompetitorSet>(`/clients/${clientId}/competitors`, {
      method: 'PUT',
      body: JSON.stringify(body),
    });
  },
};
