---
name: avp-geo-seo
description: Use when doing SEO or GEO work on AVP's own public pages (landing, /crawler, /privacy metadata, robots.ts, sitemap.ts, JSON-LD) or when shaping the fix generator's GEO recommendations from FixFacts. Not for general marketing copy (use the library `tells` skill or built-in `design:ux-copy`) and not for scoring work (`docs/scoring-spec.md`).
---

# AVP GEO and SEO

Drift check first (`docs/north-star.md` §8): name the layer (§3) and lifecycle phase (§6) the change serves. Everything in `docs/north-star.md` §9.1 is informational, not scoped; a brief still has to scope it.

Two modes. Pick one per task.

## How confident to sound (applies to both modes)

The evidence is thin. Read `references/geo-evidence.md` before writing a recommendation. Four caveats shape the wording:

1. The GEO paper's gain is "up to 40% in its benchmark", varies by domain, and is conditional on a source already being present in a fixed context. It is not organic discoverability. Never quote it as an expected uplift.
2. Topical relevance and context position are the most reproducible levers.
3. Different engines cite very different sources, and the same prompt varies run to run. One good result proves little.
4. Citation-oriented rewrites can impair retrieval. Recommend structure, not rewrites for the sake of citations.

So: say "may help", tie each change to a fact about the site, promise no number.

Never, in either mode: recommend hidden text, strategic text sequences, prompt injection or any content aimed at manipulating a model (Kumar & Lakkaraju is a defensive reference only); serve content that varies by user agent or is served differently to AI crawlers (no cloaking, no UA-specific middleware); or reproduce a third-party page, engine answer or competitor description.

## Mode A: AVP's own site

Facts-only applies to every field below. No ratings, reviews, customer counts, logos or testimonials that do not exist. Numbers only if the client (here: the founder) supplied them, with source and as-of date. Design-system tokens only for any visible change.

Current state: `export const metadata` in `apps/web/src/app/layout.tsx`, `privacy/page.tsx`, `crawler/page.tsx`, `terms/page.tsx`. `apps/web/src/app/robots.ts` exists (below); no `app/sitemap.ts`, no JSON-LD. Landing is `apps/web/src/components/marketing/LandingView.tsx`.

