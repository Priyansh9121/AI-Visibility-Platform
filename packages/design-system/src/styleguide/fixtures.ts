/**
 * Style-guide fixtures.
 *
 * INVENTED DATA about an INVENTED COMPANY. No real brand, no real competitor,
 * and no scraped text appears anywhere in this file (docs/ip-safety.md #7, #8).
 * Prompts are original strings written for this guide.
 */

import type { LedgerDimension, LedgerCompetitor } from '../components/chart/ledgerLayout.js';
import type { ShelfRowInput } from '../components/chart/answerShelfLayout.js';

export const SUBJECT = 'Northaven Dental';

/** Real §6 weights, fictional sub-scores. */
export const DIMENSIONS: LedgerDimension[] = [
  { key: 'mention_rate', label: 'Mention Rate', weight: 30, subscore: 41 },
  { key: 'share_of_voice', label: 'Share of Voice', weight: 25, subscore: 22 },
  { key: 'citation_strength', label: 'Citation Strength', weight: 20, subscore: 15 },
  { key: 'sentiment', label: 'Sentiment', weight: 15, subscore: 78 },
  { key: 'technical', label: 'Technical Foundation', weight: 10, subscore: 64 },
];

export const COMPETITORS: LedgerCompetitor[] = [
  {
    name: 'Competitor A',
    dimensions: [
      { key: 'mention_rate', label: 'Mention Rate', weight: 30, subscore: 88 },
      { key: 'share_of_voice', label: 'Share of Voice', weight: 25, subscore: 71 },
      { key: 'citation_strength', label: 'Citation Strength', weight: 20, subscore: 65 },
      { key: 'sentiment', label: 'Sentiment', weight: 15, subscore: 74 },
      { key: 'technical', label: 'Technical Foundation', weight: 10, subscore: 80 },
    ],
  },
  {
    name: 'Competitor B',
    dimensions: [
      { key: 'mention_rate', label: 'Mention Rate', weight: 30, subscore: 62 },
      { key: 'share_of_voice', label: 'Share of Voice', weight: 25, subscore: 48 },
      { key: 'citation_strength', label: 'Citation Strength', weight: 20, subscore: 55 },
      { key: 'sentiment', label: 'Sentiment', weight: 15, subscore: 60 },
      { key: 'technical', label: 'Technical Foundation', weight: 10, subscore: 45 },
    ],
  },
  {
    name: 'Competitor C',
    dimensions: [
      { key: 'mention_rate', label: 'Mention Rate', weight: 30, subscore: 35 },
      { key: 'share_of_voice', label: 'Share of Voice', weight: 25, subscore: 30 },
      { key: 'citation_strength', label: 'Citation Strength', weight: 20, subscore: 22 },
      { key: 'sentiment', label: 'Sentiment', weight: 15, subscore: 55 },
      { key: 'technical', label: 'Technical Foundation', weight: 10, subscore: 38 },
    ],
  },
];

export interface CompetitorRow {
  name: string;
  score: number;
  mentionRate: number;
  citations: number;
  isSubject: boolean;
}

export const COMPARISON_ROWS: CompetitorRow[] = [
  { name: SUBJECT, score: 38, mentionRate: 41, citations: 3, isSubject: true },
  { name: 'Competitor A', score: 75, mentionRate: 88, citations: 24, isSubject: false },
  { name: 'Competitor B', score: 54, mentionRate: 62, citations: 11, isSubject: false },
  { name: 'Competitor C', score: 34, mentionRate: 35, citations: 4, isSubject: false },
];

/** Prompts written for this guide — our own text, not captured from anywhere. */
export const EVIDENCE = [
  {
    engine: 'Engine 1',
    prompt: 'best family dentist in the northaven area',
    findings: [
      { label: 'Brand mentioned', value: 'No' },
      { label: 'Brands named', value: '4' },
      { label: 'Sources cited', value: '3 domains' },
    ],
  },
  {
    engine: 'Engine 2',
    prompt: 'who does same-day crowns near me',
    findings: [
      { label: 'Brand mentioned', value: 'Yes' },
      { label: 'Position', value: '4th of 5' },
      { label: 'Sentiment', value: 'Positive' },
    ],
  },
];

