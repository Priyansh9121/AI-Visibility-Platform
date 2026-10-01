# Technical SEO checklist, Next 15 App Router

Adapted from ECC seo. Conventional practice, not from the GEO papers; it does not itself support any AI-visibility claim. Core Web Vitals thresholds are sourced below; title and description lengths are unverified heuristics.

## Crawlability and indexability
- `app/robots.ts` allows public pages, blocks low-value and token routes. No public page unintentionally `noindex`.
- Public pages reachable within a shallow click depth; redirect chains of at most two hops (unverified heuristic).
- One canonical URL form; `alternates.canonical` self-consistent and non-looping.
- `app/sitemap.ts` lists exactly the intended public surface.
- Multilingual pages need hreflang only if used (AVP has none today).

## Metadata (`generateMetadata`)
- Unique title and description per page, honest to the page. Length guidance (titles about 50-60 characters, descriptions about 120-160) is an unverified heuristic.
- One `H1`; `H2`/`H3` follow the real content hierarchy.
- One primary intent per page; do not create thin near-duplicate pages.

## Structured data
- Only types that match visible content. Organization on the landing page; SoftwareApplication for the product; BreadcrumbList on interior pages if breadcrumbs exist; FAQPage/HowTo for machine readability only. Google's FAQ docs (https://developers.google.com/search/docs/appearance/structured-data/faqpage, checked 2026-09-30) say FAQ rich results are "only shown for well-known, authoritative government and health websites", and HowTo rich results are no longer shown in Google Search on desktop or mobile (per Google's FAQPage doc, checked 2026-09-30, which links the deprecation post). Never promise clients rich results from this markup.
- Never invent ratings, reviews or prices.
- Validate with a local JSON parse and the schema.org validator. A hosted validator is an outbound host: check `providers.test.ts` before adding one to any product feature.

## Performance
- Core Web Vitals (https://web.dev/articles/vitals, checked 2026-09-30): LCP "should occur within 2.5 seconds of when the page first starts loading"; "pages should have a INP of 200 milliseconds or less"; "pages should maintain a CLS of 0.1 or less". Measure at "the 75th percentile of page loads, segmented across mobile and desktop devices". Measure before changing.
- Use `next/image` and `next/font` as already configured; reserve layout space; trim client JS. Fonts come from the design system.

## Internal linking
- Descriptive anchor text; link new pages from relevant existing ones.

## Audit output shape
```text
[HIGH] Landing page has no Organization JSON-LD
Location: apps/web/src/app/layout.tsx
Issue: Entity facts exist only as visible text.
Fix: Add Organization JSON-LD with name, url and description only.
```
Tie every finding to a file. Read the real page before advising.
