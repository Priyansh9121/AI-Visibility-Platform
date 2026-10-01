---
name: to-questionnaire
description: Use only when the founder asks for a questionnaire to hand to an outside expert (for example the Australian lawyer reviewing the lawyer-pending /privacy and /terms drafts, or whoever can bound the retention options the founder chooses from for a future account-deletion endpoint). Not for writing legal copy, drawing legal conclusions, or questions the founder or the repo can already answer.
disable-model-invocation: true
---

Turn something the founder cannot answer alone into a **questionnaire**: a Markdown document handed to one person to fill in async, or to fill out together in a meeting. The recipient holds knowledge the founder lacks.

**Grill the send, not the subject.** Interview the founder only about the send, which they can always answer. The questions in the document then target the gap between what the recipient knows and what the founder needs.

## Steps

1. **Who is it going to?** Ask, in one exchange: the recipient's role, expertise, and relationship to the founder. Done when you know who they are and what they know that the founder does not.
2. **What do you need back?** Ask, in one exchange: the decisions or facts the founder needs. Done when you have a concrete list of what the founder must walk away able to decide.
3. **Gather AVP facts first (read-only).** Before writing, read the repo for the facts the recipient needs, and link them by repo-relative path (for example `apps/web/src/components/legal/PrivacyPolicy.tsx`, `apps/web/src/components/legal/TermsOfService.tsx`, `apps/web/src/lib/legal/retention.ts`, `docs/product-spec.md`, `docs/runbook.md`, `apps/web/src/components/legal/providers.test.ts`; the `app/privacy` and `app/terms` page.tsx files are only wrappers). Confirm a path exists before naming it. Describe data fields from `apps/api/src/avp_api/models/`, never from rows: do not query `avp_dev` or production. Never read `apps/api/.env`, `.mcp.json`, `.claude/settings.local.json`, `.devdata/` or `dump.rdb`.
4. **Write the questionnaire** using the template below. Every item named in step 2 must be covered by a question.
5. **Place the output where the founder says.** Default: show it in chat. For a file, ask the founder for a path outside the repo; do not default to a shared temp dir. If the founder names a repo path, write there and say "do not stage or commit it". Report the path.

## Rules for the content

- **AVP facts only, no legal conclusions.** State what AVP does (what data is collected, where it is stored, which processors, what the endpoints do) with a link to the source. Do not say what the law requires, whether AVP complies, or which clause is adequate; those are the questions, not the answers. Do not rewrite or draft `/privacy`, `/terms` or `/crawler` copy.
- **No secrets, tokens, emails or customer data** in the document. Describe fields by name and type, never real values.
- **No third-party raw text** (AI answers, page HTML, competitor copy); counts, names and URLs only.
- **No invented numbers.** Figures appear only with a source in the repo; otherwise leave them out or mark them "unverified".
- **Product decisions stay open.** Retention, a price, a limit or user-visible behaviour are the founder's decision. List them under a "Decisions we have not made" heading and ask the recipient to bound the options the founder chooses from (options, trade-offs, limits), not to choose. No account-deletion endpoint exists yet; do not describe one as built.
- Jurisdiction: state only what the drafts say. The privacy draft cites the Australian Privacy Act 1988 (`apps/web/src/components/legal/PrivacyPolicy.tsx` ~31, ~60); the terms choose the law of the operator's state (`TermsOfService.tsx` ~98-101). Then ASK the recipient which laws apply. Do not assert any other statute (the Spam Act 2003 is not in the repo; leave it out or mark it unverified). The drafts are lawyer-pending.

## Document structure

Frame it as a discovery questionnaire. Order questions most-important-first (async may give one pass) and group under `##` headings by theme once there are more than a handful.

<questionnaire-template>

# <Questionnaire title>

**Purpose:** why this exists and the decision riding on it.

**From:** <the founder>, **To:** <the recipient>, **How your answers will be used:** <where they go>

## Context

One paragraph orienting a recipient who was not in the founder's head, plus links to the AVP facts (repo-relative paths). Enough to answer well, not a page.

## How to answer

Deadline and rough effort. Partial answers and "I don't know" are useful: flag anything unsure rather than skipping it.

## <Theme heading>

One `##` per theme. Every question is one idea, never compound, with an answer stub beneath, and a one-line _why this matters_ only where it could be misread.

<question-example>
### How long may AVP keep an agency's scan records after the agency closes its account?

_Why this matters: it decides what the deletion endpoint must remove and what may be retained._

>
</question-example>

## Anything else?

A closing catch-all: anything we did not ask that we should know?

</questionnaire-template>
