/**
 * @avp/design-system — the proprietary design system.
 *
 * Every customer-facing surface imports from here. Ad hoc Tailwind defaults on
 * customer-facing screens are prohibited (docs/ip-safety.md #2).
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
export { Badge, VisibilityBadge, type BadgeProps, type BadgeTone } from './components/Badge.js';
export { DataTable, type DataTableProps, type Column } from './components/Table.js';
export { ScoreDisplay, type ScoreDisplayProps } from './components/ScoreDisplay.js';
export { ScoreMeter, type ScoreMeterProps, type ScoreAbsence } from './components/ScoreMeter.js';
export { TextField, type TextFieldProps } from './components/TextField.js';

export { ChartFrame, type ChartFrameProps } from './components/chart/ChartFrame.js';
export { ChartPatterns, patternPaint } from './components/chart/ChartPatterns.js';
export { LuminanceLedger, type LuminanceLedgerProps } from './components/chart/LuminanceLedger.js';
export { AnswerShelf, type AnswerShelfProps } from './components/chart/AnswerShelf.js';
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
  Beat,
  Prose,
  Evidence,
  FixList,
  BEAT_SEQUENCE,
  type BeatId,
  type ReportPageProps,
  type ReportHeaderProps,
  type BeatProps,
  type EvidenceProps,
  type FixItem,
} from './components/report/ReportLayout.js';

export { default as tailwindPreset } from './tailwind-preset.js';
