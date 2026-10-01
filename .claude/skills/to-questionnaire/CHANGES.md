# Changes from upstream

- Description rewritten: third person, names AVP surfaces (/privacy, /terms, deletion retention), says when not to use it -> upstream's generic description (SKILL.md:3) would trigger on any unanswerable decision.
- Kept `disable-model-invocation: true` (SKILL.md:4) -> user-invoked only.
- Output location changed from "`to-questionnaire-<slug>.md` in the current directory" (SKILL.md:16) to chat, OS temp dir, or a founder-named path, never the repo by default -> removes the risk of dropping legal questions into the repo tree.
- Added a read-only "gather AVP facts" step with links to repo-relative paths and a do-not-read list for secrets -> questionnaire carries AVP facts; no secret reads.
- Added content rules: facts only, no legal conclusions, no rewriting legal pages, no secrets/emails/customer data, no third-party raw text, no unsourced numbers, product decisions left open for the founder (CLAUDE.md rules; AVP-FACTS house rules) -> upstream had no such limits (SKILL.md:7-16).
- Template example changed to a retention question -> AVP-relevant; structure otherwise kept (SKILL.md:22-54).
- Not copied: agents/openai.yaml (rules: no agent yaml).
- LICENSE is a `cp` of the repo-root MIT licence.
- Jurisdiction line no longer asserts a legal conclusion; it cites what PrivacyPolicy.tsx (~31, ~60) and TermsOfService.tsx (~98-101) say and asks the recipient; Spam Act dropped (not in repo) -> removes an unsourced legal claim (security review L1).
- Default output is chat; files go to a founder-named path outside the repo; repo paths get "do not stage or commit it"; no `${TMPDIR:-/tmp}` default -> shared temp dirs are world-readable (L2).
- Retention framed as the founder's decision with the expert bounding options; notes no deletion endpoint exists yet (N3).
- Fact links now point to PrivacyPolicy.tsx, TermsOfService.tsx, lib/legal/retention.ts (page.tsx are wrappers) (N4).
- Fields described from apps/api/src/avp_api/models/, never rows; no querying avp_dev or production (N5).
- Upstream URL added to SOURCE.md (N6).
- Cut upstream's tone sentence (SKILL.md:12 "This fixes the questionnaire's tone...") and "Done when the file exists..." (SKILL.md:16) -> replaced by AVP steps 4-5 (N6).
