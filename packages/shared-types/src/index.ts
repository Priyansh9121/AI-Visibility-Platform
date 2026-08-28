/**
 * @avp/shared-types — the TS half of the TS<->Python contract (§5.2).
 *
 * `src/api.gen.ts` is GENERATED. Do not edit it. The pipeline is:
 *
 *   1. FastAPI is the source of truth for every request/response shape
 *   2. `apps/api/scripts/export_openapi.py` dumps `openapi.json`
 *   3. `pnpm --filter @avp/shared-types generate` emits `src/api.gen.ts`
 *
 * Hand-writing parallel type definitions in two languages was rejected in
 * Epic 0.3: they drift silently, and the drift surfaces as a runtime bug in
 * the browser rather than a compile error. Generating from one source makes
 * drift impossible — a backend field rename becomes a TypeScript error in
 * apps/web at build time.
 *
 * The aliases below are hand-maintained ergonomics ONLY. They must never
 * restate a shape; they only give the generated ones readable names.
 */

import type { components, operations, paths } from './api.gen.js';

export type { components, operations, paths };

/** Every schema object the API defines. */
export type Schemas = components['schemas'];

// --- entity aliases -----------------------------------------------------
export type User = Schemas['UserOut'];
export type Agency = Schemas['AgencyOut'];
export type SeatUsage = Schemas['SeatUsageOut'];
export type Me = Schemas['MeOut'];
export type Dashboard = Schemas['DashboardOut'];
export type Client = Schemas['ClientOut'];
export type ClientDetail = Schemas['ClientDetailOut'];
export type CrawlSummary = Schemas['CrawlSummaryOut'];
export type Scan = Schemas['ScanOut'];
export type ScanDetail = Schemas['ScanDetailOut'];
export type PromptSet = Schemas['PromptSetOut'];
export type Prompt = Schemas['PromptOut'];
export type EngineResult = Schemas['EngineResultOut'];
export type Score = Schemas['ScoreOut'];
export type ScoreDetail = Schemas['ScoreDetailOut'];
export type TechnicalAudit = Schemas['TechnicalAuditOut'];
export type AuditCheck = Schemas['AuditCheckOut'];
export type AuditStatus = Schemas['AuditStatus'];
export type CheckStatus = Schemas['CheckStatus'];
export type CompetitorScore = Schemas['CompetitorScoreOut'];
export type ScoreStatus = Schemas['ScoreStatus'];
export type BrandMention = Schemas['BrandMentionOut'];
export type Citation = Schemas['CitationOut'];
export type Engine = Schemas['Engine'];
export type PromptIntent = Schemas['PromptIntent'];
export type ClassificationStatus = Schemas['ClassificationStatus'];
export type ScanSummary = Schemas['ScanSummaryOut'];

// --- competitor override (Epic 3.6) -------------------------------------
export type Competitor = Schemas['CompetitorOut'];
export type CompetitorSet = Schemas['CompetitorSetOut'];
export type CompetitorInput = Schemas['CompetitorInput'];
export type ReplaceCompetitorsRequest = Schemas['ReplaceCompetitorsRequest'];
export type DetectionSource = Schemas['DetectionSource'];
export type DetectionStatus = Schemas['DetectionStatus'];

// --- report (Epic 7) ----------------------------------------------------
export type Report = Schemas['ReportOut'];
export type ReportAgency = Schemas['ReportAgencyOut'];
export type ReportSubject = Schemas['ReportSubjectOut'];
export type ReportDimension = Schemas['ReportDimensionOut'];
export type ReportCompetitor = Schemas['ReportCompetitorOut'];
export type ReportCompetitorSet = Schemas['ReportCompetitorSetOut'];
export type ReportProof = Schemas['ReportProofOut'];
export type ReportAudit = Schemas['ReportAuditOut'];
export type ReportAuditFinding = Schemas['ReportAuditFindingOut'];
export type EngineCoverage = Schemas['EngineCoverageOut'];
export type CitedDomain = Schemas['CitedDomainOut'];
export type MentionShare = Schemas['MentionShareOut'];

