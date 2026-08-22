import type { JSX } from 'react';
import { competitor, oklch } from '../../tokens/color.js';

/**
 * SVG pattern defs for competitor series.
 *
 * Competitors are separated by lightness AND fill pattern rather than hue, so
 * that a printed or photocopied report keeps every series distinguishable.
 * Colour alone fails in B&W; pattern alone is noisy; together they are robust.
 *
 * Render ONCE per SVG document, then reference as fill="url(#avp-hatch-45)".
 */
export function ChartPatterns(): JSX.Element {
  const line = oklch(competitor['1']);
  return (
    <defs>
      <pattern id="avp-hatch-45" width={6} height={6} patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
        <rect width={6} height={6} fill="var(--avp-surface-ground)" />
        <line x1={0} y1={0} x2={0} y2={6} stroke={line} strokeWidth={2} />
      </pattern>
      <pattern id="avp-hatch-135" width={6} height={6} patternUnits="userSpaceOnUse" patternTransform="rotate(135)">
        <rect width={6} height={6} fill="var(--avp-surface-ground)" />
        <line x1={0} y1={0} x2={0} y2={6} stroke={line} strokeWidth={2} />
      </pattern>
      <pattern id="avp-dot" width={5} height={5} patternUnits="userSpaceOnUse">
        <rect width={5} height={5} fill="var(--avp-surface-ground)" />
        <circle cx={2.5} cy={2.5} r={1.2} fill={line} />
      </pattern>
    </defs>
  );
}

/** Resolve a pattern name to an SVG paint value. */
export function patternPaint(pattern: string, fallbackFill: string): string {
  switch (pattern) {
    case 'hatch-45':
      return 'url(#avp-hatch-45)';
    case 'hatch-135':
      return 'url(#avp-hatch-135)';
    case 'dot':
      return 'url(#avp-dot)';
    case 'outline':
      return 'transparent';
    default:
      return fallbackFill;
  }
}
