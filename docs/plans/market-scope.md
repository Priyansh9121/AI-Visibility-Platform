# Market scope — implementation plan

Drift check: touches layers 2 (Measurement: prompts, engine calls, competitor detection), 3 (Insight: which cited domains count as whose) and 1 (Delivery: intake, report, CSV, history); serves lifecycle phase Activation (Epic 9), with item M4 also serving Retention (Epic 11). It spans layers because one product question — *where are we comparing?* — has to reach each of them; it is split below into four items (M1–M4), each one branch and each in essentially one layer.

> **For agentic workers:** Work task by task. Steps use checkbox (`- [ ]`) syntax. **The founder accepted all eleven recommendations as written on 2026-10-09 (section 5). Nothing in this plan is built on `pilot-fixes-8`; each item is its own branch, in the order of section 4.**

**Goal:** a client is scanned, scored and compared within a market the agency names (a country, or places inside one), so a buyer's question is answered for that market and a client's own site in another country is never counted as a third party.

**Architecture:** a market scope is stored on the client and copied onto each scan when it starts, so a scan always says what it measured. It reaches each step through the levers that step actually has: the prompt text (every engine), the engines' own location fields where they exist (Claude search, Perplexity), and SerpApi's `location`/`gl` for detection. Cited domains gain one rule: a domain with the client's (or a rival's) own name under another country's suffix is that brand's *out-of-market site*, shown apart, never a third party.

**Stack:** AVP's (FastAPI + async SQLAlchemy 2 / asyncpg, Postgres, Alembic, Next 15 App Router, `@avp/design-system`). Nothing new: no new dependency and no new outbound host (place names are checked against SerpApi's Locations API on `serpapi.com`, already a processor).

**Spec:** the founder's brief of 2026-10-08 (`.claude/worktrees/pf8-backup/brief-market-scope.txt`, quoted under "The brief").

## The brief

> "If the client searches au, we keep au. But before that, we need to be asking upfront where we are comparing this, and limit the search, scoring and comparison to that state, city, suburb, country, continent, multiple suburbs, multiple cities, etc."

Trigger: RMIT University's scan counted `rmit.edu.vn` (RMIT Vietnam) as a third-party source, 4 citations.

---

## 1. What exists today (read 2026-10-08; every line checked)

| Step | Where | What it does with place |
|---|---|---|
| Competitor detection | `serp.py:140-156` (`DEFAULT_SEARCH_COUNTRY = "au"`, `SEARCH_COUNTRY_BY_TLD`), `serp.py:330` (`search_country`), `competitors.py:516-520` | The client domain's last label picks a Google country from 14 (au nz uk ie ca us za in sg my ph hk id jp); anything else, `.com` included, searches **Australia**. The model that names rivals is given the brand's name and domain, not a country (`competitors.py:536`). |
| SerpApi | `serp.py:474-481` | `engine=google`, `hl=en`, `gl=<country>`. No `location`, no `google_domain`, no `cr`. |
| Prompt generation | `prompts.py:131` (system prompt), `prompts.py:175-203` (`fallback_prompts`) | No place is ever written in. The system prompt tells the model to write as a buyer types — its own example is **"who does same-day crowns near me"** — and the fallback prompts are place-free. A "near me" question reaches the engines with no location to resolve it against. |
| Engines | `engines.py:538-541` (Claude search: `web_search_20260209`, `allowed_callers: ["direct"]`); `engines.py:909-959` (Perplexity: `POST /v1/agent`, `web_search` with `search_context_size`, `max_results`); `engines.py:165` (ChatGPT: `gpt-5.5`, chat completions, no search tool); Gemini `generateContent`, no grounding | **No engine is sent a location.** ChatGPT and Gemini have no search tool here, so only the prompt text can reach them. |
| Citation types | `extraction.py:301-318` (`classify_citation`) | `domain == subject_domain` → OWNED; in `competitor_domains` → COMPETITOR; else a third-party type. **Exact equality only**: `rmit.edu.vn` is not `rmit.edu.au`, so it is a third party. |
| Data model | `models/client.py`, `models/scan.py`, `models/competitor.py` | No country, region, locale, market or language field anywhere. |
| Report attribution | `report.py:521-563`; `domain_authority.py:178-183` (`normalise_domain`); `scan_runner.py:650-657` | Cited domains are grouped as stored at scan time. A rival's citation is linked by **exact** domain (`competitor_ids.get(cited.domain)`); `www.` is stripped, nothing else. |
| The "Get onto …" fix | `report.py:609-615` | Any domain that is neither the client's nor a rival's and has **at least 4 citations** is eligible. `rmit.edu.vn` had exactly 4. |
| Scoring's "own" domains | `scoring.py:579-587` | The subject is credited the authority of every cited domain **not flagged `cites_subject`**; a rival, of every domain but its one `competitor.domain`. A rival has **one** domain (`models/competitor.py:184-243`). |
| Tier A | `domain_authority.py:139-146` (`_CURATED_TLDS`, `_CURATED_CC_LABELS`) | `edu` and its country forms are curated (value 100): **`rmit.edu.vn` is Tier A.** |
| Fix generator | `fix_generator.py:652-667` | The one place with subdomain and parent matching (`host == a or host.endswith("." + a) or a.endswith("." + host)`). |
| Intake | `routers/clients.py:65-121`; `intake.py:231-270`; `apps/web/src/components/IntakeForm.tsx:61-137` | One field, "Website address", reduced to its registrable domain. No form edits a client's domain afterwards. |
| History | `routers/clients.py:166-190`; `client_history.py:158-260`; `schemas/client_history.py:74-94` | Each scan's stored composite and figures. **No `formula_version` in the payload**, so the page cannot mark a version change. The visibility alert does refuse to compare across versions (`alerts.py:283-295`, `alerts.formula_changed`). |

