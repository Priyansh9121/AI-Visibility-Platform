/**
 * Tailwind preset.
 *
 * product-spec.md §5.1 requires Tailwind "customized to proprietary design
 * tokens", and the design-system-only rule forbids ad hoc Tailwind defaults on
 * customer-facing screens. This preset is how both are enforced mechanically
 * rather than by reviewer vigilance.
 *
 * Note `theme` (replace), not `theme.extend` (merge), for colour, spacing,
 * font family/size, shadow and radius: Tailwind's stock palette is REMOVED, so
 * `bg-slate-500` or `text-blue-600` simply does not compile. Off-system colour
 * cannot ship by accident, because the class does not exist.
 *
 * Usage in apps/web:
 *   import preset from '@avp/design-system/tailwind-preset'
 *   export default { presets: [preset], content: [...] }
 */

const v = (name: string) => `var(--avp-${name})`;

const tailwindPreset = {
  theme: {
    colors: {
      transparent: 'transparent',
      current: 'currentColor',
      inherit: 'inherit',

      paper: {
        '000': v('paper-000'),
        '050': v('paper-050'),
        100: v('paper-100'),
        200: v('paper-200'),
        300: v('paper-300'),
      },
      ink: {
        400: v('ink-400'),
        600: v('ink-600'),
        800: v('ink-800'),
        900: v('ink-900'),
      },
      /* Visibility ramp. FILL ONLY — see tokens/color.ts. Exposed for bg-* and
         fill-*; using it as text-* is a system violation caught in review. */
      vis: {
        '00': v('vis-00'),
        25: v('vis-25'),
        50: v('vis-50'),
        75: v('vis-75'),
        100: v('vis-100'),
      },
      beacon: {
        '050': v('beacon-050'),
        100: v('beacon-100'),
        400: v('beacon-400'),
        600: v('beacon-600'),
        700: v('beacon-700'),
      },
      /* The second accent — beacon's gradient partner, Epic 14. Never a value,
         a category or a state on its own; see tokens/color.ts. */
      signal: {
        '050': v('signal-050'),
        100: v('signal-100'),
        400: v('signal-400'),
        600: v('signal-600'),
        700: v('signal-700'),
      },
      /* The Working-screen accent layer. Deliberately a SEPARATE scale from
         `beacon` and `vis`: a screen can be colourful without the report's
         token set gaining a single new value. Report surfaces never use these
         — enforced by tokens.test.ts, not by convention. */
      bench: {
        '1': { '050': v('bench-1-050'), 100: v('bench-1-100'), 600: v('bench-1-600'), 700: v('bench-1-700') },
        '2': { '050': v('bench-2-050'), 100: v('bench-2-100'), 600: v('bench-2-600'), 700: v('bench-2-700') },
        '3': { '050': v('bench-3-050'), 100: v('bench-3-100'), 600: v('bench-3-600'), 700: v('bench-3-700') },
        '4': { '050': v('bench-4-050'), 100: v('bench-4-100'), 600: v('bench-4-600'), 700: v('bench-4-700') },
        '5': { '050': v('bench-5-050'), 100: v('bench-5-100'), 600: v('bench-5-600'), 700: v('bench-5-700') },
        '6': { '050': v('bench-6-050'), 100: v('bench-6-100'), 600: v('bench-6-600'), 700: v('bench-6-700') },
        '7': { '050': v('bench-7-050'), 100: v('bench-7-100'), 600: v('bench-7-600'), 700: v('bench-7-700') },
      },
      competitor: {
        1: v('competitor-1'),
        2: v('competitor-2'),
        3: v('competitor-3'),
        4: v('competitor-4'),
        5: v('competitor-5'),
      },
      success: v('success'),
      warn: v('warn'),
      danger: v('danger'),
      info: v('info'),

      /* Role tokens — prefer these over raw ramps in app code, since they are
         the ones that respond to dark mode. */
      surface: {
        ground: v('surface-ground'),
        sunken: v('surface-sunken'),
        seated: v('surface-seated'),
        raised: v('surface-raised'),
        void: v('surface-void'),
        hover: v('surface-hover'),
        desk: v('surface-desk'),
      },
      text: {
        primary: v('text-primary'),
        body: v('text-body'),
        secondary: v('text-secondary'),
        tertiary: v('text-tertiary'),
      },
      line: {
        hairline: v('line-hairline'),
        strong: v('line-strong'),
        ink: v('line-ink'),
      },
      /* Text ON an accent or a semantic fill — resolves per theme. */
      on: {
        accent: v('on-accent'),
        danger: v('on-danger'),
        warn: v('on-warn'),
      },
    },

    spacing: {
      0: v('space-0'),
      px: v('space-px'),
      0.5: v('space-0_5'),
      1: v('space-1'),
      1.5: v('space-1_5'),
      2: v('space-2'),
      3: v('space-3'),
      4: v('space-4'),
      5: v('space-5'),
      6: v('space-6'),
      8: v('space-8'),
      10: v('space-10'),
      14: v('space-14'),
      18: v('space-18'),
      24: v('space-24'),
      32: v('space-32'),
      rhythm: v('rhythm'),
      beat: v('beat'),
    },

    fontFamily: {
      display: v('font-display'),
      editorial: v('font-editorial'),
      ui: v('font-ui'),
      mono: v('font-mono'),
    },

    /* Both tracks namespaced so `text-ui-base` and `text-ed-md` can never be
       confused at the call site. */
    fontSize: {
      'ui-2xs': [v('text-ui-2xs'), { lineHeight: v('leading-ui') }],
      'ui-xs': [v('text-ui-xs'), { lineHeight: v('leading-ui') }],
      'ui-sm': [v('text-ui-sm'), { lineHeight: v('leading-ui') }],
      'ui-base': [v('text-ui-base'), { lineHeight: v('leading-ui') }],
      'ui-md': [v('text-ui-md'), { lineHeight: v('leading-ui') }],
      'ui-lg': [v('text-ui-lg'), { lineHeight: v('leading-ui') }],
      'ui-xl': [v('text-ui-xl'), { lineHeight: v('leading-ui') }],
      'ed-2xs': [v('text-ed-2xs'), { lineHeight: v('leading-display') }],
      'ed-xs': [v('text-ed-xs'), { lineHeight: v('leading-display') }],
      'ed-sm': [v('text-ed-sm'), { lineHeight: v('leading-display') }],
      'ed-md': [v('text-ed-md'), { lineHeight: v('leading-display') }],
      'ed-lg': [v('text-ed-lg'), { lineHeight: v('leading-display') }],
      'ed-xl': [v('text-ed-xl'), { lineHeight: v('leading-display') }],
      'ed-2xl': [v('text-ed-2xl'), { lineHeight: v('leading-display') }],
      score: [v('text-score'), { lineHeight: '0.9', letterSpacing: v('tracking-display') }],
      /* The dark identity's two display figures — Epic 14. */
      kpi: [v('text-kpi'), { lineHeight: '1', letterSpacing: v('tracking-display') }],
      hero: [v('text-hero'), { lineHeight: '0.95', letterSpacing: v('tracking-hero') }],
    },

    borderRadius: {
      none: '0',
      sm: v('radius-sm'),
      md: v('radius-md'),
      lg: v('radius-lg'),
      xl: v('radius-xl'),
      '2xl': v('radius-2xl'),
      full: v('radius-full'),
    },

    boxShadow: {
      none: 'none',
      flat: v('elev-flat'),
      seated: v('elev-seated'),
      raised: v('elev-raised'),
      lifted: v('elev-lifted'),
      overlay: v('elev-overlay'),
      focus: v('focus-ring'),
      selected: v('selected-ring'),
    },

    extend: {
      maxWidth: {
        measure: v('measure'),
        report: v('report-width'),
        app: v('app-max'),
        form: v('form-width'),
        headline: v('headline'),
      },
      transitionDuration: {
        hover: v('duration-hover'),
        state: v('duration-state'),
        layout: v('duration-layout'),
        reveal: v('duration-reveal'),
      },
      transitionTimingFunction: {
        standard: v('ease-standard'),
        out: v('ease-out'),
        reveal: v('ease-reveal'),
      },
      letterSpacing: {
        display: v('tracking-display'),
        hero: v('tracking-hero'),
        caps: v('tracking-caps'),
      },
      backgroundImage: {
        accent: v('gradient-accent'),
      },
      /* The leading tokens existed in tokens.css from Epic 0 but were never
         exposed as utilities, so `leading-prose` silently compiled to nothing.
         Added in Epic 7 when the report needed measured prose; tokens/tokens.test.ts
         now asserts the preset and the stylesheet agree. */
      lineHeight: {
        display: v('leading-display'),
        ui: v('leading-ui'),
        prose: v('leading-prose'),
        mono: v('leading-mono'),
      },
    },
  },
} as const;

export default tailwindPreset;
