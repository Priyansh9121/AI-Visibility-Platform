/**
 * Elevation tokens — "paper doesn't float".
 *
 * The category default is a big soft blurred drop shadow: material floating
 * over a canvas. Two problems. It reads as generic app chrome, and — the
 * decisive one — SHADOWS DISAPPEAR WHEN THE REPORT IS PRINTED, taking the
 * entire visual hierarchy with them. This product's main artifact is a PDF put
 * in front of a client.
 *
 * So elevation here is built from borders and tight offsets, like stacked card
 * stock. Levels 0-2 print correctly. Levels 3-4 are transient UI (popovers,
 * modals) that never appears in an export, so they may use blur.
 */

export const elevation = {
  /** On-ground. Separation by hairline only. */
  flat: 'none',
  /** Seated surfaces. NO shadow at all — separation is by tone shift. */
  seated: 'none',
  /** Printed-card edge: hard 1px offset, zero blur. Survives print. */
  raised: '0 1px 0 0 oklch(0.185 0.026 265 / 0.10)',
  /** Popovers and menus. Transient — blur permitted. */
  lifted:
    '0 2px 4px -1px oklch(0.185 0.026 265 / 0.10), 0 6px 12px -4px oklch(0.185 0.026 265 / 0.12)',
  /** Modals. Transient — blur permitted. */
  overlay:
    '0 4px 8px -2px oklch(0.185 0.026 265 / 0.14), 0 12px 28px -8px oklch(0.185 0.026 265 / 0.18)',
} as const;

/** Hairlines carry the hierarchy that shadows are not allowed to carry. */
export const border = {
  hairline: '1px solid oklch(0.925 0.010 75)',
  strong: '1px solid oklch(0.870 0.012 75)',
  ink: '1px solid oklch(0.285 0.022 265)',
} as const;

export const scrim = 'oklch(0.185 0.026 265 / 0.40)';

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
  focusRing:
    '0 0 0 2px oklch(0.72 0.11 200 / 0.60), 0 0 0 6px oklch(0.72 0.11 200 / 0.12)',
  selectedRing: 'inset 0 0 0 1px oklch(0.545 0.125 200)',
  selectedWash: 'oklch(0.545 0.125 200 / 0.04)',
} as const;
