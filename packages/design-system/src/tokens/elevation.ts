/**
 * Elevation tokens — Epic 14: a card sits in the dark, lit along its top edge.
 *
 * On the dark identity a raised surface is a plate with light on it: a 1px
 * inner highlight along the top, a shadow tinted to the ground rather than
 * black at low alpha, and a lighter surface tone. The three together are what
 * make a card read as elevation rather than as a lighter rectangle.
 *
 * `paper` is Epic 0's "paper doesn't float" set, unchanged: borders and hard
 * offsets, because blurred shadows vanish when the report is printed and the
 * report is the artefact this product sells. It is scoped to `.avp-report` in
 * tokens.css, so the document keeps it without a route having to ask.
 */

export const elevation = {
  /** On-ground. Separation by hairline only. */
  flat: 'none',
  /** A seated surface — the inner top highlight and nothing else. */
  seated: 'inset 0 1px 0 0 oklch(1 0 0 / 0.05)',
  /** A card. Highlight, contact shadow, and a soft ground-tinted drop. */
  raised:
    'inset 0 1px 0 0 oklch(1 0 0 / 0.07), 0 1px 2px 0 oklch(0.08 0.01 265 / 0.5), 0 16px 32px -20px oklch(0.08 0.01 265 / 0.8)',
  /** Popovers and menus. */
  lifted:
    'inset 0 1px 0 0 oklch(1 0 0 / 0.08), 0 4px 10px -2px oklch(0.08 0.01 265 / 0.6), 0 16px 40px -12px oklch(0.08 0.01 265 / 0.8)',
  /** Modals. */
  overlay: '0 8px 16px -4px oklch(0.08 0.01 265 / 0.6), 0 32px 64px -16px oklch(0.08 0.01 265 / 0.85)',
} as const;

/** The report's elevation — Epic 0's, verbatim. */
export const paperElevation = {
  flat: 'none',
  seated: 'none',
  raised: '0 1px 0 0 oklch(0.185 0.026 265 / 0.10)',
  lifted:
    '0 2px 4px -1px oklch(0.185 0.026 265 / 0.10), 0 6px 12px -4px oklch(0.185 0.026 265 / 0.12)',
  overlay:
    '0 4px 8px -2px oklch(0.185 0.026 265 / 0.14), 0 12px 28px -8px oklch(0.185 0.026 265 / 0.18)',
} as const;

/** Hairlines carry the hierarchy that shadows are not allowed to carry. */
export const border = {
  hairline: '1px solid oklch(0.275 0.014 265)',
  strong: '1px solid oklch(0.35 0.016 265)',
  ink: '1px solid oklch(0.72 0.01 75)',
} as const;

export const scrim = 'oklch(0.08 0.01 265 / 0.7)';

/**
 * THE OWNABLE PART: emphasis is light, not lift.
 *
 * Because the whole system says visibility = luminance, an emphasised element
 * does not rise toward the viewer — it ILLUMINATES. Focus is a beacon ring plus
 * halo; selection reads as lit from within. This is the one interaction rule
 * that makes the system feel authored rather than assembled, so it is a token,
 * not a per-component decision.
 */
export const emphasis = {
  focusRing: '0 0 0 2px oklch(0.8 0.13 200 / 0.7), 0 0 0 6px oklch(0.8 0.13 200 / 0.15)',
  selectedRing: 'inset 0 0 0 1px oklch(0.8 0.13 200)',
  selectedWash: 'oklch(0.8 0.13 200 / 0.08)',
} as const;
