/**
 * Minimal class-name joiner.
 *
 * Deliberately not a dependency: clsx/classnames are fine libraries, but this
 * is nine lines and every dependency added has to clear the ip-safety.md #6
 * licence gate and be recorded in the build log. Not worth it for this.
 */
export type ClassValue = string | number | false | null | undefined;

export function cn(...values: ClassValue[]): string {
  return values.filter((v): v is string | number => v !== false && v != null && v !== '').join(' ');
}
