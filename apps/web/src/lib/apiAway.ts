import { ApiProblem } from '@/lib/api';

/**
 * Whether a failed call says the API was AWAY, as opposed to the API saying no.
 *
 * Two shapes. No answer at all is a network error, which is not an `ApiProblem`.
 * And an answer that is not the API's own: a proxy's non-JSON 502 reaches
 * `request()` as an `ApiProblem` carrying that status, and one can arrive with
 * no status at all. A 5xx is the server failing rather than refusing. None of
 * them tells the person anything about what they asked for, so a screen says it
 * could not reach the server, or that it does not know how the request ended,
 * instead of repeating a status line. A 4xx is the API's own refusal and carries
 * its own sentence.
 *
 * One rule, named once: `/sign-up/google` decides between "expired" and
 * "unreachable" with it, and so do the form that completes that sign-up and the
 * share bar's withdraw.
 */
export function apiWasAway(err: unknown): boolean {
  return !(err instanceof ApiProblem) || !err.status || err.status >= 500;
}
