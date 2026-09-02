/**
 * Sentiment Tide layout — the arithmetic behind `SentimentTide`, separated so
 * it is testable without a renderer. Same split `ledgerLayout.ts` established.
 *
 * WHAT THE SHAPE IS, AND WHY DIVERGING RATHER THAN STACKED
 * --------------------------------------------------------
 * One scan, one engine, is a group of answers sorted into four buckets:
 * positive, neutral, negative, and "the subject was not named". A plain stacked
 * bar puts all four in one column and makes the reader compare segment heights
 * to work out whether the news is good. A diverging bar puts positive ABOVE a
 * waterline and negative BELOW it, so the direction is read before any value
 * is: a column sitting mostly under the line is bad news at a glance.
 *
 * That also means POSITION carries the ordering. Sentiment is ordinal —
 * positive is better than negative — and the geometry says so, which is why the
 * component is free to encode the ENGINE in hue instead. Hue is doing
 * categorical work here precisely because the axis is doing the ordinal work.
 *
 * THE FOURTH BUCKET IS NOT A NEUTRAL, AND THIS FILE IS WHERE THAT IS ENFORCED
 * ---------------------------------------------------------------------------
 * `unclassified` is an answer in which the subject never appeared, so tone
 * toward it was never asked. `classify_sentiment` is only called when the
 * subject was named — spending a model call on a brand nobody mentioned would
 * be both wasteful and misleading, and scoring-spec.md excludes it from the
 * composite rather than scoring it zero.
 *
 * So it is NOT DRAWN AT ALL. It is carried as a count and stated in words, in
 * the hidden data table and in the ledger beside the chart. A fourth rect on a
 * chart whose other three are tones would be read as a fourth tone, and folding
 * it into neutral would report a brand nobody mentioned as having been
 * described neutrally — a measurement nobody took, which is the one thing this
 * product must not do.
 *
 * AN ENGINE THAT ANSWERED NOTHING HAS NO COLUMN AT ALL
 * -----------------------------------------------------
 * A scan where an engine failed outright yields no bucket for it — not four
 * zeros. Four zeros would draw a flat column on the waterline, which reads as
 * "this engine described you neutrally" rather than "this engine was down".
 * `layoutTide` drops it and reports it in `missing` so the caller can say so.
 */

export interface TideBuckets {
  positive: number;
  neutral: number;
  negative: number;
  /** Answers where the subject was never named. NOT a neutral. */
  unclassified: number;
}

export interface TidePointInput {
  /** Label for this column of the x-axis — a date, already formatted. */
  label: string;
  /** Machine-readable stamp, for the hidden data table. */
  stamp: string;
  /** One entry per engine that answered anything. A missing engine is absent. */
  byEngine: Readonly<Record<string, TideBuckets>>;
}

export interface TideLayoutOptions {
  width?: number;
  height?: number;
  padding?: { top: number; right: number; bottom: number; left: number };
  /** Gap between the engine bars inside one scan's group, in units. */
  barGap?: number;
  /** Gap between one scan's group and the next, in units. */
  groupGap?: number;
}

export interface TideBar {
  engine: string;
  /** Index of this engine in the stable engine order — drives its hue. */
  engineIndex: number;
  pointIndex: number;
  x: number;
  width: number;
  /** Rect above the waterline. Height 0 when there is nothing positive. */
  positive: { y: number; height: number; count: number };
  /** Rect below the waterline. */
  negative: { y: number; height: number; count: number };
  /** A band straddling the waterline. Neutral is neither up nor down. */
  neutral: { y: number; height: number; count: number };
  /**
   * Answers where the subject was never named — a COUNT ONLY, deliberately
   * given no geometry.
   *
   * It is not drawn. Sentiment is a judgement about how the client was
   * described, and this is the absence of any description; a fourth rect on a
   * chart whose other three are tones would be read as a fourth tone, which is
   * the exact confusion this bucket exists to prevent. It reaches the reader in
   * the hidden data table and in the ledger beside the chart, where it can be
   * labelled in words instead of inferred from a height.
   */
  unclassified: { count: number };
  /** positive - negative, the number the column is read for. */
  net: number;
  /** Answers with a tone. The denominator for any rate a caller computes. */
  classified: number;
}

export interface TideLayout {
  width: number;
  height: number;
  plot: { x: number; y: number; width: number; height: number };
  /** The zero line, in user units. */
  waterline: number;
  /** Largest single-direction stack, which sets the scale. Never 0. */
  scale: number;
  points: readonly { label: string; stamp: string; x: number }[];
  bars: readonly TideBar[];
  /** Stable engine order, derived from the data. Drives hue assignment. */
  engines: readonly string[];
  /** Per point, engines absent from `byEngine` — reported, never drawn as 0. */
  missing: readonly { pointIndex: number; engines: readonly string[] }[];
  /** True when nothing anywhere has a tone. The caller shows an empty state. */
  empty: boolean;
}

const DEFAULTS = {
  width: 720,
  height: 320,
  padding: { top: 20, right: 16, bottom: 34, left: 30 },
  barGap: 4,
  groupGap: 28,
};

/**
 * Lay out the tide.
 *
 * Pure and deterministic: the same input always yields the same geometry, which
 * matters because these columns get compared across months.
 */