export const FIXES = [
  {
    id: 'f1',
    title: 'Publish a structured service page for same-day crowns',
    detail:
      'The prompt set shows this service being answered by directory pages rather than by practice sites. A dedicated page with Service schema is the shortest path onto the answer.',
    priority: 'high' as const,
    effort: 'M' as const,
    pointsUpside: 8.4,
  },
  {
    id: 'f2',
    title: 'Claim and populate the three review profiles cited most often',
    detail:
      'Three domains account for the majority of citations across the tracked prompts, and none of them currently carry a complete profile for this practice.',
    priority: 'high' as const,
    effort: 'S' as const,
    pointsUpside: 6.1,
  },
  {
    id: 'f3',
    title: 'Add LocalBusiness and Dentist schema to the location pages',
    detail: 'Structured data is absent sitewide, which caps the Technical Foundation sub-score.',
    priority: 'medium' as const,
    effort: 'S' as const,
    pointsUpside: 3.6,
  },
];

/**
 * Answer Shelf rows — Epic 7.1.
 *
 * Same rule as everything else in this file: invented practice, invented
 * rivals, prompts written for this guide. The shape is deliberately the one
 * the real data produces — the subject absent from most answers, present low
 * down in a couple, and one answer that never came back.
 */
export const SHELF_ROWS: ShelfRowInput[] = [
  {
    promptId: 'q1', promptText: 'best family dentist in Northaven', promptPosition: 1,
    engine: 'claude', answered: true, subjectPresent: true, subjectPosition: 3, subjectCited: true,
    slots: [
      { position: 1, entityName: 'Brightwater Dental', isSubject: false, cited: true },
      { position: 2, entityName: 'Cedar Lane Orthodontics', isSubject: false, cited: false },
      { position: 3, entityName: SUBJECT, isSubject: true, cited: true },
    ],
  },
  {
    promptId: 'q2', promptText: 'who does same-day crowns near me', promptPosition: 2,
    engine: 'claude', answered: true, subjectPresent: false, subjectPosition: null,
    slots: [
      { position: 1, entityName: 'Brightwater Dental', isSubject: false, cited: true },
      { position: 2, entityName: 'Harbourview Smile Co', isSubject: false, cited: false },
    ],
  },
  {
    promptId: 'q3', promptText: 'affordable dental implants comparison', promptPosition: 3,
    engine: 'claude', answered: true, subjectPresent: false, subjectPosition: null,
    slots: [
      { position: 1, entityName: 'Cedar Lane Orthodontics', isSubject: false, cited: false },
      { position: 2, entityName: 'Brightwater Dental', isSubject: false, cited: true },
      { position: 3, entityName: 'Harbourview Smile Co', isSubject: false, cited: false },
    ],
  },
  {
    promptId: 'q4', promptText: 'dentists open on Saturday', promptPosition: 4,
    engine: 'claude', answered: true, subjectPresent: false, subjectPosition: null,
    slots: [{ position: 1, entityName: 'Harbourview Smile Co', isSubject: false, cited: false }],
  },
  {
    promptId: 'q5', promptText: 'invisalign providers with payment plans', promptPosition: 5,
    engine: 'claude', answered: true, subjectPresent: true, subjectPosition: 2, subjectCited: false,
    slots: [
      { position: 1, entityName: 'Cedar Lane Orthodontics', isSubject: false, cited: true },
      { position: 2, entityName: SUBJECT, isSubject: true, cited: false },
    ],
  },
  {
    promptId: 'q6', promptText: 'emergency dental care after hours', promptPosition: 6,
    engine: 'claude', answered: false, subjectPresent: false, subjectPosition: null, slots: [],
  },
];
