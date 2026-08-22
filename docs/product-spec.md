# Product Spec — SOURCE OF TRUTH

---
## 3. Core User & Core Loop

**User:** SEO/digital marketing agency (owner, account manager, or sales rep).

**Core loop:**
```
Enter prospect URL → Auto-detect industry + competitors → Run scan
→ Generate score + weakness report → Auto-draft outreach/pitch
→ (deal closes) → Recurring re-scan → Proposal/SOW auto-updates
→ Before/after report proves ROI → Renewal/upsell
```

## 5. Technical Architecture

### 5.1 Stack
- **Backend:** Python (FastAPI) for scan orchestration and scoring logic
- **Database:** PostgreSQL (scans, scores, competitors, citations) + Redis (job queue/caching)
- **Job orchestration:** Celery or Temporal for scheduled/bulk scans
- **Frontend:** Next.js + React, Tailwind (customized to proprietary design tokens — see Section 2), Recharts for charts (styled to custom design system, not default theme)
- **LLM layer:** Claude/GPT API for industry detection, prompt generation, page-diffing analysis, pitch/content drafting, sentiment classification
- **Search/AI data collection:** SerpApi (Google SERP), direct API calls to ChatGPT/Perplexity/Gemini where available, Playwright for AI Overviews and engines without APIs
- **PDF/report generation:** React-PDF or WeasyPrint, templated for white-labeling
- **Web crawling:** Playwright/Scrapy for technical SEO audit + competitor page diffing (structure/facts only — see Section 2 IP rules)
- **Analytics integration:** GA4 API, Google Search Console API
- **CRM integrations:** HubSpot/Pipedrive APIs (Phase 3)

### 5.2 Repo Structure (for the agent to scaffold)
```
/apps
  /web              -> Next.js frontend
  /api              -> FastAPI backend
  /workers          -> Celery/Temporal scan workers
/packages
  /design-system    -> tokens, components, chart primitives (build FIRST)
  /shared-types     -> shared TS/Python type contracts
/infra
  /db               -> migrations, schema
  /deploy           -> IaC / deployment configs
/docs
  ip-safety.md       -> Section 2 of this doc, standalone reference for all contributors
  api-contracts.md
  scoring-spec.md
```

### 5.3 Data Model (core entities)
```
Agency
 └── User (seats)
      └── Client/Prospect
           └── Scan
                ├── CompetitorSet (auto-detected)
                ├── PromptSet (auto-generated, tagged by intent)
                ├── EngineResult (per prompt x per AI engine)
                │    ├── mentioned (bool)
                │    ├── position/prominence
                │    ├── sentiment
                │    └── citations[] (source domain, url, type)
                ├── TechnicalAudit (schema, CWV, indexation, etc.)
                ├── Score (composite 0-100 + sub-scores)
                └── ActionItems[] (fix list, priority, effort estimate)
```

### 5.4 Core Pipeline (Phase 1)
1. **Intake** — URL submitted → crawl homepage/key pages → LLM classifies industry/niche
2. **Competitor detection** — query SERP for niche keywords + run seed prompts through AI engines, extract co-occurring brand/domain mentions → dedupe → rank top 3-5
3. **Prompt generation** — LLM generates prompt set based on industry + buyer journey stages
4. **Engine execution** — run each prompt against each AI engine (parallelized), capture citations/mentions
5. **Scoring** — aggregate mention rate, position/prominence, sentiment, citation count into composite score
6. **Technical audit** — crawl site for schema, CWV, indexation, content structure signals
7. **Report generation** — populate white-label template (proprietary design system), render PDF + shareable web link
8. **Action list** — LLM cross-references gaps into prioritized, named fixes

## 6. AI Visibility Score — Formula