- **Metadata:** Next 15 App Router `generateMetadata` (or static `metadata`): unique title and description per page, `alternates.canonical`, `openGraph`. The credential-address pages (`/share`, `/invite`, `/reset-password`, their `/:token` forms and `/sign-up/google`) are kept out of search by `X-Robots-Tag: noindex, nofollow` and `Referrer-Policy: no-referrer`, both sent from the one `headers()` list in `apps/web/next.config.mjs` and asserted exactly in `apps/web/src/app/referrerPolicy.test.ts`. Never by a robots.txt Disallow: a crawler that obeys robots.txt never fetches a disallowed page, so it never sees that page's noindex (standard crawler behaviour, unverified here).
- **`app/robots.ts`:** returns `MetadataRoute.Robots`. It allows everything and disallows nothing: `rules: [{ userAgent: '*', allow: '/' }]`, no per-bot group, and no `sitemap` until one is served (founder decisions 2026-09-30, AI crawlers allowed for search and training, and 2026-10-01, decision 3, no Disallow at all; asserted in `apps/web/src/app/robots.test.ts`). Do not add a Disallow for a token or app route; those rely on the header above. AI crawler rules below.
- **`app/sitemap.ts`:** `MetadataRoute.Sitemap`, public surface only (`/`, `/privacy`, `/terms`, `/crawler`). `lastModified` only when it truly changed; do not stamp everything with the build time.
- **JSON-LD:** `Organization` on the landing page, `SoftwareApplication` for the product. Only properties AVP can state as fact: name, url, description, applicationCategory. No `aggregateRating`, `review`, `offers` with invented prices, or `sameAs` to profiles that do not exist. Pricing schema needs a founder decision. `FAQPage` and `HowTo` only where the visible page has that content, and for machine readability, not rich results: Google shows FAQ rich results only for well-known, authoritative government and health sites and no longer shows HowTo rich results (per Google's FAQPage structured-data doc, https://developers.google.com/search/docs/appearance/structured-data/faqpage, checked 2026-09-30, which links the deprecation post). Never promise a client rich results from this markup. Render with a `<script type="application/ld+json">` in a server component; escape `<` in the serialised JSON.
- **Entity facts:** one canonical brand name, one description, one domain, stated identically in metadata, JSON-LD, `/crawler`, `/privacy`. Inconsistent naming is the cheapest thing to fix.
- **Page shape:** answer first in the opening sentences under each heading, one topic per page, a dated "last updated" only where content changed, sources cited by URL. No fixed word or token counts: none is sourced.
- **`/crawler` metadata:** it describes AVPScanBot, which identifies itself and obeys robots.txt (`apps/api/src/avp_api/services/crawl.py`). Keep it honest; never suggest a spoofed user agent.
- **llms.txt:** unproven. If mentioned at all, say no source in `docs/north-star.md` §9.1 shows an engine reads it. Do not present it as a fix.
- **Outbound hosts:** a validator or analytics host counts as a new provider; classify it in `apps/web/src/components/legal/providers.test.ts` and name it on `/privacy`.

### AI crawler robots rules

Vendor-verified 2026-09-30. Re-verify every token and URL before shipping; vendors rename and add bots.

| Purpose | Token | Vendor doc |
| --- | --- | --- |
| Search/answer (drives visibility in AI answers) | `OAI-SearchBot` | https://developers.openai.com/api/docs/bots |
| | `Claude-SearchBot` | https://support.claude.com/en/articles/8896518 |
| | `PerplexityBot` | https://docs.perplexity.ai/guides/bots |
| Training | `GPTBot`, `ClaudeBot` | same OpenAI / Anthropic pages |
| Training and grounding control token | `Google-Extended` | https://developers.google.com/search/docs/crawling-indexing/google-common-crawlers |
| User-initiated fetch | `ChatGPT-User`, `Claude-User`, `Perplexity-User` | same pages |

- In our summary (not a vendor quote): blocking a search bot likely keeps the site out of that engine's answers, while blocking a training bot is a training-use signal. `GPTBot` disallow "indicates a site's content should not be used in training"; OpenAI recommends allowing `OAI-SearchBot`.
- `Google-Extended` is a robots.txt token only, with no separate user agent. It governs Gemini training and grounding, and "does not impact a site's inclusion in Google Search nor is it used as a ranking signal". Do not say it has no visibility effect in Gemini.
- Per vendor: Anthropic says `Claude-User` honours robots.txt (as do `ClaudeBot` and `Claude-SearchBot`). OpenAI says robots.txt rules may not apply to `ChatGPT-User`. Perplexity says `Perplexity-User` generally ignores robots.txt. So robots.txt is not an access control for OpenAI's and Perplexity's user-initiated fetches.
- AVP's own site: decided by the founder on 2026-09-30, AI crawlers are allowed, search and training alike (`robots.ts` has no per-bot group). A client's site (the fix generator's recommendations): whether to allow or block training bots is the site owner's decision (content-licensing position), not the skill's. Default recommendation there: allow the search bots explicitly, leave the training-bot choice to the owner.

## Mode B: the fix generator

`apps/api/src/avp_api/services/fix_generator.py`. Input `FixFacts` is facts-only: names, domains, counts, schema.org type names, booleans. Output `GeneratedFix`: `candidate_id`, `title` (<=200, an instruction naming the artefact), `detail` (<=600, only the scan's own numbers), `priority`, `effort` (S/M/L), and two reasons. The constraint lives in the type; keep it that way.

The model only words a closed candidate list from `build_candidates` (`fix_generator.py`, mirrored in `apps/web/src/lib/report/derive.ts`), and `BANNED_CLAIMS` is checked on the output. A new fix type needs a scoped change to `build_candidates` with `derive.ts` parity, never just prompt wording. Page signals (`schema_types`, `h1_count`, `word_count`, `indexable`) cover the home page only (one URL): never generalise them to "every page" or "site-wide".

How a fact becomes a structural fix (no page copy, no promised uplift):

| FixFacts field | Structural fix |
| --- | --- |
| `indexable` is false | Remove the `noindex` or blocking rule on the pages meant to be public. Blocker first. |
| `has_sitemap` is false | Add a sitemap covering the public surface and reference it in robots.txt. |
| `schema_types` lacks a type that matches existing content | Add that schema.org type (for example Organization, or FAQPage where the page already has visible Q&A). Never for content that is not present. |
| `h1_count` is 0 or above 1 | One `H1` per page reflecting the page's single topic. |
| `word_count` very low | Report the home page's word count as a fact and recommend answering the buyer question directly on it. Do not claim what an engine can extract (unverified); no target length: none is sourced. |
| `citations_to_subject` low, `top_cited_domains` populated | Call them "the domains engines cited most in this scan" (domain and count only) and recommend checking the brand's presence there. `FixFacts` holds only citations to the subject's own domain; presence elsewhere is never measured, so never assert absence. Do not describe or copy what those pages say. |
| `competitor_names` | Use as names for comparison only; never describe competitor claims. |
| robots rules for AI bots (not in FixFacts today) | Adding this needs a new boolean field and a scoped brief; do not infer it. |

Rules for the wording of `title` and `detail`:
- Cite this scan's own numbers (`answers_naming_subject` of `answers_analysed`), not literature figures. State no expected gain.
- Scores vary run to run and across engines; phrase as "this scan found", not "will improve".
- The shared "Never" rules above apply to every `title` and `detail`.
- Changing what FixFacts may hold, or the output schema, is a product and facts-only decision: stop and ask, and run `apps/api/tests/test_facts_only.py`.

## Reference files
- `references/geo-evidence.md`: the four sources, what each does and does not support.
- `references/technical-seo.md`: Next 15 technical checklist.