export function layoutTide(
  points: readonly TidePointInput[],
  options: TideLayoutOptions = {},
): TideLayout {
  const width = options.width ?? DEFAULTS.width;
  const height = options.height ?? DEFAULTS.height;
  const padding = options.padding ?? DEFAULTS.padding;
  const barGap = options.barGap ?? DEFAULTS.barGap;
  const groupGap = options.groupGap ?? DEFAULTS.groupGap;

  const plot = {
    x: padding.left,
    y: padding.top,
    width: Math.max(1, width - padding.left - padding.right),
    height: Math.max(1, height - padding.top - padding.bottom),
  };

  // Stable engine order across every point, so a column does not change hue
  // between scans. Sorted rather than first-seen: first-seen would reorder the
  // whole chart the day the earliest scan happened to lose an engine.
  const engines = [
    ...new Set(points.flatMap((p) => Object.keys(p.byEngine))),
  ].sort();

  // The scale is the largest single-direction stack anywhere, so the waterline
  // sits in a fixed place and two scans are comparable by height. Positive and
  // negative share it — a chart whose two halves scaled independently would
  // make a small negative look like a large one.
  let scale = 0;
  for (const point of points) {
    for (const engine of engines) {
      const b = point.byEngine[engine];
      if (!b) continue;
      scale = Math.max(scale, b.positive + b.neutral / 2, b.negative + b.neutral / 2);
    }
  }
  const empty = scale === 0;
  // Never 0 — a zero scale divides by nothing and collapses every rect.
  scale = scale === 0 ? 1 : scale;

  const groups = Math.max(1, points.length);
  const groupWidth = (plot.width - groupGap * (groups - 1)) / groups;
  const barWidth =
    engines.length === 0
      ? groupWidth
      : (groupWidth - barGap * (engines.length - 1)) / engines.length;

  /*
   * THE WATERLINE IS PLACED BY THE DATA, NOT AT THE MIDDLE.
   *
   * It was centred at first, on the reasoning that an all-positive chart and
   * an all-negative one should be mirror images. Measured on the real
   * `plausible.io` history that was a bad trade: tone there is overwhelmingly
   * positive, so the negative half of the plot held one hatched sliver and
   * the bottom 45% of the figure was empty. Half a chart of nothing is not a
   * fair rendering of "almost entirely positive" — it is a rendering of the
   * axis, not of the data.
   *
   * So the two halves are sized by the largest stack in EACH direction, which
   * keeps the property that actually matters: ONE unit scale. A bar of five
   * positive and a bar of five negative are the same length, because
   * `above / maxUp` and `below / maxDown` are the same number by construction.
   * What changes is only where the zero line sits.
   *
   * The mirror-image property survives too — an all-positive chart and its
   * negation put the waterline at opposite ends and draw identical bars.
   */
  let maxUp = 0;
  let maxDown = 0;
  for (const point of points) {
    for (const engine of engines) {
      const b = point.byEngine[engine];
      if (!b) continue;
      maxUp = Math.max(maxUp, b.positive + b.neutral / 2);
      maxDown = Math.max(maxDown, b.negative + b.neutral / 2);
    }
  }
  // Nothing either way: park it in the middle rather than dividing by zero.
  const span = maxUp + maxDown;
  const upFraction = span === 0 ? 0.5 : maxUp / span;
  const waterline = plot.y + plot.height * upFraction;
  const unit = span === 0 ? 0 : plot.height / span;

  const bars: TideBar[] = [];
  const missing: { pointIndex: number; engines: string[] }[] = [];
  const laidOutPoints: { label: string; stamp: string; x: number }[] = [];

  points.forEach((point, pointIndex) => {
    const groupX = plot.x + pointIndex * (groupWidth + groupGap);
    laidOutPoints.push({
      label: point.label,
      stamp: point.stamp,
      x: groupX + groupWidth / 2,
    });

    const absent: string[] = [];
    engines.forEach((engine, engineIndex) => {
      const b = point.byEngine[engine];
      if (!b) {
        // No column. See the module note — four zeros would be a lie.
        absent.push(engine);
        return;
      }
      const x = groupX + engineIndex * (barWidth + barGap);
      // Neutral straddles the waterline, half above and half below, so it
      // displaces the two directions symmetrically and never implies one.
      const halfNeutral = (b.neutral / 2) * unit;
      const positiveHeight = b.positive * unit;
      const negativeHeight = b.negative * unit;

      bars.push({
        engine,
        engineIndex,
        pointIndex,
        x,
        width: barWidth,
        neutral: {
          y: waterline - halfNeutral,
          height: halfNeutral * 2,
          count: b.neutral,
        },
        positive: {
          y: waterline - halfNeutral - positiveHeight,
          height: positiveHeight,
          count: b.positive,
        },
        negative: {
          y: waterline + halfNeutral,
          height: negativeHeight,
          count: b.negative,
        },
        unclassified: { count: b.unclassified },
        net: b.positive - b.negative,
        classified: b.positive + b.neutral + b.negative,
      });
    });
    if (absent.length > 0) missing.push({ pointIndex, engines: absent });
  });

  return {
    width,
    height,
    plot,
    waterline,
    scale,
    points: laidOutPoints,
    bars,
    engines,
    missing,
    empty,
  };
}

/**
 * Net tone per engine across the whole history — the ledger beside the chart.
 *
 * Returns null for an engine with nothing classified anywhere, rather than 0.
 * Zero is a real answer ("as much praise as criticism") and must not be the
 * same value as "never measured".
 */
export function netByEngine(
  points: readonly TidePointInput[],
): { engine: string; net: number | null; classified: number }[] {
  const engines = [...new Set(points.flatMap((p) => Object.keys(p.byEngine)))].sort();
  return engines.map((engine) => {
    let net = 0;
    let classified = 0;
    for (const point of points) {
      const b = point.byEngine[engine];
      if (!b) continue;
      net += b.positive - b.negative;
      classified += b.positive + b.neutral + b.negative;
    }
    return { engine, net: classified === 0 ? null : net, classified };
  });
}