**What that means for RMIT.** `rmit.edu.vn` is not just listed as a third party. It is a Tier A domain the scoring treats as independent, so **every answer that names RMIT and cites RMIT Vietnam credits RMIT's own Citation Strength with a top-tier source**. RMIT's 96.01 may be partly RMIT vouching for itself. The same holds for any brand with country sites on a curated suffix (Monash: `monash.edu`, `monash.edu.my`), and for rivals.

## 2. What the providers allow (each provider's own docs, read 2026-10-08)

| Provider | Location control | Granularity | Filter or hint | Several at once |
|---|---|---|---|---|
| Anthropic web search (`web_search_20250305` → `20260318`) | `user_location {type: "approximate", city, region, country, timezone}` | ISO 3166-1 alpha-2 country (unsupported codes are a 400), free-text region and city, IANA timezone; no coordinates, no continent | **Hint** ("localize search results") | No: one object |
| Anthropic domain lists | `allowed_domains` *or* `blocked_domains` | Exact domains, subdomains included; `*` only in a path, so **no "all of .com.au"** | Hard filter | n/a |
| Perplexity Agent API (Sonar chat completions ended 2026-09-27) | `user_location` on the `web_search` tool: `country`, `region`, `city`, `latitude`+`longitude` (need `country`) | as above, plus coordinates | **Unclear**: the page is headed "Location filter" but says "Personalize" | No |
| Perplexity domain filter | `filters.search_domain_filter`, up to 20, allow or `-`deny | Accepts country suffixes (`.uk`, `.ca`) | Hard filter | n/a |
| OpenAI Responses `web_search` (not used by AVP today) | `user_location {type: "approximate", country, city, region, timezone}` | as Anthropic | Hint; **omitted means the United States** | No |
| Gemini grounding | none documented | — | — | — |
| SerpApi Google | `location` (a canonical name from the free Locations API, e.g. `Melbourne,Victoria,Australia`; "Melbourne" alone also matches Florida, England, Arkansas and Iowa), `gl`, `google_domain` (`google.com.au`), `cr` | city to country | `location`/`gl` bias; **`cr` is a hard country filter and the only control here that takes several countries** (`countryAU\|countryNZ`) | `cr` only |

Two consequences shape everything below. **No engine takes a continent, and none takes more than one place per request.** And the engines' location fields are hints: the only levers that reach all four engines, and bind them, are the words of the question and, for the two with search, a domain list.

## 3. Recommendations, question by question

**Granularity and shape.** A scope is **one country, optionally narrowed to up to five places inside it** (states, cities or suburbs, mixed). No continents and no several-country scopes in this version: no engine can be told either, so a "continent" would mean running the whole prompt set once per country (each run about US$1.45–1.50, north-star §5.1) or not localising at all. Multi-country can come later as several scopes, one scan each.

**At intake.** Asked on the add-client form, **required for new clients**, prefilled from the domain's country suffix by the rule detection already uses (`.au` → Australia; `.com` and unknown suffixes → Australia, as today), shown as "Comparing in: Australia — change". Places are picked from SerpApi's Locations API (free, `serpapi.com`, already a processor), so a stored place is always the canonical `Melbourne,Victoria,Australia` and never an ambiguous "Melbourne". Editable later on the client's settings; a change applies from the next scan.

