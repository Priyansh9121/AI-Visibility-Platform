# Changes

Paths: E = ECC-main/skills/seo/SKILL.md. B = bencium-aeo/SKILL.md. P = prd.md. S = story-structured.md.

## Merge and scope
- Merged E and B into one AVP-specific skill with two modes (own site; fix generator) -> E has no GEO at all (whole file) and B has no AVP context; neither fits AVP's facts-only constraints.
- Description narrowed to AVP public pages and fix-generator GEO recommendations, excluding general copy -> B:3-4 and E:3 trigger on any SEO/AEO request.
- Dropped B "NOT for traditional SEO" (B:20) -> AVP needs both; the technical checklist is kept in references/technical-seo.md.

## Deleted statistics and unsourced claims
- Deleted "18-Token Extraction Rule" (B:28-32; P:13-17) -> unsourced figure presented as a research finding. No token or word length target remains.
- Deleted the example "reduces property analysis errors by 67%" (B:31; also P:17) -> invented number.
- Deleted "Citations + Statistics = 30-40% More Visibility" (B:37) -> unsourced; only the GEO paper's "up to 40% in its benchmark", conditional, appears (references/geo-evidence.md).
- Deleted "95% of AI citations come from content updated in last 10 months" (B:44) -> unsourced.
- Deleted "Princeton finding: Rank-5 sites gained 115% ... Rank-1 sites lost 30%" (B:53) -> unsourced and not in the cited paper summary in north-star §9.1.
- Deleted the authority-tier table of "5-7 extraction points" and weekly micro-updates (B:46-51; P:527-541) -> derived from the deleted figures, encourages over-optimisation.
- Deleted "Freshness: updated within 90 days", "FAQ answers 30-50 words", "15 FAQs", "questions 7-12 words" (B:65-69, B:111) -> unsourced heuristics. Freshness is now "date only what changed".
- Deleted "Reuters: $5M/year to Anthropic" and the "AI is desperate..." rationale (S:69-72) -> unsourced claim about third parties.
- Deleted the invented "94% accuracy rate (National Real Estate Technology Council, 2024)" and "10,000+ real estate professionals" sample copy (P:569-577) -> fabricated social proof.
- Replaced "Stat callouts everywhere" (P:532) with "only numbers the client supplies, with source and as-of date" (SKILL.md, Mode A) -> pressure to invent numbers.
- Dropped the 0-10 scoring rubric (B:102-113) -> scores built on the deleted metrics.
- Dropped ECC's "LCP < 2.5s, INP < 200ms, CLS < 0.1" and title/description lengths as stated rules (E:51-53, 68, 74) -> Core Web Vitals thresholds now cited to web.dev (see Sourced claims below); title/description lengths kept only as a labelled unverified heuristic in references/technical-seo.md.

## Stale or wrong schema advice
- Deleted "FAQPage JSON-LD (most important)" and "Validated with Google Rich Results Test" (B:81, B:123) -> stale rich-result framing. FAQPage/HowTo are now for machine readability only, and only where the visible page has the content.
- Dropped Product and Offer advice and `datePublished`/`dateModified` on every FAQ (E:60; B:68) -> would invite invented prices and blanket date stamping. JSON-LD limited to facts AVP can state; no ratings or reviews.
- Kept E:62 and E:146 ("schema only when content truly matches") as a rule.

## Added
- GEO section: consistent entity facts, answer-first passages with cited sources, llms.txt marked unproven -> the audit's spec; grounded in north-star §9.1 only.
- AI crawler table with vendor URLs, search-bot vs training-bot distinction, user-initiated fetch caveats, "re-verify before shipping" -> AVP facts fetched 2026-09-30.
- Confidence rules from Martinez (conditional 40%, low cross-engine overlap, run-to-run variability, rewrites can impair retrieval) -> keeps recommendations from over-promising.
- Kumar & Lakkaraju as defensive-only; explicit ban on hidden text, strategic text sequences, prompt injection -> manipulation risk.
- Mode B: FixFacts -> structural fix table, GeneratedFix limits -> ties advice to `fix_generator.py`, keeps output facts-only.
- Next 15 `generateMetadata`, `app/robots.ts`, `app/sitemap.ts` -> AVP has none yet; replaces the framework-agnostic advice and the `src/routes/products/[slug].tsx` example (E:134-137).
- Provider rule for any outbound validator host -> `providers.test.ts` and /privacy.

## Removed as irrelevant or unsafe
- B:136-140 pointers to prd.md and story-structured.md -> those files are not copied; only their structure informed the layout.
- B:125-134 testing against ChatGPT/Claude/Gemini by hand -> AVP's own scan measures this; manual prompting is not a reliable signal given run-to-run variability.
- E:150-155 Related Skills (`seo-specialist`, `frontend-patterns`, `brand-voice`, `market-research`) -> siblings that do not exist here; `tells` and `design:ux-copy` referenced instead.
- E keyword-mapping and cannibalisation section (E:84-90) -> not needed for AVP's four public pages.
- E frontmatter `metadata.origin` (E:4-5) -> dropped. No `allowed-tools`, hooks, installs or shell steps existed in either source.

## Sourced claims (added 2026-09-30, orchestrator-fetched)
- Core Web Vitals thresholds (LCP 2.5 s, INP 200 ms, CLS 0.1, 75th percentile, mobile and desktop) cited to https://web.dev/articles/vitals with near-verbatim quotes in references/technical-seo.md, replacing the "unverified" label (E:51-53).
- FAQPage/HowTo: cited to https://developers.google.com/search/docs/appearance/structured-data/faqpage (FAQ rich results only for well-known, authoritative government and health sites; HowTo rich results no longer shown). SKILL.md and references/technical-seo.md now say never to promise clients rich results from this markup (B:81, B:123).

## Security-review fixes (2026-09-30)
- M1: SKILL.md no longer says token routes are `noindex`; states they are not (nothing in apps/web sets it), names the `headers()` list in next.config.mjs and `X-Robots-Tag: noindex` as a founder-scoped fix, and notes Disallow blocks crawlers seeing noindex (unverified here) -> false claim about current state (B/E had no equivalent).
- M2: `top_cited_domains` row reworded to "domains engines cited most in this scan" and never asserts absence -> FixFacts does not measure presence elsewhere.
- M3: added a line that the model words a closed `build_candidates` list (mirrored in derive.ts), `BANNED_CLAIMS` applies, new fix types need a scoped change, and home-page signals must not be generalised site-wide -> prevents prompt-only fix types and overclaiming.
- M4: manipulation ban moved to the shared section for both modes, plus no cloaking / UA-specific content -> the manipulation and cloaking risk applies to Mode A as much as Mode B.
- L1: crawler bullet now per vendor: Claude-User honours robots.txt; only ChatGPT-User and Perplexity-User are exceptions -> earlier text lumped them.
- L2: "removes the site" marked as our summary, not a vendor quote; Google-Extended described as governing Gemini training and grounding -> avoids implying no visibility effect.
- L3: HowTo statement cited "per Google's FAQPage structured-data doc (checked 2026-09-30), which links the deprecation post" in SKILL.md and technical-seo.md.
- L4: "at most two hops" (E:39) labelled unverified heuristic.
- L5: "fixed context" restored; "gives an engine little to extract" replaced by a report-the-fact wording marked unverified.
- L6: description rephrased ("Use when doing") and scoring work excluded; "five public pages" corrected to four; ECC title-length cite corrected to E:74.
