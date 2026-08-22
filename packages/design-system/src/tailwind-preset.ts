/**
 * Tailwind preset.
 *
 * product-spec.md §5.1 requires Tailwind "customized to proprietary design
 * tokens", and ip-safety.md #2 forbids ad hoc Tailwind defaults on
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
    },

    borderRadius: {
      none: '0',
      sm: v('radius-sm'),
      md: v('radius-md'),
      lg: v('radius-lg'),
      xl: v('radius-xl'),
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
        caps: v('tracking-caps'),
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
