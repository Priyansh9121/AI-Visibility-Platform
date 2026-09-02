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
};

export function engineLabel(engine: string): string {
  return ENGINE_LABEL[engine] ?? engine;
}

export function engineShort(engine: string): string {
  return ENGINE_SHORT[engine] ?? engine;
}