| Sub-score | Weight | Inputs |
|---|---|---|
| Mention Rate | 30% | % of tracked prompts where brand appears at all |
| Share of Voice | 25% | Brand mentions ÷ total mentions (brand + competitors) |
| Citation Strength | 20% | Number + authority of domains citing the brand |
| Sentiment | 15% | Weighted positive/neutral/negative across all mentions |
| Technical Foundation | 10% | Schema presence, structured data, content freshness |

Each sub-score normalized 0-100, weighted sum = final score. Tune weights per-industry once real data exists.

## 7. Full Roadmap for the AI Coding Agent — Epics, Tasks, Acceptance Criteria

> Instructions for the agent: work epic by epic, in order. Do not start UI/screen work (Epic 4+) until Epic 0 (design system) and Epic 1 (infra) are complete. Re-read Section 2 (IP Safety) before any design or frontend task.

### Epic 0 — Design System Foundation (before any screens)
- [ ] Define color palette, type scale, spacing scale, elevation rules — original, not copied from any reference tool
- [ ] Design signature score/comparison visualization component (not a generic gauge/radar chart)
- [ ] Build component library in `/packages/design-system`: buttons, cards, tables, charts, report layout primitives
- [ ] Document design tokens in `/docs/design-system.md`
- **Acceptance:** a style guide page rendering all components exists and is reviewed against Section 2 checklist

### Epic 1 — Infra & Auth Foundation
- [ ] Scaffold repo structure (Section 5.2)
- [ ] Postgres schema + migrations for core entities (Section 5.3)
- [ ] Redis + job queue setup
- [ ] Agency account + user auth (seat-based)
- [ ] Environment/secrets management for API keys (SerpApi, LLM providers, engine APIs)
- **Acceptance:** an agency can sign up, log in, and see an empty dashboard

### Epic 2 — Intake & Industry Classification
- [ ] URL intake form + validation
- [ ] Crawl homepage/key pages (Playwright)
- [ ] LLM call to classify industry/niche + extract brand name/entity
- **Acceptance:** submitting a URL returns a correctly classified industry within 30 seconds

### Epic 3 — Competitor Detection Engine
- [ ] SERP-based competitor discovery (SerpApi)
- [ ] AI co-citation based competitor discovery (run seed prompts, extract co-mentioned brands)
- [ ] Dedupe + rank top 3-5 competitors
- [ ] Manual override/edit UI for competitor list
- **Acceptance:** for 10 test URLs across different industries, detected competitors are manually verified as accurate ≥80% of the time

### Epic 4 — Prompt Generation & Engine Runner
- [ ] LLM-based prompt generation (20-30 prompts per scan, industry + intent aware)
- [ ] Prompt intent tagging (awareness/comparison/bottom-funnel)
- [ ] Engine integration: start with 2 engines (e.g., ChatGPT API + Perplexity API), abstracted behind a common interface so more engines can be added later
- [ ] Playwright-based runner for engines without clean APIs (AI Overviews)
- [ ] Citation/mention extraction from each engine response
- **Acceptance:** a scan produces structured EngineResult records for every prompt x engine pair, with mentions and citations correctly parsed

### Epic 5 — Scoring Engine
- [ ] Implement composite scoring formula (Section 6)
- [ ] Sub-score breakdowns stored and retrievable
- [ ] Score comparison across brand + competitors
- **Acceptance:** score recalculates correctly and deterministically from a given EngineResult set; unit tests cover edge cases (zero mentions, all competitors tied, etc.)

### Epic 6 — Technical SEO Audit Module
- [ ] Core Web Vitals check
- [ ] Schema/structured data presence check
- [ ] Indexation/crawlability check
- **Acceptance:** audit returns pass/fail + detail for each check on a known test site

### Epic 7 — Report Generation (built on Epic 0 design system)
- [x] Report layout: narrative structure (score → biggest gap → proof → fix → pitch)
- [~] White-label branding injection — agency **name + slug** only. Logo/domain/colours
      deferred to Epic 7.1: they need new columns *and* a written policy on which tokens
      an agency may override, because the visibility ramp is load-bearing. See `build-log.md` Epic 7.0.