**How the scope enters each step.**
- *Prompt text:* the generator is told the market and writes it in where a buyer would ("best universities in Melbourne", "drain cleaning in Richmond"); "near me" becomes the place. With several places, each prompt names one, round-robin, so the scan costs the same. A country-only scope names the country only where a buyer would (most country-level questions need no place word, and the engines get it as a location instead).
- *Engine location fields:* Claude search and Perplexity get `user_location` with the prompt's own place (country always; region and city when the prompt has one). ChatGPT and Gemini have no such field here; the prompt text is their only lever.
- *Detection:* SerpApi gets `location` (the canonical place) and `gl` (the country), and `google_domain` for that country; with several places, one query set per place up to five, so detection costs at most five times today's searches (a new client only).
- *Rivals in or out of market:* detection proposes only rivals whose site is in the market's country (its suffix) or on a generic suffix; the agency can still add any rival by hand.

**Domains: "if the client searches au, we keep au".** Under an Australian scope, `rmit.edu.vn` is **RMIT University's out-of-market site**: the same name as the client's domain (`rmit`) under another country's suffix. It is not the client's in-market site and **not a third party**: it never feeds a third-party list, a "Get onto …" fix or Tier B; the report lists it apart as "RMIT University's sites outside Australia: rmit.edu.vn — 4 citations". The same rule applies to each rival (Monash's `monash.edu.my` is Monash's, outside the market). The match is proposed by rule and **confirmed by the agency** (a list on the client), because a shared name is evidence, not proof. It matters to the number as well as to the page: today `rmit.edu.vn` is a Tier A domain the scoring treats as independent, so RMIT's own Vietnamese site raises RMIT's Citation Strength. That needs no market to be wrong, so it is the first item (M1), on its own.

**Scoring.** Nothing is filtered: an answer is a fact, whatever it names. What changes is the inputs — the questions, the engines' locations, the rivals — and one attribution rule (a sibling is the brand's own, not a third party). Both change what a score measures, so each bumps `FORMULA_VERSION`: **`v8` with M1** (a sibling stops crediting its own brand) and **`v9` with M3** (the market reaches the questions). Stored scans are never re-scored, and a score is never compared with one of another version (the alert already refuses across a version).

**Report and CSV.** The market is stated where a reader looks first: on the cover and the web letterhead ("Market: Melbourne, Victoria, Australia"), in the CSV's `Scan` section (`Market`), and on each scan in the client's history. A scan made before market scope reads "Market: not set (scanned before market scope)".

