/**
 * Prompt-run fixtures — Epic 9.24.
 *
 * Shaped like the real endpoint's responses, including the states that only
 * happen when something has gone partly wrong: an engine that answered without
 * naming the client, and an engine that did not answer at all. Those two are
 * the ones worth having fixtures for, because they are the ones a screen
 * quietly gets wrong.
 */

import type { PromptRun, PromptRunHistory, PromptRunResult } from '@avp/shared-types';

const CITATION = {
  url: 'https://helpscout.com/pricing',
  domain: 'helpscout.com',
  sourceType: 'owned',
  position: 1,
  citesSubject: true,
} as PromptRunResult['citations'][number];

const named: PromptRunResult = {
  id: 'prre_01AAA',
  engine: 'claude',
  engineVersion: 'claude-opus-5',
  status: 'ok',
  errorCode: null,
  latencyMs: 2100,
  mentioned: true,
  position: 1,
  prominence: '0.910',
  brandsMentioned: 3,
  brands: [
    { name: 'Help Scout', domain: 'helpscout.com', isSubject: true, position: 1 },
    { name: 'Front', domain: 'frontapp.com', isSubject: false, position: 2 },
    { name: 'Zendesk', domain: 'zendesk.com', isSubject: false, position: 3 },
  ],
  citations: [CITATION],
} as PromptRunResult;

/** Answered, but did not name the client. The finding, not a failure. */
const notNamed: PromptRunResult = {
  id: 'prre_01BBB',
  engine: 'claude_search',
  engineVersion: 'claude-opus-5-search',
  status: 'answered_no_mention',
  errorCode: null,
  latencyMs: 18400,
  mentioned: false,
  position: null,
  prominence: null,
  brandsMentioned: 2,
  brands: [
    { name: 'Front', domain: 'frontapp.com', isSubject: false, position: 1 },
    { name: 'Zendesk', domain: 'zendesk.com', isSubject: false, position: 2 },
  ],
  citations: [],
} as PromptRunResult;

/** Genuinely did not answer. A different fact from the one above. */
const errored: PromptRunResult = {
  id: 'prre_01CCC',
  engine: 'chatgpt',
  engineVersion: null,
  status: 'timeout',
  errorCode: 'ENGINE_TIMEOUT',
  latencyMs: null,
  mentioned: false,
  position: null,
  prominence: null,
  brandsMentioned: 0,
  brands: [],
  citations: [],
} as PromptRunResult;

export const mixedRun: PromptRun = {
  id: 'prun_01AAA',
  clientId: 'clnt_01AAA',
  promptText: 'best help desk software for small teams',
  status: 'partial',
  subjectName: 'Help Scout',
  subjectDomain: 'helpscout.com',
  createdAt: '2026-09-01T09:15:00Z',
  results: [named, notNamed, errored],
} as PromptRun;

/** Nobody named the client anywhere. The screen must not read as broken. */
export const absentRun: PromptRun = {
  id: 'prun_01BBB',
  clientId: 'clnt_01AAA',
  promptText: 'who should I use for customer support',
  status: 'ok',
  subjectName: 'Help Scout',
  subjectDomain: 'helpscout.com',
  createdAt: '2026-09-01T08:00:00Z',
  results: [{ ...notNamed, id: 'prre_01DDD', engine: 'claude' }, notNamed],
} as PromptRun;

export const LIMITS = {
  runsRemaining: 28,
  runsPerHour: 30,
  maxPromptChars: 500,
} as const;

export const EXHAUSTED = {
  runsRemaining: 0,
  runsPerHour: 30,
  maxPromptChars: 500,
} as const;

export const promptHistory: PromptRunHistory = {
  data: [mixedRun, absentRun],
  ...LIMITS,
} as PromptRunHistory;
