/**
 * Engine identity — one name and one hue per engine, product-wide.
 *
 * WHY THIS IS SHARED RATHER THAN DUPLICATED
 * ------------------------------------------
 * Epic 9.24 put these two maps privately inside `ClientPromptsView`, following
 * the rule `ClientsView`'s `Stat` helper set: two call sites is a coincidence,
 * move it on the third. Epic A is a different case and the rule does not apply
 * — these are not a shape that happens to repeat, they are an IDENTITY. If the
 * Prompts screen and the Sentiment screen disagreed about which hue is ChatGPT,
 * an operator would have to re-learn the mapping every time they changed tab,
 * which is worse than any duplication cost. Two copies of an identity is one
 * identity waiting to drift.
 */

/**
 * A stable `bench-*` accent index per engine.
 *
 * Fixed by engine KEY, never by array position, for the reason
 * `WorkspaceShell`'s and `ClientSpace`'s accent records are: an engine added to
 * the registry above another must not repaint the one below it.
 *
 * An unknown engine falls through to a caller-supplied default rather than
 * borrowing a hue that already means something else.
 */
export const ENGINE_ACCENT: Readonly<Record<string, number>> = {
  claude: 0,
  claude_search: 2,
  chatgpt: 4,
  // Epic 21. The gaps between the first three were left for exactly this.
  perplexity: 1,
  gemini: 3,
};

/**
 * Display names.
 *
 * `claude` and `claude_search` are ONE vendor in two modes and the labels say
 * so, because the difference between them is the finding: a brand can be absent
 * from grounded answers while present in parametric ones, and two rows both
 * called "Claude" would hide exactly that.
 */
export const ENGINE_LABEL: Readonly<Record<string, string>> = {
  claude: 'Claude — from memory',
  claude_search: 'Claude — with web search',
  chatgpt: 'ChatGPT — from memory',
  // Epic 21. Perplexity is grounded by nature and says so the way the
  // grounded Claude does; Gemini is run from memory, as ChatGPT is, so the
  // three parametric vendors sit on one footing.
  perplexity: 'Perplexity — with web search',
  gemini: 'Gemini — from memory',
};

/**
 * A shorter form, for a chart axis or a dense table where the full label wraps.
 *
 * Still distinguishes the two Claude modes, because that distinction is the
 * whole reason both engines exist.
 */
export const ENGINE_SHORT: Readonly<Record<string, string>> = {
  claude: 'Claude',
  claude_search: 'Claude + search',
  chatgpt: 'ChatGPT',
  perplexity: 'Perplexity',
  gemini: 'Gemini',
};

/** What three of the provider's refusals share: a problem on our side, and no more than that. */
const OUR_SIDE_SENTENCE = 'We could not get an answer from its provider because of a problem on our side.';

/**
 * Why an engine did not answer, in a sentence that follows "This engine did not answer."
 * — launch checklist 3.39, in place of the code the Prompts screen used to print.
 *
 * The keys are the codes an engine result can carry: `services/engines.py`'s error
 * mapping for the four vendors (`PROVIDER_*` and `TIMEOUT`), and its stop-reason
 * vocabulary (`ANSWER_TRUNCATED`, `ANSWER_PAUSED`, `STOP_REASON_UNKNOWN`). A test reads
 * that file back, so a code added there without wording here fails.
 *
 * Each sentence is true of every case that records its code, for every vendor, and
 * blames nobody: not the person who asked, and not the brand. Where the cause is on our
 * side it says so. "Its provider" is the company that runs the engine.
 *
 * `PROVIDER_AUTH_FAILED`, `PROVIDER_QUOTA_EXHAUSTED` and `PROVIDER_BAD_REQUEST` share one
 * sentence: each is a problem on our side (a key the provider refused, an account with no
 * credit or quota, a request it rejected), and which of the three it was is not something
 * the person reading can act on. They keep an entry each so a test can read the API's
 * codes back against them.
 */
export const ENGINE_FAILURE_SENTENCE: Readonly<Record<string, string>> = {
  TIMEOUT: 'It did not respond in the time we allow.',
  PROVIDER_RATE_LIMITED: 'Its provider asked us to slow down.',
  PROVIDER_AUTH_FAILED: OUR_SIDE_SENTENCE,
  PROVIDER_QUOTA_EXHAUSTED: OUR_SIDE_SENTENCE,
  PROVIDER_BAD_REQUEST: OUR_SIDE_SENTENCE,
  PROVIDER_UNREACHABLE: 'We could not reach its provider.',
  PROVIDER_ERROR: 'The request to its provider failed.',
  PROVIDER_REFUSED: 'It declined to answer this question.',
  ANSWER_TRUNCATED: 'Its answer was cut off before it finished.',
  ANSWER_PAUSED: 'It had not finished its answer when the response came back.',
  STOP_REASON_UNKNOWN: 'It stopped for a reason we do not recognise.',
};

/** The sentence for an engine result's `errorCode`, or null: no code, or one nothing words. Never the code. */
export function engineFailureSentence(code: string | null | undefined): string | null {
  return code && Object.hasOwn(ENGINE_FAILURE_SENTENCE, code) ? ENGINE_FAILURE_SENTENCE[code]! : null;
}

export function engineLabel(engine: string): string {
  return ENGINE_LABEL[engine] ?? engine;
}

export function engineShort(engine: string): string {
  return ENGINE_SHORT[engine] ?? engine;
}
