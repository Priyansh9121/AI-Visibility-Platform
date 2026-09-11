# Competitor UI & feature audit — AI Clicks, PromptWatch, Searchable

**Status: founder-sourced, live-navigated, dated 2026-09-10.** This is the
research `north-star.md` §2.1 said was "still open": the founder's own
walkthroughs of each competitor's real product — own login, own browser,
screenshots timestamped 10 Sep 2026 — captured in three "A look at X" PDFs
(56 pages total: Searchable 29, PromptWatch 18, AI Clicks 9). It sits
alongside the web-sourced table in north-star §2.1; it does not replace it.
The two are different kinds of evidence.

**Verification:** the claims below were checked against the PDFs on
2026-09-10, and the claims about AVP's own state were checked against the
codebase the same day (see the last section). Corrections made during that
check are marked *(corrected)*.

**This is research, not a spec.** The no-competitor-reference rules in
north-star §1 apply in full: looking at a rendered competitor page for UX
research is fine, and that is all this document is. Nothing here is "build
this screen." The point is to know what exists and decide independently
what, if anything, AVP should do about it, derived from AVP's own data model.

---

## The one table worth having: engines, quotas, price

| | AI Clicks | PromptWatch | Searchable | AVP today |
|---|---|---|---|---|
| Engines shown in the model filter | ChatGPT, Perplexity, Gemini, AI Overviews | ChatGPT, AI Overview, AI Mode, Copilot, Alexa, Gemini, Perplexity | ChatGPT, Perplexity, AI Overview (trial's "included models") | Claude, OpenAI |
| What the quota counts | Prompts (110/150) and posts (2/15) | Responses (48 of 25K), "Upgrade to Scale" | Tracked prompts per plan | Scans per month (north-star §5) |
| Price | Not shown in the walkthrough | Not shown in the walkthrough | Launch $250/mo (200 prompts, 5 projects) · Growth $400/mo (500, 10, "Popular") · Enterprise $999/mo (1,250, unlimited) · 14-day trial | $29/mo flat, 3 seats |

Searchable prices by tracked-prompt volume, not seats. That is a completely
different model from AVP's flat $29, worth knowing precisely because it is so
far from AVP's position, not because it suggests AVP should move toward it.
The engine row is the gap the original draft missed: every competitor tracks
at least Google's AI surface and Perplexity; AVP tracks neither.

---

## AI Clicks — the leanest surface (9 pages)

**Structure:** flat single-level nav. Dashboard → Prompts (Analytics /
Discovery / Fanout Queries) → Sources → Competitors (Discovery / Brand
Rankings) → AI Traffic → Sentiment Analysis → Ads Intelligence → Actions
(Write Content / Get Mentioned / Engage Online / Outreach Campaigns).

**Notable features:**
- **Fanout Queries table** — the literal sub-queries an engine generates
  internally to answer a tracked prompt, shown as its own screen with
  frequency, model, and "triggered by" columns.
- **Outreach Campaigns** — finds AI-cited pages ("the top 3,000+ AI-cited
  pages in your industry", 917 eligible for this brand) and runs a *paid*
  link-placement campaign against them: minimum campaign budget $1,000,
  $250 per link (mention) achieved, unused budget refunded. This is a
  materially different business-model lever than anything AVP does: buying
  citations, not just measuring them.
- **Engage Online** — a queue of live Reddit threads (14 for this brand;
  LinkedIn, Quora, Facebook, X, Instagram tabs present but empty) relevant to
  tracked prompts, each with an AI-drafted reply the operator edits before
  posting.
- **Ads Intelligence** — tracks which brands run paid ads that appear inside
  AI answers (23 of 35 prompts carried ads; 39 unique advertisers), with an
  "Advertiser Leaderboard."
- **Competitor Discovery** ranks 434 candidate brands by prompts mentioned,
  each with a one-click Track.

**UI:** light theme throughout, dense data tables, minimal visual identity.
Functional rather than designed. No dark mode, no strong brand colour beyond
a blue accent.

---

## PromptWatch — the widest feature surface (18 pages)

**Structure:** grouped nav. Monitoring (Monitors, Prompts, Page Tracker,
Sentiment, Shopping, Ads Radar) / Sources (Citations, Socials, Offsite
Mentions) / Content Agent (Create Content, Optimize Content, Knowledge Base,
Content Gap) / Agent Analytics (Crawler Logs, Visitor Analytics, 360
Insights) / Brand Hub (Brand Book, Competitors, Sitemap). A large fraction of
this surface (Shopping, all of Content Agent, Knowledge Base, Visitor
Analytics, Crawler Logs, 360 Insights) is **upsell-gated**: visibly present in
the nav but blocked behind "Contact Sales" on the agency plan, rather than
simply absent.

**Onboarding:** analyses the website first, then pre-fills brand aliases
("Complete Implant & Sedation Dentistry", "Complete Dental Implants Perth")
and a short brand description the operator can edit. Competitors are entered
as name plus URL, "at least 5" recommended.

**Notable features:**
- **Agent Chat** — a full conversational assistant with prebuilt "Agent
  Skills" cards: 7-day GEO briefing *(corrected from "T-day")*, Diagnose
  visibility, Search Console overlap, Newsjacking, YouTube influencer
  outreach, Top Reddit comments. A chat-first way to ask ad hoc competitive
  questions, a genuinely different interaction model from a fixed dashboard.
- **Monitors as saved cards** — each shows three mini sparklines side by side
  (visibility, sentiment, citation rank) plus the tracked models, so a list
  of monitors reads as a scannable grid rather than a table.
- **Sentiment-by-competitor heatmap** — a table where green cell intensity
  encodes sentiment score per engine per competitor, all in one glance.
  Sentiment's 0-100 score is also rendered as a marker on a horizontal
  red-to-green gradient track.
- **Actions as a kanban board** (To Do / In Progress / Done) for AI-suggested
  setup tasks: "Connect Google Search Console," "Set up crawler logs," etc.
- **Prompt Types taxonomy**: Organic / Brand Specific / Competitor
  Comparison, filterable at dashboard level. The walkthrough shows visibility
  moving from 19.4% (all types) to 35.2% (competitor comparison only) to
  46.7% (comparison plus brand-specific). Conceptually close to AVP's own
  awareness/comparison/bottom-funnel intent tags, independently arrived at.
- **Citations split three ways** — All Citations (domains), URL Positions
  (rank of each cited URL inside the answer), Citation Trends (citation rate
  by type and by domain rank).
- **Export Dashboard Report modal** — global filters, chart-type choice,
  "email me the PDF once generated." A lightweight scheduled-report flow
  distinct from a fixed report screen.

**UI:** light theme, blue accent, information-dense but organised.

---

## Searchable — most deliberate onboarding, highest price point (29 pages)

**Onboarding, more structured than the other two:**
- Brand-vs-agency split at signup; an agency then names its own workspace
  before adding its first client.
- A "client reach" question (Global / Primary market + international /
  Nationwide / Regional / Local) plus country and language pickers. Prompt
  generation is locality-aware in a way AVP's currently isn't.
- Competitor selection is semi-automated: AI suggests candidates ("Filtering
  relevant brands" loading state), operator confirms or edits before
  continuing. 3 required, up to 5.
- **A prompt review-and-approval step before the first scan runs** — "Review
  Prompts... Looks Good": 50 baseline prompts grouped by topic, edited or
  approved before anything executes. AVP currently generates and runs
  without this checkpoint.
- Loading states use a small animated grid of coloured squares rather than a
  spinner. A detail, but a deliberate one.

**The most conceptually distinct feature: Query Fanout, exposed directly.**
Clicking into any tracked prompt shows the literal sub-queries the model asks
itself to construct its answer, and there is a dedicated Query Fanout tab at
the account level aggregating this across all prompts (49 fanouts for this
client). The onboarding video, fully transcribed in the source PDF, frames
this as the single most important thing to understand if you want to
"perform well in a year." AVP has no equivalent: it treats each prompt as a
black box that produces an answer, not as a tree of sub-queries worth
understanding on its own.

**Other things AVP has no equivalent for:**
- **Geography as a first-class dimension** — a literal map (country or
  region shaded by visibility %) with drill-down to city level ("Melbourne,
  Australia" as a location filter). AVP has no geographic dimension at all.
- **A real crawler-verification mechanism** — a WordPress must-use plugin
  (and other platform integrations, "11 supported, 2 coming soon"
  *(corrected from 9; read from the screenshot)*, Nginx and Webflow among the
  coming ones) that captures actual GPTBot/ClaudeBot/PerplexityBot requests
  server-side at the origin, keyed by an API key and site token. This is
  categorically different from AVP's `robots.txt` permission check:
  Searchable confirms *observed crawl events*, not just stated permission.
- **A block-based, AI-assisted report canvas** — not a fixed template.
  Reports are assembled from named data blocks dragged into a Notion-style
  canvas. The PDF lists 18 block categories totalling 76 blocks (Visibility,
  Mentions & Citations, Sentiment, Sources, Topics, Query Fan-out, Location,
  four Shopping groups, four AI Traffic groups, Search Console, Google
  Analytics, AI Traffic Logs); the UI copy says "40+ data nodes, charts, and
  timeframes" *(corrected from "40+ blocks across ~12 categories")*. Three
  starter templates (Executive AI Visibility, AI Traffic & Referrals, Organic
  Search Performance). A right-hand AI chat panel can rewrite or restructure
  the report ("tighten the structure," "prune weak sections"). The generated
  executive summary reads as boilerplate: "visibility was broadly flat versus
  the comparison period" on seven days of data. This is a fundamentally
  different model from AVP's fixed five-beat narrative (score → gap → proof →
  fix → pitch): flexible assembly vs. an opinionated, unchangeable argument.
- **Prompt lifecycle states** — Active (50) / Proposed (160) / Archived, with
  bulk upload and a "Generate Prompts & Topics" action. Distinct from AVP's
  one-shot generation.
- **An agency "pitch" sandbox** — a separate allocation (500 prompts across
  10 workspaces, per the video) explicitly for prospecting a client who
  hasn't signed yet, kept apart from live client tracking budgets.
- **Site Health framed as two sub-scores** (Technical and "AEO") per page,
  benchmarked against "top X% of sites," with a **personalised "quick wins"
  triage** ahead of the full issue list ("Welcome back, Priyansh — tackling
  these 3 quick wins will clean up your search appearance immediately"). AVP's
  Technical screen lists every finding at once with no such front-loaded
  prioritisation.
- **Simulated competitors** — a "Simulate" action beside a brand in the
  rankings table, and the video's "I can simulate a competitor and see how
  they're performing": modelling a hypothetical rival rather than only
  tracking real, currently-active ones.
- **A "remove my brand" toggle** for unbranded visibility, and a
  branded-vs-unbranded filter at dashboard level.

**UI:** light theme, orange/black accent, isometric empty-state
illustrations (Search Console connection, Opportunities screen). The most
visually considered of the three, though still conventional SaaS dashboard
styling. Nothing AVP's own identity work should feel pressure from.

---

## Cross-cutting takeaways

**Where AVP already has an answer these three arrived at independently:**
funnel-stage prompt tagging (Searchable's "decision journey," PromptWatch's
Prompt Types) validates AVP's own awareness/comparison/bottom-funnel scoring
design rather than suggesting AVP change it.

**Where AVP has a real, nameable gap, in rough order of how foundational it
is:**
1. Engine coverage: two engines, and neither Google's AI surface nor
   Perplexity, which all three competitors track.
2. No geographic dimension at all (Searchable's is the deepest).
3. No verified crawler-traffic mechanism, only permission-checking
   (Searchable's origin-side plugin is real signal, not stated intent).
4. No pre-scan prompt review/approval step (Searchable).
5. Query Fanout has no analog anywhere in AVP (Searchable, and AI Clicks'
   simpler version).
6. No "quick wins" triage ahead of a full technical findings list
   (Searchable).

**Where AVP's current design is a considered, different choice, not an
omission:** the fixed five-beat narrative report vs. Searchable's block-based
free-form canvas is the clearest case. AVP's own documentation argues
explicitly for a narrative over a metrics grid, and Searchable's approach is
the metrics-grid-with-AI-assist alternative. Its auto-written summary on thin
data is a small piece of evidence for AVP's side of that argument. Worth
knowing it exists; not evidence AVP's choice is wrong.

None of the above is a recommendation to build any of it. It is the "know
what's out there" half of the research. Whether any of it is worth doing,
and how AVP would derive its own version from its own data model rather than
copying the mechanism, is a separate decision for whenever that is on the
table.

---

## How the AVP-side claims were checked (2026-09-10)

- **No geographic dimension:** every `country|region|locale|city` hit in
  `apps/api/src`, `apps/web/src`, and `packages/*/src` is an ARIA
  `role="region"`, a date-formatting comment, or a test fixture.
- **No prompt approval step:** `models/prompt.py` carries no status,
  approved, or archived field, and no route or component matches
  `approve|review prompt|proposed`.
- **Two engines:** `services/engines.py` defines a Claude answer model and an
  OpenAI answer model and nothing else.
- **No quick-wins triage:** `ClientTechnicalView.tsx` has no prioritised
  subset ahead of the findings list; `fix_runner` ranks fix candidates, which
  is the report's concern, not the Technical screen's.
- **$29 flat:** north-star §5, "One plan, $29/month, 3 seats."
