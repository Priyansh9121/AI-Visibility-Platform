/**
 * @avp/design-system — the proprietary design system.
 *
 * Every customer-facing surface imports from here. Ad hoc Tailwind defaults on
 * customer-facing screens are prohibited (the design-system-only rule).
 *
 * Consumers must also import the stylesheets:
 *   import '@avp/design-system/styles.css'
 */

export * from './tokens/index.js';
export { cn, type ClassValue } from './lib/cn.js';

export { Button, type ButtonProps, type ButtonVariant, type ButtonSize } from './components/Button.js';
export {
  Card,
  CardHeader,
  CardTitle,
  CardBody,
  CardFooter,
  type CardProps,
  type CardElevation,
} from './components/Card.js';
export {
  Badge,
  VisibilityBadge,
  visibilityBandLabel,
  type BadgeProps,
  type BadgeTone,
} from './components/Badge.js';
export {
  StatTile,
  StatRow,
  type StatTileProps,
  type StatRowProps,
} from './components/StatTile.js';
export { DataTable, type DataTableProps, type Column } from './components/Table.js';
export { ScoreDisplay, type ScoreDisplayProps } from './components/ScoreDisplay.js';
export { ScoreHero, type ScoreHeroProps } from './components/ScoreHero.js';
export { PageHead, type PageHeadProps } from './components/PageHead.js';
export { ScoreMeter, type ScoreMeterProps, type ScoreAbsence } from './components/ScoreMeter.js';
export {
  VerdictBar,
  type VerdictBarProps,
  type VerdictCounts,
} from './components/VerdictBar.js';
export { TextField, type TextFieldProps } from './components/TextField.js';
export {
  SelectField,
  type SelectFieldProps,
  type SelectOption,
} from './components/SelectField.js';

export { ChartFrame, type ChartFrameProps } from './components/chart/ChartFrame.js';
export { ChartPatterns, patternPaint } from './components/chart/ChartPatterns.js';
export { LuminanceLedger, type LuminanceLedgerProps } from './components/chart/LuminanceLedger.js';
export { AnswerShelf, type AnswerShelfProps } from './components/chart/AnswerShelf.js';
export { TrendChart, type TrendChartProps } from './components/chart/TrendChart.js';
export {
  SentimentTide,
  negativePatternId,
  type SentimentTideProps,
} from './components/chart/SentimentTide.js';
export { GapGrid, type GapGridProps } from './components/chart/GapGrid.js';
export {
  layoutGapGrid,
  gapSeverity,
  GAP_KIND_LABEL,
  GAP_KIND_SHORT,
  type GapRowKind,
  type GapRowInput,
  type GapBrandInput,
  type GapCellInput,
  type GapSort,
  type GapGridLayout,
  type GapRowLayout,
  type GapCellLayout,
} from './components/chart/gapGridLayout.js';
export {
  layoutTide,
  netByEngine,
  type TideBuckets,
  type TidePointInput,
  type TideLayout,
  type TideLayoutOptions,
  type TideBar,
} from './components/chart/sentimentTideLayout.js';
export {
  layoutTrend,
  segmentPath,
  spreadLabels,
  truncateLabel,
  LABEL_MAX_CHARS,
  type TrendPoint,
  type TrendSeriesInput,
  type TrendSeriesLayout,
  type TrendSegment,
  type TrendLayout,
  type TrendLayoutOptions,
} from './components/chart/trendLayout.js';
export {
  layoutAnswerShelf,
  shelfSummary,
  type ShelfRowInput,
  type ShelfSlotInput,
  type ShelfLayout,
  type ShelfLayoutOptions,
  type ShelfRow,
  type ShelfMark,
  type SubjectMark,
} from './components/chart/answerShelfLayout.js';
export {
  layoutLedger,
  compositeScore,
  type LedgerDimension,
  type LedgerCompetitor,
  type LedgerSegment,
  type LedgerGhost,
  type LedgerLayout,
} from './components/chart/ledgerLayout.js';

export {
  ReportPage,
  ReportHeader,
  ReportMetaItem,
  ScoreBlock,
  Beat,
  Prose,
  Evidence,
  FixList,
  BEAT_SEQUENCE,
  type BeatId,
  type ReportPageProps,
  type ReportHeaderProps,
  type ReportMetaItemProps,
  type ScoreBlockProps,
  type BeatProps,
  type EvidenceProps,
  type FixItem,
} from './components/report/ReportLayout.js';

export {
  AppShell,
  NavItem,
  NavGroup,
  NavSlot,
  NavDisclosure,
  NavPanel,
  NavSubItem,
  NavHead,
  NavScore,
  type AppShellProps,
  type NavItemProps,
  type NavGroupProps,
  type NavDisclosureProps,
  type NavPanelProps,
  type NavSubItemProps,
  type NavHeadProps,
  type NavScoreProps,
} from './components/shell/AppShell.js';

export {
  LocalNav,
  LocalNavItem,
  LocalNavGroup,
  type LocalNavProps,
  type LocalNavItemProps,
  type LocalNavGroupProps,
} from './components/shell/LocalNav.js';

export { LoadingState, type LoadingStateProps } from './components/state/LoadingState.js';
export { ErrorState, type ErrorStateProps } from './components/state/ErrorState.js';
export { EmptyState, type EmptyStateProps } from './components/state/EmptyState.js';

export { PageSection, type PageSectionProps } from './components/marketing/PageSection.js';

export {
  Reveal,
  RevealGroup,
  type RevealProps,
  type RevealGroupProps,
  type RevealElement,
} from './components/Reveal.js';

export { default as tailwindPreset } from './tailwind-preset.js';