**Existing clients and scans.** Existing clients get the scope the detection rule already implies (their suffix's country, else Australia), so their next scan measures what their last one did plus the place words; the agency is shown the scope once and can change it. The history page marks a scope change as a break, as it marks a formula change, and before/after never compares across one.

**Cost, facts-only, pages, hosts.** Per scan: no change for one place or a country (same prompts, same calls); detection only for a new client, at most five times today's searches. Facts-only: a scope is the agency's own setting and a place name, never third-party text. `/privacy`: no new processor (SerpApi is named; the Locations API is the same host); a sentence that place names are stored. `/crawler`: unchanged (the crawler is not localised). New outbound hosts: none.

## Global constraints

- Facts-only: a scope is the agency's own setting and a place name; no third-party text is stored.
- No new runtime dependency and no new outbound host (place lookup uses SerpApi's Locations API on `serpapi.com`); `/privacy` gains one sentence (place names are stored), `/crawler` is unchanged.
- No secret, token or email in a log; a place name in a log line is allowed (it is the agency's setting, not personal data).
- Scoring never re-scores history: each item that changes what a score measures bumps `FORMULA_VERSION`, and one that changes what is stored at scan time also bumps `MATCHER_VERSION` (pilot-fixes-8 A6), so `POST /scans/{scanId}/score` refuses an older scan.
- Design-system tokens and components only; ui-reviewer on every web task; security-reviewer on any new endpoint.
- The full bar with count gates on every task; floors raised in the change that adds tests; one build-log entry per commit.

## Review focus

1. A brand whose name is a common word (`apple.com.au` and `apple.com.vn` may be different companies): the sibling rule only **proposes**; nothing is treated as a sibling until the agency confirms it (M1, task 1.1 pins it).
2. A rival with several country sites whose main domain is not the one detection stored: confirmed siblings attach to the rival, never to the client (M1, task 1.2).
3. A place SerpApi resolves to another country ("Melbourne" → Florida): only canonical names inside the scope's country are accepted (M2, task 2.2).
4. A scan that starts while its client's scope is being edited: the scan copies the scope once, at its RUNNING transition, and never re-reads it (M3, task 3.1).
5. History across a scope change: no before/after, no alert, and a visible break (M4, task 4.2).

## Decisions

All eleven in section 5 were accepted as written by the founder on 2026-10-09. **Continents and several-country scopes are out of this version on purpose**: no engine accepts either (each takes one location per request, and none takes a continent), so supporting them would mean one full prompt set per country or no localisation at all. They can come later as several scopes, one scan each.

---

## How each task runs

- **Preflight, before any `uv run`:** `uv lock --check` in `apps/api` and in `apps/workers`, each to its own log, each exit code read on its own; a non-zero one stops the task until the lock is current. Every `uv run` below is `uv run --locked`.
- **Red, then green:** each task's tests are written first and run alone (`uv run --locked pytest -q -p no:cacheprovider <file>` > a log; read the exit code), expecting FAIL, then PASS. Single-file runs are never count-gated.
- **The full bar** after each task (delegated to the test-runner): every suite against its floor in `infra/ci/floors.json` through `infra/ci/assert_count.py`, mypy against its ceiling, ruff, every typecheck, both `uv lock --check`, Alembic `upgrade head` and `check` on a scratch database (`-x db_url=…`), and the OpenAPI regeneration diff (expected to differ only in M1 task 1.1 and M2 task 2.2, which change endpoints; then `corepack pnpm --filter @avp/shared-types generate`). Never pipe a command.
- A negative control per task (the rule switched off fails the task's tests), a build-log entry with a Verified block, and the founder commits when asked.

## 4. The items — four branches, in this order

### M1 — A brand's own sites in other countries are never a third party (Layer 3; `FORMULA_VERSION` v8; first, on its own)

Needs no market scope: `rmit.edu.vn` is RMIT's own site whatever market is chosen. Cheapest item, and the only one that corrects a number today.

**Task 1.1 — the rule, proposed and confirmed.** Files: create `apps/api/src/avp_api/services/siblings.py`; modify `models/client.py`, `models/competitor.py` (each gains `other_sites: JSONB list[str]`, default `[]`), an Alembic revision, the client and competitor routes and schemas, `docs/api-contracts.md`. Agent: implementer; security-reviewer (tenancy of the new lists).
- Produces: `same_brand_elsewhere(domain: str, brand_domain: str) -> bool` — the label left of each public suffix is equal and the suffixes differ (`rmit.edu.au`/`rmit.edu.vn` yes; `monash.edu`/`monash.edu.my` yes; `rmit-online.com` no; `pineapple.com.vn` vs `apple.com.au` no).
- Tests first: that table, plus "proposed is not confirmed": a proposed sibling changes nothing until it is in `other_sites`.

**Task 1.2 — attribution and scoring.** Files: `extraction.py:301-318` (a confirmed sibling is the brand's own: `cites_subject` true for the client's, linked to the rival for a rival's), `scan_runner.py:650-657`, `scoring.py:579-587` (a rival's own set is its domain plus its `other_sites`), `domain_authority.py:283-293` (Tier B refuses them), `report.py:609-615` (never unclaimed), `fix_generator.py:652-667` (the client's own, allowed). `MATCHER_VERSION` and `FORMULA_VERSION` move to v8; `docs/scoring-spec.md` changelog. Agent: implementer.
- Test first, on RMIT's shape: an answer naming RMIT that cites `rmit.edu.vn` gives RMIT **no** authority credit; `rmit.edu.vn` is not in the unclaimed list and is never "Get onto …"; a rival's confirmed sibling is linked to the rival.

**Task 1.3 — the report and the CSV.** "RMIT University's other sites: rmit.edu.vn — 4 citations" under the evidence beat; CSV `Attributed to` = the brand's name. ui-reviewer.

### M2 — The market is asked for and stored (Layer 1)

**Task 2.1 — the model.** `clients.market_country` (ISO 3166-1 alpha-2, NOT NULL, backfilled from the domain's suffix, else `AU`) and `clients.market_places` (JSONB, ≤ 5 canonical names, default `[]`); `scans.market_country` / `scans.market_places` (NULL = "not set": scanned before market scope). Produces `market.country_for_domain(domain) -> str` and `market.google_country(iso2) -> str` (`GB` ↔ `uk`; `serp.SEARCH_COUNTRY_BY_TLD` moves here). Test: `rmit.edu.au`→`AU`, `x.co.nz`→`NZ`, `x.co.uk`→`GB`, `x.com`→`AU`; `google_country("GB") == "uk"`.

**Task 2.2 — the API.** Create and update accept `marketCountry` and `marketPlaces`; `GET /locations?q=` returns up to 10 canonical names from SerpApi's Locations API, inside the client's country only. Tests (httpx `MockTransport`, no network): six places → 422; a place outside the country → 422; "Melbourne" offers `Melbourne,Victoria,Australia` and never a Florida Melbourne under `AU`. security-reviewer (a new endpoint that calls out: auth and the existing per-user rate limit).

**Task 2.3 — the web.** `IntakeForm.tsx:61-137` gains "Comparing in: Australia — change" (prefilled) and a place picker (≤ 5); the client's settings gain the same, "Applies from the next scan." ui-reviewer.

### M3 — The market reaches measurement (Layer 2; `FORMULA_VERSION` v9)

**Task 3.1 — the scan copies the scope** at its RUNNING transition (`scan_runner.py:502-505`, beside `engine_versions` and `matcher_version`). Test: a later edit to the client never changes a scan already started.

**Task 3.2 — prompts.** The generator is told the market (`prompts.py:131`): name the place where a buyer would, and never "near me"; `fallback_prompts` (`prompts.py:175-203`) carry it; several places are assigned round-robin. Tests: a Melbourne scope's fallback set names Melbourne; no prompt contains "near me" (the post-check rewrites it to the prompt's place); two places split a set evenly.

**Task 3.3 — engines.** Claude search (`engines.py:538-541`) and Perplexity (`engines.py:931-959`) send `user_location` with the prompt's place (`country` always; `region`, `city` when the prompt has them). ChatGPT and Gemini are unchanged. Test: the captured request bodies.

**Task 3.4 — detection.** SerpApi gets `location`, `gl` and `google_domain` (`serp.py:474-481`); one query set per place, ≤ 5; only in-market rivals are proposed (`competitors.py:516-536`); a manual rival is never refused for its country. Tests: the params for a Melbourne scope; a `.vn` candidate is not proposed under `AU`; a manual `.vn` rival is accepted.

### M4 — The market on the page and across history (Layers 1 and 3; Retention)

**Task 4.1** — "Market: Melbourne, Victoria, Australia" on the PDF cover and the web letterhead; the CSV's `Scan` section gains `Market`; a scan from before reads "not set (scanned before market scope)"; M1's line becomes "RMIT University's sites outside Australia". ui-reviewer.

**Task 4.2** — the history payload (`schemas/client_history.py:74-94`) gains `formulaVersion` and `market`; the page marks a break where either changes; the visibility alert (`alerts.py:283-295`) also refuses across a market change (`alerts.market_changed`).

## 5. Decisions (product calls; all accepted as written, 2026-10-09)

| # | Decision | Recommendation |
|---|---|---|
| 1 | **A brand's same-name site in another country** (`rmit.edu.vn`, `monash.edu.my`) | The brand's own, never a third party, never authority credit, never "Get onto …"; proposed by rule, **confirmed by the agency**; **ship first, as its own branch (M1, v8)**, since it corrects RMIT's Citation Strength today. |
| 2 | Shape of a scope | One country, plus up to five places inside it. No continents and no several-country scopes in this version (no engine can be told either). |
| 3 | At intake | Required for new clients, prefilled from the domain's suffix (else Australia), editable later, applying from the next scan. |
| 4 | Place in the prompt text | Yes: named where a buyer would, "near me" becomes the place; several places round-robin at the same cost. |
| 5 | Engines' location fields | Yes for Claude search and Perplexity (hints, at no cost). |
| 6 | Detection | One SerpApi query set per place, up to five: at most five times today's detection searches, for a new client only. |
| 7 | Rivals outside the market | Detection proposes only in-market rivals; an agency can still add anyone by hand. |
| 8 | Scoring | Nothing filtered; only the inputs change. M1 and M3 each bump `FORMULA_VERSION` (v8, v9); stored scans are never re-scored, and scores are never compared across a version. |
| 9 | Where the market is stated | PDF cover, web letterhead, CSV `Scan` section, and each scan in the history. |
| 10 | Existing clients and scans | Default from the domain's suffix (else Australia), shown once to confirm; history marks the break; no before/after across a market change. |
| 11 | How a place is entered | Picked from SerpApi's free Locations API (canonical names, same host, no new processor), not typed freely. |

**Side findings, not decisions:** OpenAI's web search assumes the United States when given no location (relevant if ChatGPT ever gets a search tool here); Perplexity's Sonar chat completions ended on 27 Sep 2026 (AVP already uses the Agent API); and Perplexity's docs no longer list the `search_context_size` AVP sends (`engines.py:939`), so it may now be ignored — unconfirmed, worth one live check.