- [ ] PDF export + shareable web link — **deferred to Epic 7.1**
- **Acceptance:** ✅ a full report renders correctly for a real test scan
  (`scan_01M0HDRGJNWNZDSJPP0NC3SV8W`, Help Scout, composite 58.24) and passes the
  Section 2 visual review. Screenshots in `docs/screenshots/`.

### Epic 8 — Action List / Fix Generator
- [ ] LLM cross-references audit + scan gaps into named, specific recommendations
- [ ] Priority/effort estimation per fix
- **Acceptance:** for a test scan with known gaps, the generated fix list correctly names those gaps with actionable language

### Epic 9 — MVP Launch Readiness (Phase 1 complete)
- [ ] End-to-end test: URL in → report out, under 5 minutes
- [ ] Basic agency dashboard: list of past scans, re-run scan
- [ ] Pilot with 3-5 real agencies, collect feedback
- **Acceptance:** pilot agencies successfully generate and send at least one real prospect report

### Epic 10 — Phase 2: Sales Enablement (start after pilot feedback)
- [ ] Auto-generated outreach email/DM (LLM, referencing specific scan findings)
- [ ] Auto-generated one-slide sales visual
- [ ] Bulk CSV upload + overnight batch scoring
- [ ] Lead prioritization ranking
- [ ] Embeddable free-scan widget
- [ ] Competitor "recent wins" alerting
- [ ] Citation authority graph
- **Acceptance:** each feature independently testable; prioritize based on pilot agency requests first

### Epic 11 — Phase 3: Delivery & Retention (start once paying agencies exist)
- [ ] Recurring scan scheduling
- [ ] Before/after ROI report generator
- [ ] SOW/proposal generator from fix list
- [ ] CMS integrations (WordPress first)
- [ ] GA4 integration for AI traffic detection
- [ ] Review/reputation + local SEO audit modules
- [ ] CRM push integrations
- **Acceptance:** an existing pilot agency completes a full 90-day before/after cycle using the platform

### Epic 12 — Phase 4: Moat Features (start once core product has traction)
- [ ] Causal experimentation engine (A/B test tracking + SOV delta measurement)
- [ ] "Why" engine — automated page diffing vs. competitors
- [ ] Prompt fan-out/paraphrase clusters
- [ ] Ads intelligence module
- [ ] Backlink + social signal audits
- **Acceptance:** each is a standalone module added without disrupting core scan pipeline

---

## Epic 0 working assumptions — RECONCILED 2026-08-20

Epic 0 was designed before §3/§5/§6/§7 landed. These were the assumptions used.
All have now been checked against the real spec; corrections are recorded in
`build-log.md` (entry 0.3).

**Two were wrong and are fixed:** the backend is Python/FastAPI with
Celery or Temporal (§5.1), not Node — `apps/api` and `apps/workers` are now
outside the pnpm workspace; and `packages/shared-types` is a TS↔Python contract
(§5.2), to be generated from FastAPI's OpenAPI output rather than hand-written
twice.

**The rest held**, and the design system in `packages/design-system` rests on them:

- **Primary user:** an operator at an SEO / digital marketing agency.
- **Two jobs:** (a) prospecting — prove to a stranger they are invisible in AI
  answers; (b) retention — prove to an existing client that the work is moving
  the number.
- **Core loop:** pick a domain → pick/generate a prompt set → scan across AI
  answer engines → get a score + gaps vs competitors → get fixes → export a pitch.
- **Narrative order (mandated by IP-safety constraint 3):**
  score → biggest gap → proof → fix → pitch.
- **The report is an outward-facing artifact.** It gets exported and put in front
  of a prospect's CMO. This drives the light-first, print-safe design decisions
  recorded in `/docs/build-log.md` Epic 0.
- **Score is a 0–100 composite** over several sub-dimensions, and is required to be
  deterministic (hard acceptance criterion, per the kickoff brief).
