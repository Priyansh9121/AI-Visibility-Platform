# apps/workers — Celery or Temporal (Python)

Background workers for the Phase 1 pipeline (product-spec.md §5.4):
intake crawl → industry classification → competitor detection → prompt generation
→ engine execution → scoring → technical audit → report generation → action list.

**Stack** (§5.1): Python. Orchestrator choice (Celery vs Temporal) is an open
decision for Epic 1 — see `/docs/build-log.md`. **Not a Node workspace**;
excluded from `pnpm-workspace.yaml`.

**Facts-only rule (IP safety constraint 7):** raw AI-engine answer text and raw
competitor HTML may exist only transiently in worker memory for fact extraction.
Never persist, return, or render it. Persist only: booleans (`mentioned`),
ordinal positions/prominence, sentiment labels, citation domains + URLs, and
structural signals. See `/docs/ip-safety.md`.

Scaffolded in Epic 1.