// --- Answer Shelf + unclaimed domains (Epic 7.1) ------------------------
export type PromptShelfRow = Schemas['PromptShelfOut'];
export type ShelfSlot = Schemas['ShelfSlotOut'];

// --- public share link (Epic 9.8) ---------------------------------------
export type ShareLink = Schemas['ShareLinkOut'];

// --- seat management (Epic 9.14) ----------------------------------------
export type SeatList = Schemas['SeatListOut'];
export type PendingInvitation = Schemas['PendingInvitationOut'];
export type InviteSeatRequest = Schemas['InviteSeatRequest'];
export type InviteSeatResponse = Schemas['InviteSeatResponse'];

// --- billing (Epic 9.15) ------------------------------------------------
export type BillingStatus = Schemas['BillingStatusOut'];
export type CheckoutSession = Schemas['CheckoutSessionOut'];
export type PortalSession = Schemas['PortalSessionOut'];

// --- action list (Epic 8) -----------------------------------------------
export type ActionItem = Schemas['ActionItemOut'];
export type ActionItemList = Schemas['ActionItemListOut'];
export type ActionItemSource = Schemas['ActionItemSource'];
export type ActionItemStatus = Schemas['ActionItemStatus'];
export type Priority = Schemas['Priority'];
export type Effort = Schemas['Effort'];

// --- request aliases ----------------------------------------------------
export type SignUpRequest = Schemas['SignUpRequest'];
// --- password reset (Epic 9.13) ------------------------------------------
export type ResetPasswordRequest = Schemas['ResetPasswordRequest'];
export type ResetPasswordConfirm = Schemas['ResetPasswordConfirm'];
// --- seat invitations + password change (Epic 9.14) ----------------------
export type AcceptInvitationRequest = Schemas['AcceptInvitationRequest'];
export type ChangePasswordRequest = Schemas['ChangePasswordRequest'];
export type LoginRequest = Schemas['LoginRequest'];
export type CreateClientRequest = Schemas['CreateClientRequest'];
export type RunScanRequest = Schemas['RunScanRequest'];

// --- enumerations -------------------------------------------------------
export type UserRole = Schemas['UserRole'];
export type UserStatus = Schemas['UserStatus'];
export type ScanStatus = Schemas['ScanStatus'];

/**
 * RFC 9457 problem document — the shape of EVERY error this API returns.
 *
 * Declared by hand because FastAPI does not describe its error responses in
 * the OpenAPI schema, so there is nothing to generate from. The contract is
 * fixed in docs/api-contracts.md and enforced by tests in apps/api.
 */
export interface ProblemDetail {
  /** URI reference identifying the problem type, e.g. `/problems/not-found`. */
  type: string;
  title: string;
  status: number;
  detail: string;
  instance: string;
  /** Extension members. Validation problems carry `errors`. */
  [key: string]: unknown;
}

export interface ValidationProblemDetail extends ProblemDetail {
  errors: { field: string; message: string; type: string }[];
}

export function isProblemDetail(value: unknown): value is ProblemDetail {
  return (
    typeof value === 'object' &&
    value !== null &&
    'type' in value &&
    'status' in value &&
    'title' in value
  );
}

/** Cursor-paginated envelope. See docs/api-contracts.md. */
export interface Page<T> {
  data: T[];
  nextCursor: string | null;
}

/** Entity id prefixes, mirroring apps/api/src/avp_api/ids.py. */
export const ID_PREFIXES = {
  agency: 'agcy',
  user: 'user',
  client: 'clnt',
  scan: 'scan',
  competitorSet: 'cset',
  competitor: 'comp',
  promptSet: 'pset',
  prompt: 'prmt',
  engineResult: 'eres',
  citation: 'cite',
  brandMention: 'bmen',
  technicalAudit: 'taud',
  auditCheck: 'tchk',
  score: 'scor',
  actionItem: 'acti',
  invitation: 'invt',
} as const;

export type IdPrefix = (typeof ID_PREFIXES)[keyof typeof ID_PREFIXES];

/** Narrow a string to an id of a known entity type. */
export function isIdOf(value: string, prefix: IdPrefix): boolean {
  return value.startsWith(`${prefix}_`) && value.length === prefix.length + 27;
}
