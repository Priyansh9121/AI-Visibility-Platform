/**
 * A client's navigation, as data — Epic B.1.
 *
 * WHY THIS IS A TABLE RATHER THAN JSX WITH ACCENT NUMBERS TYPED INTO IT
 * ----------------------------------------------------------------------
 * Epic B filled the seventh and last seat in the `bench-*` accent layer, and a
 * client's space is heading for ten sections (Alerts, Crawler activity and
 * Prompt discovery are still to come). The layer cannot grow: the 30-degree
 * meaning buffer and the sRGB gamut at the shared chroma table leave one usable
 * arc, 256.5 to 355 degrees, and seven accents fill it. See `BENCH_ACCENTS`.
 *
 * So the nav grew CLUSTERS instead, and an accent is now scoped to its cluster:
 * **each cluster restarts at accent 0**, and a hue only has to be told apart
 * from the others in its own group. Neither cluster below reaches seven even at
 * the roadmap's end state, so the ceiling stops being the binding constraint.
 *
 * Holding it as data rather than as markup is what makes that checkable.
 * `clientNav.test.ts` asserts no cluster repeats an accent and no cluster
 * outgrows the layer — assertions that cannot be written against ten
 * hand-typed `accent={...}` props scattered through JSX, which is exactly how
 * Epic B's crimson/magenta collision reached a live browser.
 *
 * ACCENTS ARE STILL WRITTEN OUT, NOT DERIVED FROM POSITION
 * ---------------------------------------------------------
 * Epic 9.24's rule holds and is the reason `accent` is a field rather than an
 * array index: these are identities, not positions. Inserting Alerts above
 * Prompts must not repaint Prompts. Deriving the accent from the item's
 * position in its cluster would do exactly that, quietly, in a diff about
 * something else.
 */

export type ClientSection =
  | 'overview'
  | 'sources'
  | 'rankings'
  | 'sentiment'
  | 'technical'
  | 'gaps'
  | 'prompts'
  | 'alerts'
  | 'crawler';

export interface ClientNavItem {
  /** `null` for the Report, which is a link out rather than a section. */
  section: ClientSection | null;
  label: string;
  /** Path under the client's base, or `null` for the Report's own resolver. */
  path: string | null;
  /**
   * Cluster-relative index into `BENCH_ACCENTS`. `null` is deliberate and means
   * unaccented — the Report is the one item that leaves for a Presenting
   * document, and giving it a Working hue would imply it belongs to the same
   * set as the sections that stay.
   */
  accent: number | null;
  external?: boolean;
}

export interface ClientNavCluster {
  key: string;
  label: string;
  items: readonly ClientNavItem[];
}

/**
 * The two clusters, and why each section sits where it does.
 *
 * **Measurement — what the scans recorded.** Every item here reads rows a scan
 * already wrote. Sentiment belongs here rather than with the analyses: its
 * labels have been stored since Epic 4 and it is one of the five scored
 * dimensions at 15% of the composite, which makes it a measurement, not a
 * derivation. AI crawlers joined it in Epic F for the same reason — it reads a
 * second first-party SOURCE (the site's own robots.txt), not an analysis of
 * the first. B.1 predicted that seat would hold "first-party server logs";
 * Epic F found this product ingests none, and the section reads the policy
 * those logs would have shown compliance with instead.
 *
 * **Investigation — what an operator does with it.** Answer gaps derives over
 * measured rows; Prompts creates new ones. Grouping "derive" and "probe"
 * together rather than splitting them into a third cluster is a judgement about
 * an operator's intent — both are the active mode, where Measurement is the
 * reading mode — and it keeps a ten-item strip to two labels rather than three.
 *
 * The Report leads, ungrouped in effect: it sits in Measurement because it IS
 * the measurement written up, and it is unaccented and marked external so the
 * one item that leaves this space is never mistaken for a section inside it.
 */
export const CLIENT_NAV: readonly ClientNavCluster[] = [
  {
    key: 'measurement',
    label: 'Measurement',
    items: [
      { section: 'overview', label: 'Overview', path: '', accent: 0 },
      { section: null, label: 'Report', path: null, accent: null, external: true },
      { section: 'sources', label: 'Sources', path: '/sources', accent: 1 },
      { section: 'rankings', label: 'Rankings', path: '/rankings', accent: 2 },
      { section: 'sentiment', label: 'Sentiment', path: '/sentiment', accent: 3 },
      { section: 'technical', label: 'Technical', path: '/technical', accent: 4 },
      // Epic F. The last free seat in this cluster, reserved by B.1 and
      // asserted by `clientNav.test.ts` before it existed.
      //
      // "AI crawlers", not "Crawler activity", and the difference is the
      // epic. The roadmap's name promised server-log activity — bots actually
      // hitting the site — and nothing in this system ingests server logs.
      // What is readable is the site's stated POLICY, from robots.txt. The
      // label says access, so the nav cannot promise what the screen does not
      // deliver.
      { section: 'crawler', label: 'AI crawlers', path: '/crawler', accent: 5 },
    ],
  },
  {
    key: 'investigation',
    label: 'Investigation',
    items: [
      { section: 'gaps', label: 'Answer gaps', path: '/gaps', accent: 0 },
      { section: 'prompts', label: 'Prompts', path: '/prompts', accent: 1 },
      // Epic E. The seat B.1 reserved and `clientNav.test.ts` asserted would
      // fit — taken without touching any hue above it, which is the property
      // the cluster model was built for.
      { section: 'alerts', label: 'Alerts', path: '/alerts', accent: 2 },
    ],
  },
];

/** The label a section is shown under — the content's heading, Epic 13. */
export function sectionLabel(section: ClientSection): string {
  for (const cluster of CLIENT_NAV) {
    for (const item of cluster.items) {
      if (item.section === section) return item.label;
    }
  }
  return section;
}

/** The accent a section carries, or `null` if it is unaccented. */
export function accentFor(section: ClientSection): number | null {
  for (const cluster of CLIENT_NAV) {
    for (const item of cluster.items) {
      if (item.section === section) return item.accent;
    }
  }
  return null;
}
