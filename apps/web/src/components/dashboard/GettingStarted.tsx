import type { JSX } from 'react';
import { Button, Checklist, type ChecklistStep } from '@avp/design-system';
import {
  GETTING_STARTED_COMPLETE_LEAD,
  GETTING_STARTED_COMPLETE_TITLE,
  GETTING_STARTED_TITLE,
  type GettingStarted as GettingStartedModel,
} from '@/lib/dashboard/gettingStarted';

/**
 * The getting-started checklist on the dashboard — Epic 19.
 *
 * Presentational: `deriveGettingStarted` decides what is true, this decides
 * how it is pressed. One primary button on the whole screen — the first
 * unlit step that has a way to do it — and the rest secondary, so the
 * screen has exactly one "next thing" rather than a row of equal calls.
 *
 * The buttons navigate the way every other CTA on this screen already does
 * (`window.location.assign`), to the same places: the intake at `/`, the
 * seats panel in Settings, a scan's own report.
 */
export function GettingStarted({
  model,
  onDismiss,
}: {
  model: GettingStartedModel;
  onDismiss?: (() => void) | undefined;
}): JSX.Element {
  const primaryKey = model.steps.find((s) => !s.done && s.action != null)?.key ?? null;

  const steps: ChecklistStep[] = model.steps.map((step) => ({
    key: step.key,
    label: step.label,
    done: step.done,
    detail: step.detail,
    action:
      step.action == null ? undefined : (
        <Button
          size="sm"
          variant={step.key === primaryKey ? 'primary' : 'secondary'}
          onClick={() => window.location.assign(step.action!.href)}
        >
          {step.action.label}
        </Button>
      ),
  }));

  return (
    <Checklist
      title={GETTING_STARTED_TITLE}
      lead={model.lead}
      completeTitle={GETTING_STARTED_COMPLETE_TITLE}
      completeLead={GETTING_STARTED_COMPLETE_LEAD}
      steps={steps}
      onDismiss={onDismiss}
      dismissLabel="Hide this"
      completeDismissLabel="Done"
    />
  );
}
