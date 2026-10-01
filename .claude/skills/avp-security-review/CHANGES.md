# Changes from ECC security-review (source paths relative to ECC-main/skills/security-review/)

- Renamed `security-review` -> `avp-security-review`; description rewritten to say when to use it versus the built-in -> removes shadowing of the built-in (SKILL.md:2-3).
- Replaced the TypeScript/Express/zod/Next examples with FastAPI, Pydantic v2 and async SQLAlchemy 2 -> source stack is not AVP's (SKILL.md:27-133, :285 `import rateLimit from 'express-rate-limit'`).
- Deleted "### 9. Blockchain Security (Solana)" and the "Wallet Signatures" checklist line -> off-domain (SKILL.md:354, :500).
- Inverted the logging example: source showed `console.log('User login:', { email, userId })` as correct; the skill now says log ids, never emails -> contradicts CLAUDE.md "no email in a log" (SKILL.md:324).
- Removed Supabase RLS example and "Row Level Security: Enabled in Supabase" line; replaced with agency-scoped queries and a pointer to `supabase-postgres-best-practices` -> AVP's API enforces tenancy in code (SKILL.md:179-191, :497).
- Hosting: "Vercel, Railway" replaced by Render (and the note that Render auto-deploys main) -> wrong host (SKILL.md:48).
- Dropped `cloud-infrastructure-security.md` entirely: AWS IAM/S3/Terraform/Cloudflare, `aws` CLI and MFA commands, none applicable to a Render deployment -> avoids off-stack and credential-handling instructions (cloud-infrastructure-security.md:1-361).
- Dropped the Jest-style rate-limit test, file-upload validator, JWT-in-localStorage and npm audit sections; replaced with AVP gates (floors.json / assert_count.py, unpiped logs) -> no coverage targets, no npm, rule 1 of infra/ci/README.md.
- Added: Stripe webhook signature and idempotency (section 7), SSRF for crawler-supplied URLs (section 8), web header list for next.config.mjs (section 9), token routes (section 10) -> requested by the audit.
- Added checklist items from AVP's reviews of the week to 2026-10-01: tenancy via `assert_own_agency`, rate limits before expensive verification, session revocation before and after password change, Redis errors -> 503 with no URL in logs, tokens in fragments, noindex + no-referrer on token routes.
- Removed the source's `metadata.origin` frontmatter and the HTML comment about placeholders (SKILL.md:4-6, :136-139) -> not needed; no dollar-sign placeholders used.
- Added honest "state as of 2026-10-01" notes: no HSTS/CSP/nosniff/frame-options found in the repo, and no public-IP guard found in crawl.py. These are flagged as findings, not passes.
- All shell snippets set `LOG="${LOG:?...}"`, redirect, read exit codes, never pipe.

## Review fixes (2026-10-01, re-verified read-only against the repo)
- H1 (SKILL.md section 10): removed "existing path tokens"; share/invite/reset are fragments since d2fe29a (links.py:14-28), redeemed by POST; residual is a legacy path link in the web host's request log until TTL -> was false.
- H2 (section 8): named the four paths an IP check in the httpx loop misses (crawl.py:183, :488, :408-411, :426; technical_audit.py:384-395, :403), IPv6, rebinding, `--no-sandbox` (crawl.py:398, technical_audit.py:331); recommended a network-level egress proxy as a founder/infra decision; reworded the per-hop item -> removed a false-green fix.
- M3 (section 5, checklist): reset-request fails open by design (rate_limit.py:22-24, :212-215) -> "never open" was wrong.
- M4 (section 4): added "whose IP is trusted?" (docker-entrypoint.sh:18, config.py:133-144, runbook.md:151).
- M5 (section 4): replaced the generic CSRF line with the real cookie facts (config.py:96,98,345; auth.py:66; deps.py:115), no Origin check, open item on production SameSite.
- L6 (section 10): dropped "unverified"; ticket read from JSON body of /auth/google/pending and /complete (auth.py:626,636-678).
- L7 (section 8): removed serp.py (fixed SerpApi URL, serp.py:34).
- L8 (section 8): removed "robots.txt on every hop"; the robots.txt fetch itself follows redirects unchecked (crawl.py:183).
- Re-review nits: XFF leftmost entry "may be client-controlled" pending the runbook probe (runbook.md:118-160); `--no-sandbox` means renderer exploit = code execution in the API container (crawl.py:398); ticket never accepted in a query by the API, web legacy `?ticket=` read is cleared (googleTicket.ts:40-44); 503 is `/problems/service-unavailable` (errors.py:262), `auth.rate_limit.unavailable` is the log event (rate_limit.py:213).
